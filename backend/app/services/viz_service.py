"""Visualize query engine + saved-view CRUD (Visualize Task #1, VIZ-T1/T5).

VizService.run_query builds and executes a real GROUP BY aggregation
against the connection (dimensions in the SELECT/GROUP BY, measures as
aggregate functions, filters as a WHERE clause) — the same "trusted
identifier, parameterized value" discipline used by
pipeline_executor.py and the schema_intel profiling connectors.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.models.security import MaskingPolicy, RowAccessPolicy
from app.models.viz import VizView
from app.services.audit_helper import record_audit
from app.services.rbac_service import MaskingPolicyCRUD
from app.services.schema_service import get_connector

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ROWS = 5000

_AGG_SQL = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}

# Row-access policy operators (SEC-T4) -- distinct from the eq/neq/... set
# viz's own FilterBar uses (_OPERATOR_SQL below), because policies are
# authored via the Security admin page's builder, which speaks "=", "!=", etc.
_POLICY_OPERATOR_SQL = {
    "=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
}


def _validate_identifier(name: str, *, kind: str) -> str:
    if kind == "table":
        for part in name.split("."):
            if not _IDENT_RE.match(part):
                raise HTTPException(status_code=422, detail=f"invalid {kind} name: {name!r}")
        return name

    if not _IDENT_RE.match(name):
        raise HTTPException(status_code=422, detail=f"invalid {kind} name: {name!r}")
    return name


def _quote(dialect: str, identifier: str) -> str:
    if dialect in ("mysql", "databricks"):
        return "`" + identifier.replace("`", "``") + "`"
    return '"' + identifier.replace('"', '""') + '"'


def _quote_table(dialect: str, table_name: str) -> str:
    return ".".join(_quote(dialect, p) for p in table_name.split("."))


def _placeholder(dialect: str) -> str:
    return "?" if dialect == "sqlite" else "%s"


_OPERATOR_SQL = {
    "eq": "=", "neq": "!=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
}


class VizService:
    @staticmethod
    def run_query(
        db: Session, *, connection_id: int, table_name: str,
        dimensions: List[str], measures: List[Dict[str, Any]], filters: List[Dict[str, Any]],
        requester_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        conn = db.query(DBConnection).filter(
            DBConnection.id == connection_id, DBConnection.is_deleted == False,  # noqa: E712
        ).first()
        if not conn:
            raise HTTPException(status_code=404, detail="connection not found")

        if not dimensions and not measures:
            raise HTTPException(status_code=422, detail="at least one dimension or measure is required")

        dialect = (conn.type or "").lower()
        _validate_identifier(table_name, kind="table")
        for d in dimensions:
            _validate_identifier(d, kind="dimension field")
        for m in measures:
            _validate_identifier(m["field"], kind="measure field")
        for f in filters:
            _validate_identifier(f["field"], kind="filter field")

        q = lambda ident: _quote(dialect, ident)  # noqa: E731
        ph = _placeholder(dialect)

        select_parts: List[str] = [q(d) for d in dimensions]
        columns: List[str] = list(dimensions)
        for m in measures:
            agg_sql = _AGG_SQL[m["aggregation"]]
            alias = m.get("label") or f"{m['aggregation']}_{m['field']}"
            _validate_identifier(alias, kind="measure alias")
            select_parts.append(f"{agg_sql}({q(m['field'])}) AS {q(alias)}")
            columns.append(alias)

        where_clauses: List[str] = []
        params: List[Any] = []
        for f in filters:
            field_sql = q(f["field"])
            op = f["operator"]
            value = f.get("value")
            if op == "contains":
                where_clauses.append(f"{field_sql} LIKE {ph}")
                params.append(f"%{value}%")
            elif op == "between":
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise HTTPException(status_code=422, detail="'between' filter requires a 2-element value")
                where_clauses.append(f"{field_sql} BETWEEN {ph} AND {ph}")
                params.extend(value)
            else:
                where_clauses.append(f"{field_sql} {_OPERATOR_SQL[op]} {ph}")
                params.append(value)

        # Row-level access filters (SEC-T4, FR5) — ANDed onto the user's own
        # filters, scoped to policies that name this connection+table and
        # apply to the requester's role. Deny-by-default doesn't apply here
        # (absence of a policy means unfiltered, matching every other
        # module's "no policy defined yet" behavior), but any policy that
        # DOES name this role is enforced unconditionally — the caller
        # cannot opt out via the filters it sends.
        if requester_role:
            row_policies = (
                db.query(RowAccessPolicy)
                .filter(RowAccessPolicy.connection_id == connection_id, RowAccessPolicy.table_name == table_name)
                .all()
            )
            for policy in row_policies:
                if requester_role not in (policy.applies_to_roles or []):
                    continue
                clause_parts: List[str] = []
                for i, cond in enumerate(policy.filter_conditions or []):
                    _validate_identifier(cond["field"], kind="row-policy filter field")
                    field_sql = q(cond["field"])
                    op_sql = _POLICY_OPERATOR_SQL.get(cond["operator"])
                    if op_sql:
                        clause_parts.append(f"{field_sql} {op_sql} {ph}" if i == 0 else
                                             f"{cond.get('logic', 'AND')} {field_sql} {op_sql} {ph}")
                        params.append(cond["value"])
                    elif cond["operator"] in ("in", "not in"):
                        values = cond["value"] if isinstance(cond["value"], list) else [cond["value"]]
                        placeholders = ", ".join([ph] * len(values))
                        not_kw = "NOT " if cond["operator"] == "not in" else ""
                        prefix = "" if i == 0 else f"{cond.get('logic', 'AND')} "
                        clause_parts.append(f"{prefix}{field_sql} {not_kw}IN ({placeholders})")
                        params.extend(values)
                if clause_parts:
                    where_clauses.append("(" + " ".join(clause_parts) + ")")

        sql = f"SELECT {', '.join(select_parts)} FROM {_quote_table(dialect, table_name)}"
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        if dimensions:
            sql += " GROUP BY " + ", ".join(q(d) for d in dimensions)
            sql += " ORDER BY " + ", ".join(q(d) for d in dimensions)
        sql += f" LIMIT {MAX_ROWS + 1}"

        connector = get_connector(conn)
        try:
            handle = connector.connect()
            cursor = handle.cursor() if hasattr(handle, "cursor") else None
            if cursor is not None:
                cursor.execute(sql, tuple(params))
                raw_rows = cursor.fetchall()
            else:
                # JDBCConnector: SQLAlchemy Connection, no .cursor()
                from sqlalchemy import text
                bind_sql = sql
                bind_params = {}
                # Convert positional placeholders to named for SQLAlchemy text()
                for i, val in enumerate(params):
                    key = f"p{i}"
                    bind_sql = bind_sql.replace(ph, f":{key}", 1)
                    bind_params[key] = val
                result = handle.execute(text(bind_sql), bind_params)
                raw_rows = result.fetchall()
        except Exception as exc:
            logger.warning("[viz] query failed connection_id=%s table=%s error=%s",
                          connection_id, table_name, exc)
            raise HTTPException(status_code=502, detail=f"query failed: {exc}") from exc
        finally:
            connector.close()

        truncated = len(raw_rows) > MAX_ROWS
        rows = [list(r) for r in raw_rows[:MAX_ROWS]]

        # Column-level PII masking (SEC-T3, FR4/AC2) — masks dimension
        # columns whose name matches a MaskingPolicy for this
        # connection+table, unless the requester's role is exempt.
        # Measure aliases (e.g. "sum_amount") never match a raw column
        # name, so aggregated values are never masked — only the raw
        # dimension values they're grouped by can be.
        if requester_role:
            masking_policies = {
                p.column_name: p
                for p in db.query(MaskingPolicy).filter(
                    MaskingPolicy.connection_id == connection_id, MaskingPolicy.table_name == table_name,
                ).all()
            }
            masked_indexes = {
                idx: masking_policies[col]
                for idx, col in enumerate(columns)
                if col in masking_policies and requester_role not in (masking_policies[col].exempt_roles or [])
            }
            if masked_indexes:
                for row in rows:
                    for idx, policy in masked_indexes.items():
                        row[idx] = MaskingPolicyCRUD.apply_masking(row[idx], policy.masking_type)

        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}

    # ── Saved views (VIZ-T5) ─────────────────────────────────────────────

    @staticmethod
    def create_view(db: Session, *, actor: str, **kwargs) -> VizView:
        view = VizView(
            created_by=actor,
            **{k: ([m if isinstance(m, dict) else m.model_dump() for m in v] if k in ("measures", "filters") else v)
               for k, v in kwargs.items()},
        )
        db.add(view)
        db.flush()
        record_audit(db, "viz_view_created", actor=actor,
                     connection_id=view.connection_id,
                     payload={"view_id": view.id, "name": view.name})
        db.commit()
        db.refresh(view)
        return view

    @staticmethod
    def list_views(db: Session) -> tuple:
        items = db.query(VizView).order_by(VizView.updated_at.desc()).all()
        return items, len(items)

    @staticmethod
    def get_view(db: Session, view_id: int) -> VizView:
        view = db.query(VizView).filter(VizView.id == view_id).first()
        if not view:
            raise HTTPException(status_code=404, detail="view not found")
        return view

    @staticmethod
    def delete_view(db: Session, view_id: int, *, actor: str) -> None:
        view = VizService.get_view(db, view_id)
        record_audit(db, "viz_view_deleted", actor=actor,
                     connection_id=view.connection_id,
                     payload={"view_id": view_id, "name": view.name})
        db.delete(view)
        db.commit()
