"""Agentic DBA planning engine (agentic_dba_tasks #3, #9-collision half).

Produces SchemaDesignPlan artifacts for schema_design-classified requests:
deterministic-first (template library + catalog-driven fallback), optionally
LLM-adapted, always grounded in Schema Intel metadata/profiling — NEVER
row-level data content (same prompt-injection-safety principle Autopilot's
engine enforces).

Nothing here executes anything: plans are artifacts for human review.
Execution lives in agentic_dba_execution_service (task #7) behind the
existing Query Studio write gate.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy.orm import Session, joinedload

from app.core.circuit_breaker import CircuitBreakerOpen, ollama_circuit
from app.core.config import settings
from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogColumn, CatalogTable
from app.models.schema_design_plan import SchemaDesignPlan
from app.services.audit_helper import emit_audit_event
from app.services.dq_rule_proposer import propose_dq_rules
from app.services.transformation_proposer import propose_transformations

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A SQL type token: a type name (optionally multi-word like DOUBLE PRECISION)
# with an optional precision/scale. Anchors the LLM-supplied `type` to the
# same grounding discipline as identifiers, so a crafted value can't flow
# verbatim into generated DDL (defense in depth — the write path also rejects
# multi-statement SQL, but an ungrounded type must fail validation, not apply).
_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?$")

# ── Domain template library (design decision #11: deterministic first) ────
# A template is a *starting draft* adapted to the discovered catalog, not a
# rigid output. Start with one recognizable domain; extending = adding an
# entry here, not rearchitecting.

DOMAIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "name": "retail_analytics",
        "match": re.compile(r"\b(retail|e-?commerce|commerce|sales|shop|store)\b", re.IGNORECASE),
        "tables": [
            {"target": "dim_customers", "surrogate_key": "customer_key",
             "source_hint": re.compile(r"customer|client|buyer|account|user", re.IGNORECASE)},
            {"target": "dim_products", "surrogate_key": "product_key",
             "source_hint": re.compile(r"product|item(?!s?_)|sku|merchandise", re.IGNORECASE)},
            {"target": "fact_orders", "surrogate_key": "order_key",
             "source_hint": re.compile(r"order(?!_?(item|detail|line))|sale|transaction|purchase", re.IGNORECASE)},
            {"target": "fact_order_items", "surrogate_key": "order_item_key",
             "source_hint": re.compile(r"order_?item|line_?item|order_?detail|order_?line", re.IGNORECASE)},
        ],
    },
]


# ── Catalog grounding ──────────────────────────────────────────────────────


def _load_catalog(db: Session, connection_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """{table_name: [{name, type, nullable, primary_key}]} from Schema Intel."""
    tables = (
        db.query(CatalogTable)
        .filter(CatalogTable.connection_id == connection_id)
        .options(joinedload(CatalogTable.columns))
        .order_by(CatalogTable.table_name)
        .all()
    )
    return {
        t.table_name: [
            {
                "name": c.column_name,
                "type": c.data_type,
                "nullable": c.nullable,
                "primary_key": c.is_primary_key,
            }
            for c in sorted(t.columns, key=lambda c: c.ordinal_position)
        ]
        for t in tables
    }


# ── Deterministic proposal builders ───────────────────────────────────────


def _columns_from_source(table_name: str,
                         source_columns: List[Dict[str, Any]],
                         *, surrogate_key: Optional[str],
                         max_columns: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Copy source columns (with source_refs) behind an optional surrogate
    key; returns (columns, truncated)."""
    columns: List[Dict[str, Any]] = []
    if surrogate_key:
        columns.append({
            "name": surrogate_key, "type": "INTEGER",
            "nullable": False, "primary_key": True, "source_refs": [],
        })
    truncated = False
    for src_col in source_columns:
        if len(columns) >= max_columns:
            truncated = True
            break
        columns.append({
            "name": src_col["name"],
            "type": src_col.get("type") or "TEXT",
            "nullable": bool(src_col.get("nullable", True)),
            "primary_key": False,
            "source_refs": [{
                "table": table_name, "column": src_col["name"],
                "type": src_col.get("type"),
            }],
        })
    return columns, truncated


def _template_proposal(template: Dict[str, Any],
                       catalog: Dict[str, List[Dict[str, Any]]],
                       *, max_tables: int, max_columns: int,
                       ) -> Tuple[List[Dict[str, Any]], List[str]]:
    proposed: List[Dict[str, Any]] = []
    notes: List[str] = []
    claimed: set = set()

    for spec in template["tables"]:
        if len(proposed) >= max_tables:
            notes.append(f"plan capped at {max_tables} tables — remaining template tables dropped")
            break
        match = next(
            (name for name in catalog
             if name not in claimed and spec["source_hint"].search(name)),
            None,
        )
        if match is None:
            notes.append(
                f"template table {spec['target']} has no matching source table in the "
                f"catalog — omitted rather than invented from nothing"
            )
            continue
        claimed.add(match)
        columns, truncated = _columns_from_source(
            match, catalog[match],
            surrogate_key=spec["surrogate_key"], max_columns=max_columns,
        )
        if truncated:
            notes.append(f"{spec['target']}: capped at {max_columns} columns — extra source columns dropped")
        proposed.append({"name": spec["target"], "columns": columns, "source_table": match})
    return proposed, notes


def _catalog_driven_proposal(catalog: Dict[str, List[Dict[str, Any]]],
                             *, max_tables: int, max_columns: int,
                             ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """No template matched: restructure what's discovered (dw_<table> targets),
    don't invent domain concepts from nothing."""
    proposed: List[Dict[str, Any]] = []
    notes: List[str] = ["no domain template matched — proposing a catalog-driven target per source table"]
    for i, (name, cols) in enumerate(catalog.items()):
        if i >= max_tables:
            notes.append(f"plan capped at {max_tables} tables — remaining source tables dropped")
            break
        columns, truncated = _columns_from_source(
            name, cols, surrogate_key=None, max_columns=max_columns)
        if truncated:
            notes.append(f"dw_{name}: capped at {max_columns} columns")
        proposed.append({"name": f"dw_{name}", "columns": columns, "source_table": name})
    return proposed, notes


# ── LLM-assisted adaptation (bounded, metadata-only, strictly validated) ──


def _catalog_summary(catalog: Dict[str, List[Dict[str, Any]]], limit_chars: int = 4000) -> str:
    """Bounded metadata summary (NFR: bounded context window regardless of
    catalog size). Names + types only — never row-level values."""
    lines: List[str] = []
    for table, cols in catalog.items():
        lines.append(f"{table}(" + ", ".join(f"{c['name']}:{c.get('type') or '?'}" for c in cols) + ")")
        if sum(len(line) for line in lines) > limit_chars:
            lines.append("... (catalog truncated for prompt size)")
            break
    return "\n".join(lines)


def _validate_llm_tables(payload: Any,
                         catalog: Dict[str, List[Dict[str, Any]]],
                         *, max_tables: int, max_columns: int) -> Optional[List[Dict[str, Any]]]:
    """Strict shape/identifier/grounding validation of an LLM adaptation.
    Any violation rejects the whole adaptation (deterministic result stands)."""
    if not isinstance(payload, list) or not payload or len(payload) > max_tables:
        return None
    catalog_cols = {
        (t, c["name"]): c for t, cols in catalog.items() for c in cols
    }
    validated: List[Dict[str, Any]] = []
    for tbl in payload:
        if not isinstance(tbl, dict) or not _IDENT_RE.fullmatch(str(tbl.get("name", ""))):
            return None
        cols = tbl.get("columns")
        if not isinstance(cols, list) or not cols or len(cols) > max_columns:
            return None
        out_cols = []
        for col in cols:
            if not isinstance(col, dict) or not _IDENT_RE.fullmatch(str(col.get("name", ""))):
                return None
            refs = col.get("source_refs") or []
            if not isinstance(refs, list):
                return None
            out_refs = []
            for ref in refs:
                key = (ref.get("table"), ref.get("column")) if isinstance(ref, dict) else None
                if key not in catalog_cols:
                    return None  # ungrounded reference — reject
                out_refs.append({
                    "table": key[0], "column": key[1],
                    "type": catalog_cols[key].get("type"),
                })
            col_type = str(col.get("type") or "TEXT").strip()
            if not _TYPE_RE.fullmatch(col_type):
                return None  # ungrounded/unsafe type — reject the whole adaptation
            out_cols.append({
                "name": col["name"],
                "type": col_type,
                "nullable": bool(col.get("nullable", True)),
                "primary_key": bool(col.get("primary_key", False)),
                "source_refs": out_refs,
            })
        validated.append({"name": tbl["name"], "columns": out_cols,
                          "source_table": tbl.get("source_table")})
    return validated


def _llm_adapt(question: str,
               baseline: List[Dict[str, Any]],
               catalog: Dict[str, List[Dict[str, Any]]],
               dialect: str,
               *, max_tables: int, max_columns: int,
               ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Ask the LLM to adapt the deterministic baseline to the actual catalog.
    Returns (tables, None) on success or (None, reason) on any failure —
    the deterministic baseline always stands as the fallback."""
    if not settings.AGENTIC_DBA_LLM_ENABLED:
        return None, "LLM adaptation disabled (AGENTIC_DBA_LLM_ENABLED=false)"

    prompt = f"""You are a data warehouse architect. Adapt this draft target-schema proposal to the actual source catalog below. Improve column naming/types where clearly better, keep every column grounded in a real source column via source_refs, and DO NOT invent tables or columns with no basis in the catalog.

SOURCE CATALOG (metadata only):
{_catalog_summary(catalog)}

DRAFT PROPOSAL (JSON):
{json.dumps(baseline)}

USER REQUEST: {question}
TARGET DIALECT: {dialect}

Rules:
- Return ONLY a JSON array with the same shape as the draft proposal.
- At most {max_tables} tables and {max_columns} columns per table.
- Every source_refs entry must reference an existing catalog table/column.

JSON:"""

    for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
        try:
            def _post():
                return requests.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json={"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False},
                    timeout=settings.OLLAMA_TIMEOUT,
                )
            resp = ollama_circuit.call(_post)
            if resp.status_code == 200:
                raw = resp.json().get("response", "").strip()
                match = re.search(r"\[.*\]", raw, re.DOTALL)
                if not match:
                    return None, "LLM returned no JSON array"
                try:
                    payload = json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None, "LLM returned unparseable JSON"
                validated = _validate_llm_tables(
                    payload, catalog, max_tables=max_tables, max_columns=max_columns)
                if validated is None:
                    return None, "LLM adaptation failed grounding/shape validation"
                return validated, None
            logger.warning("[agentic_dba] Ollama returned status %s on attempt %d",
                           resp.status_code, attempt + 1)
        except CircuitBreakerOpen as exc:
            return None, f"Ollama circuit open: {exc}"
        except Exception as exc:
            logger.warning("[agentic_dba] Ollama adaptation failed (attempt %d/%d): %s",
                           attempt + 1, settings.OLLAMA_MAX_RETRIES + 1, exc)
            if attempt < settings.OLLAMA_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None, "LLM unreachable — using deterministic proposal"


# ── DDL generation (dialect-aware, decision #8) + collision (task #9) ─────


_ORACLE_TYPES = {"TEXT": "CLOB", "VARCHAR": "VARCHAR2(255)", "STRING": "VARCHAR2(255)",
                 "CHAR": "CHAR", "DOUBLE": "BINARY_DOUBLE", "BOOLEAN": "NUMBER(1)",
                 "BOOL": "NUMBER(1)", "DATETIME": "TIMESTAMP", "TINYINT": "NUMBER(3)"}
_MYSQL_TYPES = {"TEXT": "TEXT", "STRING": "VARCHAR(255)", "VARCHAR": "VARCHAR(255)",
                "CLOB": "TEXT", "TIMESTAMPTZ": "TIMESTAMP", "BOOLEAN": "BOOLEAN"}
_POSTGRES_TYPES = {"STRING": "VARCHAR", "CLOB": "TEXT", "DATETIME": "TIMESTAMP",
                   "TINYINT": "SMALLINT", "DOUBLE": "DOUBLE PRECISION"}


def _dialect_type(raw: Optional[str], dialect: str) -> str:
    if not raw:
        return "TEXT" if dialect != "oracle" else "CLOB"
    base_match = re.match(r"\s*([A-Za-z ]+?)\s*(\(|$)", raw)
    base = (base_match.group(1).strip().upper() if base_match else raw.upper())
    if "(" in raw:  # explicit precision — preserve as-authored
        return raw
    mapping = {"oracle": _ORACLE_TYPES, "mysql": _MYSQL_TYPES,
               "postgres": _POSTGRES_TYPES}.get(dialect, {})
    return mapping.get(base, raw)


def _create_table_statement(table: Dict[str, Any], dialect: str) -> str:
    lines = []
    for col in table["columns"]:
        parts = [col["name"], _dialect_type(col.get("type"), dialect)]
        if col.get("primary_key"):
            parts.append("PRIMARY KEY")
        elif col.get("nullable") is False:
            parts.append("NOT NULL")
        lines.append("  " + " ".join(parts))
    return f"CREATE TABLE {table['name']} (\n" + ",\n".join(lines) + "\n)"


def _migration_statements(table: Dict[str, Any],
                          existing_columns: List[Dict[str, Any]],
                          dialect: str) -> Tuple[List[str], List[str]]:
    """Collision path (task #9): existing table -> ALTER-based migration,
    reusing schema_mapper_service.generate_migration_sql's precedent
    (ADD COLUMN for missing; type change unsupported on SQLite -> comment)."""
    statements: List[str] = []
    warnings: List[str] = []
    existing_by_name = {c["name"].lower(): c for c in existing_columns}
    for col in table["columns"]:
        current = existing_by_name.get(col["name"].lower())
        if current is None:
            statements.append(
                f"ALTER TABLE {table['name']} ADD COLUMN "
                f"{col['name']} {_dialect_type(col.get('type'), dialect)}"
            )
        else:
            proposed_type = (col.get("type") or "").split("(")[0].upper()
            current_type = (current.get("type") or "").split("(")[0].upper()
            if proposed_type and current_type and proposed_type != current_type:
                if dialect == "sqlite":
                    warnings.append(
                        f"{table['name']}.{col['name']}: SQLite doesn't support ALTER COLUMN "
                        f"({current_type} → {proposed_type}); consider recreating the table"
                    )
                    statements.append(
                        f"-- ALTER TABLE {table['name']} ALTER COLUMN {col['name']} "
                        f"TYPE {proposed_type} (unsupported in SQLite)"
                    )
                else:
                    statements.append(
                        f"ALTER TABLE {table['name']} ALTER COLUMN {col['name']} "
                        f"TYPE {_dialect_type(col.get('type'), dialect)}"
                    )
    return statements, warnings


def _generate_ddl(proposed_tables: List[Dict[str, Any]],
                  catalog: Dict[str, List[Dict[str, Any]]],
                  dialect: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    ddl: List[Dict[str, Any]] = []
    notes: List[str] = []
    catalog_lower = {name.lower(): cols for name, cols in catalog.items()}
    for table in proposed_tables:
        bad_idents = [c["name"] for c in table["columns"]
                      if not _IDENT_RE.fullmatch(c["name"])]
        if not _IDENT_RE.fullmatch(table["name"]) or bad_idents:
            notes.append(f"{table['name']}: invalid identifier(s) {bad_idents} — DDL skipped")
            continue
        existing = catalog_lower.get(table["name"].lower())
        if existing is not None:
            statements, warnings = _migration_statements(table, existing, dialect)
            notes.append(
                f"table {table['name']} already exists — proposing an ALTER-based "
                f"migration instead of CREATE TABLE (collision detected)"
            )
            notes.extend(warnings)
            ddl.append({"table": table["name"], "mode": "migrate", "statements": statements})
        else:
            ddl.append({"table": table["name"], "mode": "create",
                        "statements": [_create_table_statement(table, dialect)]})
    return ddl, notes


# ── Public API ─────────────────────────────────────────────────────────────


def create_plan(db: Session, *, question: str, connection_id: int,
                session_id: Optional[str], actor: str,
                target_connection_id: Optional[int] = None) -> SchemaDesignPlan:
    """Insert the plan row (status=generating) and audit the request.
    Generation itself runs async (generate_plan / the Celery task)."""
    conn = db.query(DBConnection).filter(DBConnection.id == connection_id).first()
    if conn is None:
        raise ValueError(f"connection {connection_id} not found")
    plan = SchemaDesignPlan(
        question=question, session_id=session_id,
        source_connection_id=connection_id,
        target_connection_id=target_connection_id,
        status="generating", created_by=actor, dialect=conn.type,
    )
    db.add(plan)
    db.flush()
    emit_audit_event(
        db, event_type="agentic_dba.plan_requested", actor=actor,
        module="agentic_dba", target_type="connection",
        target_id=connection_id, target_name=conn.name,
        summary=question[:200], outcome="success",
        metadata={"plan_id": plan.id, "session_id": session_id},
    )
    db.commit()
    db.refresh(plan)
    return plan


def generate_plan(db: Session, plan_id: int) -> SchemaDesignPlan:
    """The actual generation work — called from the Celery task (async by
    design, NFR) or directly in tests."""
    plan = db.query(SchemaDesignPlan).filter(SchemaDesignPlan.id == plan_id).first()
    if plan is None:
        raise ValueError(f"plan {plan_id} not found")
    logger.info("[agentic_dba] stage=generate_plan plan_id=%d", plan_id)

    try:
        catalog = _load_catalog(db, plan.source_connection_id)
        if not catalog:
            plan.status = "failed"
            plan.error = ("no Schema Intel catalog for this connection — scan it first "
                          "(Schema Intel → Scan catalog), then re-ask")
            db.commit()
            return plan

        max_tables = settings.AGENTIC_DBA_MAX_TABLES
        max_columns = settings.AGENTIC_DBA_MAX_COLUMNS_PER_TABLE
        notes: List[str] = []

        template = next(
            (t for t in DOMAIN_TEMPLATES if t["match"].search(plan.question)), None)
        if template:
            proposed, t_notes = _template_proposal(
                template, catalog, max_tables=max_tables, max_columns=max_columns)
            notes.extend(t_notes)
            plan.domain_template = template["name"]
            if not proposed:
                proposed, f_notes = _catalog_driven_proposal(
                    catalog, max_tables=max_tables, max_columns=max_columns)
                notes.extend(f_notes)
                plan.domain_template = None
        else:
            proposed, f_notes = _catalog_driven_proposal(
                catalog, max_tables=max_tables, max_columns=max_columns)
            notes.extend(f_notes)

        adapted, llm_reason = _llm_adapt(
            plan.question, proposed, catalog, plan.dialect or "sqlite",
            max_tables=max_tables, max_columns=max_columns)
        if adapted is not None:
            proposed = adapted
            notes.append("proposal adapted by LLM (grounding-validated against the catalog)")
        elif llm_reason:
            notes.append(llm_reason)

        dq_rules, dq_notes = propose_dq_rules(db, plan.source_connection_id, proposed)
        notes.extend(dq_notes)

        transformations, tf_notes = propose_transformations(
            db, plan.source_connection_id, proposed)
        notes.extend(tf_notes)

        # Collision detection runs against the TARGET connection's catalog
        # (same as source when no distinct target is set).
        target_catalog = catalog
        if plan.target_connection_id and plan.target_connection_id != plan.source_connection_id:
            target_catalog = _load_catalog(db, plan.target_connection_id)
        ddl, ddl_notes = _generate_ddl(proposed, target_catalog, plan.dialect or "sqlite")
        notes.extend(ddl_notes)

        plan.proposed_tables = proposed
        plan.dq_rules = dq_rules
        plan.transformations = transformations
        plan.generated_ddl = ddl
        plan.confidence_notes = notes
        plan.status = "ready"
        emit_audit_event(
            db, event_type="agentic_dba.plan_ready", actor=plan.created_by or "system",
            module="agentic_dba", target_type="plan", target_id=plan.id,
            summary=f"plan ready: {len(proposed)} table(s), {len(dq_rules)} DQ rule(s)",
            outcome="success",
            metadata={"plan_id": plan.id, "tables": [t["name"] for t in proposed],
                      "template": plan.domain_template},
        )
        db.commit()
        logger.info("[agentic_dba] stage=plan_ready plan_id=%d tables=%d", plan_id, len(proposed))

        # Notify-out (aci_integration_tasks #5): a plan awaiting review is an
        # approval-queue event — same opt-in fan-out as Autopilot, fire-and-
        # forget after the commit above.
        from app.services.notification_service import dispatch_notify_out
        dispatch_notify_out(
            db, event_key="agentic_dba:schema_design_create",
            title=f"Schema design plan #{plan.id} ready for review",
            body=plan.question[:300],
            link=f"{settings.DATAONE_BASE_URL}/dashboard/query-workspace",
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        plan.status = "failed"
        plan.error = str(exc)
        db.commit()
        logger.warning("[agentic_dba] stage=plan_failed plan_id=%d error=%s", plan_id, exc)
    db.refresh(plan)
    return plan
