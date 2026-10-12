import logging
import requests
import json
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.circuit_breaker import ollama_circuit, CircuitBreakerOpen

logger = logging.getLogger(__name__)


def _name_similarity(a: str, b: str) -> float:
    """Heuristic name-similarity score (0-100) using token overlap + edit distance."""
    a_lower = a.lower().replace("_", " ").replace("-", " ")
    b_lower = b.lower().replace("_", " ").replace("-", " ")
    a_tokens = set(a_lower.split())
    b_tokens = set(b_lower.split())
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    jaccard = len(intersection) / len(a_tokens | b_tokens) if (a_tokens | b_tokens) else 0.0
    # Exact match = 100, high token overlap = 70-99, partial = 30-69
    if a_lower == b_lower:
        return 100.0
    if jaccard >= 0.5:
        return 70.0 + (jaccard * 30.0)
    if jaccard > 0:
        return 30.0 + (jaccard * 40.0)
    return 0.0


def _type_compatibility(src_type: str, tgt_type: str) -> float:
    """Heuristic type compatibility (0-100)."""
    src = src_type.lower() if src_type else ""
    tgt = tgt_type.lower() if tgt_type else ""
    if not src or not tgt:
        return 50.0  # unknown = neutral
    # Exact type match
    if src == tgt:
        return 100.0
    # Numeric families
    numeric = {"int", "integer", "bigint", "smallint", "tinyint", "float", "double", "decimal", "number", "numeric", "real"}
    text = {"varchar", "char", "text", "string", "nvarchar", "nchar", "clob"}
    temporal = {"date", "datetime", "timestamp", "time", "year"}
    if src in numeric and tgt in numeric:
        return 90.0
    if src in text and tgt in text:
        return 90.0
    if src in temporal and tgt in temporal:
        return 90.0
    # Cross-family (e.g. string → numeric) = low
    return 20.0


class AIService:
    @staticmethod
    def get_ollama_url() -> str:
        return f"{settings.OLLAMA_HOST}/api/generate"

    @staticmethod
    def _normalize_confidence(conf: Any) -> float:
        if conf is None:
            return 0.0
        if isinstance(conf, (int, float)) and conf <= 1.0:
            return float(conf) * 100.0
        return float(conf)

    @staticmethod
    def _compute_components(source_col: Dict[str, Any], target_col: Dict[str, Any]) -> Dict[str, float]:
        """Compute similarity components for a source/target column pair (E04-1)."""
        src_name = source_col.get("name", "")
        tgt_name = target_col.get("name", "")
        src_type = source_col.get("type", "")
        tgt_type = target_col.get("type", "")
        return {
            "name_similarity": round(_name_similarity(src_name, tgt_name), 1),
            "type_compatibility": round(_type_compatibility(src_type, tgt_type), 1),
            "value_pattern": 0.0,  # placeholder — needs value-level analysis
            "semantic": 0.0,       # populated by LLM when available
        }

    @staticmethod
    def _build_reason(components: Dict[str, float], llm_reason: Optional[str] = None) -> str:
        """Build a human-readable reason string from components (E04-1)."""
        if llm_reason:
            return llm_reason
        parts = []
        if components.get("name_similarity", 0) >= 70:
            parts.append("similar column names")
        elif components.get("name_similarity", 0) >= 30:
            parts.append("partially matching names")
        if components.get("type_compatibility", 0) >= 80:
            parts.append("compatible data types")
        if components.get("value_pattern", 0) >= 70:
            parts.append("matching value patterns")
        if not parts:
            return "Low similarity — review manually"
        return "Match based on " + " and ".join(parts)

    @staticmethod
    def _match_schemas_rule_based(
        source_name: str,
        source_schema: List[Dict[str, Any]],
        target_name: str,
        target_schema: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return deterministic schema suggestions without an external call."""
        matches = []
        for src in source_schema:
            src_name = src.get("name", "")
            best_match = None
            score = 0
            best_components = None
            for tgt in target_schema:
                tgt_name = tgt.get("name", "")
                components = AIService._compute_components(src, tgt)
                composite = (
                    components["name_similarity"] * 0.7
                    + components["type_compatibility"] * 0.3
                )
                if composite > score:
                    score = composite
                    best_match = tgt_name
                    best_components = components
            if best_match and score >= 20:
                matches.append({
                    "source": src_name,
                    "target": best_match,
                    "confidence": round(score, 1),
                    "reason": AIService._build_reason(best_components),
                    "components": best_components,
                    "ai_processed": False,
                })

        return {
            "matches": matches,
            "source": source_name,
            "target": target_name,
            "ai_processed": False,
        }

    @staticmethod
    def match_schemas(
        source_name: str,
        source_schema: List[Dict[str, Any]],
        target_name: str,
        target_schema: List[Dict[str, Any]],
        allow_llm: bool = True,
    ) -> Dict[str, Any]:
        """
        Calls local Ollama to find semantic matches between source and target column fields.
        Returns per-match reason + similarity component breakdown (E04-1).
        Falls back to rule-based matching with heuristic components when Ollama is unavailable.
        """
        source_cols = [c["name"] for c in source_schema]
        target_cols = [c["name"] for c in target_schema]

        prompt = f"""
        You are an AI Database mapping agent.
        Match columns from the Source Table '{source_name}' to the Target Table '{target_name}'.
        
        Source Columns: {source_cols}
        Target Columns: {target_cols}

        For each match, provide a confidence score (0.0-1.0), a plain-language reason,
        and a semantic similarity score (0.0-1.0).

        Output a JSON object ONLY in this format:
        {{
          "matches": [
             {{
               "source": "src_col_name",
               "target": "target_col_name",
               "confidence": 0.95,
               "reason": "Semantic and pattern similarity — both columns store customer identifiers",
               "semantic_score": 0.92
             }}
          ]
        }}
        """

        if not allow_llm:
            logger.info("Using rule-based schema matching without an external LLM call")
            return AIService._match_schemas_rule_based(
                source_name, source_schema, target_name, target_schema,
            )

        import time as _time
        from app.services.databricks_llm_provider import get_databricks_llm_provider

        for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
            try:
                parsed = None
                try:
                    llm_provider = get_databricks_llm_provider()
                    response = llm_provider.generate(prompt=prompt, stream=False)
                    text_resp = response.get("response", "{}")
                    if "```json" in text_resp:
                        text_resp = text_resp.split("```json")[1].split("```")[0]
                    elif "```" in text_resp:
                        text_resp = text_resp.split("```")[1].split("```")[0]
                    parsed = json.loads(text_resp.strip())
                except ValueError:
                    # Fallback to Ollama if Databricks LLM not configured
                    def _post():
                        return requests.post(
                            AIService.get_ollama_url(),
                            json={
                                "model": settings.OLLAMA_MODEL,
                                "prompt": prompt,
                                "stream": False,
                                "format": "json",
                            },
                            timeout=settings.OLLAMA_TIMEOUT,
                        )
                    response = ollama_circuit.call(_post)
                    if response.status_code == 200:
                        result = response.json()
                        parsed = json.loads(result.get("response", "{}"))
                    else:
                        logger.warning("Ollama returned status %s on attempt %d", response.status_code, attempt + 1)
                        raise Exception(f"Ollama HTTP {response.status_code}")

                if parsed is not None:
                    normalized_matches = []
                    source_names = {c.get("name") for c in source_schema}
                    target_names = {c.get("name") for c in target_schema}
                    for match in parsed.get("matches", []) or []:
                        if match.get("source") not in source_names or match.get("target") not in target_names:
                            logger.warning("Discarding ungrounded AI schema match: %r", match)
                            continue
                        src_col = next((c for c in source_schema if c["name"] == match.get("source")), {})
                        tgt_col = next((c for c in target_schema if c["name"] == match.get("target")), {})
                        components = AIService._compute_components(src_col, tgt_col)
                        # Override semantic with LLM's score if provided
                        if match.get("semantic_score") is not None:
                            components["semantic"] = round(AIService._normalize_confidence(match["semantic_score"]), 1)
                        normalized_matches.append({
                            "source": match.get("source", ""),
                            "target": match.get("target", ""),
                            "confidence": AIService._normalize_confidence(match.get("confidence", 0)),
                            "reason": AIService._build_reason(components, match.get("reason")),
                            "components": components,
                            "ai_processed": True,
                        })
                    parsed["matches"] = normalized_matches
                    parsed["ai_processed"] = True
                    return parsed
                    
            except CircuitBreakerOpen as e:
                logger.warning("Ollama circuit open, skipping retries: %s", e)
                break
            except Exception as e:
                logger.warning("LLM call failed (attempt %d/%d): %s", attempt + 1, settings.OLLAMA_MAX_RETRIES + 1, e)
                if attempt < settings.OLLAMA_MAX_RETRIES:
                    _time.sleep(2 ** attempt)

        logger.info("Falling back to rule-based matching (E04-8 graceful degradation)")

        return AIService._match_schemas_rule_based(
            source_name, source_schema, target_name, target_schema,
        )
