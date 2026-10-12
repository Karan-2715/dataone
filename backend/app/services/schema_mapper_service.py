"""
Schema Mapper Service.

Parses English-language mapping instructions and generates migration SQL.
Provides visual mapping data structures for the frontend mapper UI.
"""

import logging
import re
import json
import requests
from typing import List, Dict, Any, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class SchemaMapperService:
    """Translates English or visual mappings into executable SQL transformations."""

    # ── English → Mapping Rules ───────────────────────────────

    @staticmethod
    def parse_english_mapping(
        text: str,
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_schema: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        Parse natural language mapping instructions into structured rules.
        E.g.: 'Map email_address to contact_email' → {source: 'email_address', target: 'contact_email', transform: 'direct'}
        """
        rules = []
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]

        # Flatten schema columns for lookup
        all_source_cols = {}
        for table, cols in source_schema.items():
            for c in cols:
                all_source_cols[c["name"].lower()] = {"table": table, **c}

        all_target_cols = {}
        for table, cols in target_schema.items():
            for c in cols:
                all_target_cols[c["name"].lower()] = {"table": table, **c}

        for line in lines:
            rule = SchemaMapperService._parse_single_instruction(
                line, all_source_cols, all_target_cols
            )
            if rule:
                rules.append(rule)

        # If no rules parsed from line-by-line, try LLM
        if not rules:
            rules = SchemaMapperService._try_llm_parse(text, source_schema, target_schema)

        return {
            "rules": rules,
            "total_rules": len(rules),
            "source_tables": list(source_schema.keys()),
            "target_tables": list(target_schema.keys()),
        }

    @staticmethod
    def _parse_single_instruction(
        line: str,
        source_cols: Dict,
        target_cols: Dict,
    ) -> Optional[Dict[str, Any]]:
        """Parse a single mapping instruction line."""
        lower = line.lower()

        # Pattern: "map X to Y"
        map_match = re.match(
            r"(?:map|link|connect|assign)\s+(\w+)\s+(?:to|->|→|=>)\s+(\w+)",
            lower,
        )
        if map_match:
            src, tgt = map_match.group(1), map_match.group(2)
            src_info = source_cols.get(src, {})
            tgt_info = target_cols.get(tgt, {})
            transform = "direct"
            if src_info.get("type", "").lower() != tgt_info.get("type", "").lower():
                transform = f"cast({src_info.get('type', '?')} → {tgt_info.get('type', '?')})"
            return {
                "source_column": src,
                "source_table": src_info.get("table", "?"),
                "target_column": tgt,
                "target_table": tgt_info.get("table", "?"),
                "transform": transform,
                "confidence": 95 if src in source_cols and tgt in target_cols else 60,
            }

        # Pattern: "rename X as Y" or "rename X to Y"
        rename_match = re.match(
            r"rename\s+(\w+)\s+(?:as|to)\s+(\w+)", lower
        )
        if rename_match:
            src, tgt = rename_match.group(1), rename_match.group(2)
            return {
                "source_column": src,
                "source_table": source_cols.get(src, {}).get("table", "?"),
                "target_column": tgt,
                "target_table": source_cols.get(src, {}).get("table", "?"),
                "transform": "rename",
                "confidence": 90,
            }

        # Pattern: "add column X as TYPE"
        add_match = re.match(
            r"add\s+(?:column\s+)?(\w+)\s+(?:as|type)\s+(\w+(?:\(\d+\))?)",
            lower,
        )
        if add_match:
            col_name, col_type = add_match.group(1), add_match.group(2)
            return {
                "action": "add_column",
                "column": col_name,
                "type": col_type.upper(),
                "transform": "add",
                "confidence": 90,
            }

        # Pattern: "change type of X to Y"
        type_match = re.match(
            r"(?:change|alter|set)\s+(?:type\s+(?:of\s+)?)?(\w+)\s+(?:to|as)\s+(\w+(?:\(\d+\))?)",
            lower,
        )
        if type_match:
            col, new_type = type_match.group(1), type_match.group(2)
            return {
                "action": "change_type",
                "column": col,
                "new_type": new_type.upper(),
                "transform": "alter_type",
                "confidence": 85,
            }

        return None

    @staticmethod
    def _try_llm_parse(
        text: str,
        source_schema: Dict,
        target_schema: Dict,
    ) -> List[Dict[str, Any]]:
        """Use Ollama to parse complex mapping instructions."""
        prompt = f"""You are a database schema mapping expert. Parse the following instructions into structured mapping rules.

SOURCE SCHEMA: {json.dumps({t: [c["name"] for c in cols] for t, cols in source_schema.items()})}
TARGET SCHEMA: {json.dumps({t: [c["name"] for c in cols] for t, cols in target_schema.items()})}

INSTRUCTIONS: {text}

Return a JSON array of rules:
[{{"source_column": "...", "source_table": "...", "target_column": "...", "target_table": "...", "transform": "direct|cast|rename", "confidence": 90}}]

JSON:"""

        import time
        from app.services.databricks_llm_provider import get_databricks_llm_provider

        for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
            try:
                result = None
                try:
                    llm_provider = get_databricks_llm_provider()
                    response = llm_provider.generate(prompt=prompt, stream=False)
                    text_resp = response.get("response", "[]")
                    if "```json" in text_resp:
                        text_resp = text_resp.split("```json")[1].split("```")[0]
                    elif "```" in text_resp:
                        text_resp = text_resp.split("```")[1].split("```")[0]
                    result = json.loads(text_resp.strip())
                except ValueError:
                    resp = requests.post(
                        f"{settings.OLLAMA_HOST}/api/generate",
                        json={"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
                        timeout=settings.OLLAMA_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        result = json.loads(resp.json().get("response", "[]"))
                    else:
                        logger.warning("SchemaMapper Ollama returned status %s on attempt %d", resp.status_code, attempt + 1)
                        raise Exception(f"Ollama HTTP {resp.status_code}")

                if result is not None:
                    if isinstance(result, list):
                        return result
                    if isinstance(result, dict) and "rules" in result:
                        return result["rules"]
            except Exception as e:
                logger.warning("SchemaMapper LLM call failed (attempt %d/%d): %s", attempt + 1, settings.OLLAMA_MAX_RETRIES + 1, e)
                if attempt < settings.OLLAMA_MAX_RETRIES:
                    time.sleep(2 ** attempt)
        logger.info("SchemaMapper falling back to rule-based parsing")
        return []

    # ── SQL Generation ────────────────────────────────────────

    @staticmethod
    def generate_migration_sql(
        mappings: List[Dict[str, Any]],
        target_db_type: str = "sqlite",
    ) -> Dict[str, Any]:
        """Generate DDL/DML SQL from mapping rules."""
        ddl_statements = []
        dml_statements = []
        warnings = []

        for rule in mappings:
            action = rule.get("action", "map")

            if action == "add_column":
                col = rule["column"]
                col_type = rule.get("type", "TEXT")
                table = rule.get("target_table", rule.get("source_table", "?"))
                ddl_statements.append(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")

            elif action == "change_type":
                col = rule["column"]
                new_type = rule.get("new_type", "TEXT")
                table = rule.get("target_table", rule.get("source_table", "?"))
                if target_db_type == "sqlite":
                    warnings.append(f"SQLite doesn't support ALTER COLUMN for {col}. Consider recreating the table.")
                    ddl_statements.append(f"-- ALTER TABLE {table} ALTER COLUMN {col} TYPE {new_type}; (unsupported in SQLite)")
                else:
                    ddl_statements.append(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {new_type};")

            else:
                # Column mapping → INSERT...SELECT statement
                src_table = rule.get("source_table", "?")
                src_col = rule.get("source_column", "?")
                tgt_table = rule.get("target_table", "?")
                tgt_col = rule.get("target_column", "?")
                transform = rule.get("transform", "direct")

                if "cast" in transform.lower():
                    dml_statements.append(
                        f"-- Map {src_table}.{src_col} → {tgt_table}.{tgt_col} (with type cast)"
                    )
                else:
                    dml_statements.append(
                        f"-- Map {src_table}.{src_col} → {tgt_table}.{tgt_col} (direct)"
                    )

        # Build composite INSERT...SELECT if we have column mappings
        map_rules = [r for r in mappings if r.get("action", "map") == "map" or "source_column" in r]
        if map_rules:
            src_tables = set(r.get("source_table", "?") for r in map_rules)
            tgt_tables = set(r.get("target_table", "?") for r in map_rules)
            for tgt_t in tgt_tables:
                tgt_cols = [r["target_column"] for r in map_rules if r.get("target_table") == tgt_t]
                src_cols = [r["source_column"] for r in map_rules if r.get("target_table") == tgt_t]
                src_t = next((r["source_table"] for r in map_rules if r.get("target_table") == tgt_t), "?")
                if tgt_cols and src_cols:
                    insert_sql = (
                        f"INSERT INTO {tgt_t} ({', '.join(tgt_cols)})\n"
                        f"SELECT {', '.join(src_cols)}\n"
                        f"FROM {src_t};"
                    )
                    dml_statements.append(insert_sql)

        return {
            "ddl": ddl_statements,
            "dml": dml_statements,
            "warnings": warnings,
            "total_statements": len(ddl_statements) + len(dml_statements),
        }

    @staticmethod
    def get_visual_mapping_data(
        source_schema: Dict[str, List[Dict[str, Any]]],
        target_schema: Dict[str, List[Dict[str, Any]]],
        ai_matches: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate structured data for the visual schema mapper UI."""
        source_nodes = []
        target_nodes = []
        edges = []

        y_offset = 0
        for table, cols in source_schema.items():
            for col in cols:
                node_id = f"src_{table}_{col['name']}"
                source_nodes.append({
                    "id": node_id,
                    "table": table,
                    "column": col["name"],
                    "type": col.get("type", "?"),
                    "primary_key": col.get("primary_key", False),
                    "y": y_offset,
                })
                y_offset += 40
            y_offset += 20

        y_offset = 0
        for table, cols in target_schema.items():
            for col in cols:
                node_id = f"tgt_{table}_{col['name']}"
                target_nodes.append({
                    "id": node_id,
                    "table": table,
                    "column": col["name"],
                    "type": col.get("type", "?"),
                    "primary_key": col.get("primary_key", False),
                    "y": y_offset,
                })
                y_offset += 40
            y_offset += 20

        # Add AI match edges
        if ai_matches:
            for match in ai_matches:
                src_id = None
                tgt_id = None
                for sn in source_nodes:
                    if sn["column"] == match.get("source"):
                        src_id = sn["id"]
                        break
                for tn in target_nodes:
                    if tn["column"] == match.get("target"):
                        tgt_id = tn["id"]
                        break
                if src_id and tgt_id:
                    edges.append({
                        "source": src_id,
                        "target": tgt_id,
                        "confidence": match.get("confidence", 0),
                        "ai_suggested": True,
                    })

        return {
            "source_nodes": source_nodes,
            "target_nodes": target_nodes,
            "edges": edges,
        }
