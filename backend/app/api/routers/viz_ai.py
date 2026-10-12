"""AI-driven visualization insights endpoint.

Given a connection_id and table_name, this endpoint:
1. Looks up column metadata from the Schema Intel catalog (or live introspection)
2. Sends column names + types to the Databricks LLM
3. LLM responds with chart recommendations (type, x, y, agg, title, insight, color palette)
4. Returns those recommendations + a live sample of data rows for rendering

No manual chart config needed by the user.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.connection import DBConnection
from app.models.user import User
from app.services.databricks_llm_provider import get_databricks_llm_provider
from app.services.schema_catalog_service import SchemaCatalogService
from app.services.schema_service import SchemaService, get_connector
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class AIInsightRequest(BaseModel):
    connection_id: int
    table_name: str   # can be catalog.schema.table or schema.table or just table


class ChartSpec(BaseModel):
    chart_type: str
    title: str
    insight: str
    x_column: Optional[str] = None
    y_column: Optional[str] = None
    agg: Optional[str] = None
    group_by: Optional[str] = None
    color_palette: List[str]
    priority: int


class AIInsightResponse(BaseModel):
    table_name: str
    table_summary: str
    charts: List[ChartSpec]
    columns: List[Dict[str, Any]]
    sample_rows: List[Dict[str, Any]]


_SYSTEM_PROMPT = """You are a senior data analyst and visualization expert.
Given a database table schema, recommend up to 6 visually distinct charts for business stakeholders.
Each chart should reveal a different insight angle (distribution, trend, composition, ranking, correlation, KPI).

Return ONLY a valid JSON object — no markdown, no explanation:
{
  "table_summary": "One paragraph business description of this table",
  "charts": [
    {
      "chart_type": "bar|line|pie|scatter|area|kpi",
      "title": "Chart title",
      "insight": "One-sentence business insight this chart reveals",
      "x_column": "column_name or null",
      "y_column": "column_name or null",
      "agg": "sum|avg|count|min|max or null",
      "group_by": "column_name or null",
      "color_palette": ["#hex1", "#hex2", "#hex3"],
      "priority": 1
    }
  ]
}

Rules:
- chart_type must be one of: bar, line, pie, scatter, area, kpi
- For kpi: x_column=null, y_column=the numeric column, agg=the aggregation
- Only reference columns from the provided schema
- color_palette must have 3-8 hex colors chosen for dark backgrounds
- priority 1 = most important, 6 = least
"""


def _build_column_description(columns: List[Dict[str, Any]]) -> str:
    lines = []
    for c in columns:
        name = c.get("name") or c.get("column_name", "?")
        dtype = c.get("type") or c.get("data_type", "unknown")
        pk = " [PRIMARY KEY]" if c.get("primary_key") or c.get("is_primary_key") else ""
        lines.append(f"  - {name}: {dtype}{pk}")
    return "\n".join(lines)


def _get_columns(db: Session, connection: DBConnection, table_name: str) -> List[Dict[str, Any]]:
    catalog_tables = SchemaCatalogService.get_catalog(db, connection.id)
    if catalog_tables:
        short = table_name.split(".")[-1]
        for t in catalog_tables:
            if t.table_name == short or t.table_name == table_name:
                return [
                    {
                        "name": c.column_name,
                        "type": c.data_type,
                        "nullable": c.nullable,
                        "primary_key": c.is_primary_key,
                    }
                    for c in t.columns
                ]
    try:
        schema = SchemaService.get_full_schema(connection)
        short = table_name.split(".")[-1]
        cols = schema.get(table_name) or schema.get(short) or []
        if isinstance(cols, list) and cols and isinstance(cols[0], dict):
            return cols
        return [{"name": str(c), "type": "unknown"} for c in cols]
    except Exception as exc:
        logger.warning("AI viz: column introspection failed for %s: %s", table_name, exc)
        return []


def _fetch_sample(connection: DBConnection, table_name: str, limit: int = 200) -> List[Dict[str, Any]]:
    connector = None
    try:
        connector = get_connector(connection)
        parts = table_name.split(".")
        dialect = connection.type or "databricks"
        q = "`" if dialect in ("mysql", "databricks") else '"'
        quoted = ".".join(f"{q}{p}{q}" for p in parts)
        rows = connector.execute_query(f"SELECT * FROM {quoted} LIMIT {limit}")
        if isinstance(rows, list):
            return rows
        return []
    except Exception as exc:
        logger.warning("AI viz: sample fetch failed for %s: %s", table_name, exc)
        return []
    finally:
        if connector:
            try:
                connector.close()
            except Exception:
                pass


@router.post("/ai-insights", response_model=AIInsightResponse)
def get_ai_insights(
    req: AIInsightRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AIInsightResponse:
    """Use the Databricks LLM to auto-generate chart recommendations for a table."""

    conn = db.query(DBConnection).filter(DBConnection.id == req.connection_id).first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    columns = _get_columns(db, conn, req.table_name)
    if not columns:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve columns for table '{req.table_name}'. "
                   "Scan the connection in Schema Intel first.",
        )

    sample_rows = _fetch_sample(conn, req.table_name, limit=200)

    llm_model = (
        getattr(user, "llm_model", None)
        or settings.DATABRICKS_LLM_ENDPOINT
        or "databricks-meta-llama-3-3-70b-instruct"
    )
    col_desc = _build_column_description(columns)
    user_prompt = (
        f"Table name: {req.table_name}\n\n"
        f"Columns:\n{col_desc}\n\n"
        f"Available sample rows: {len(sample_rows)}\n\n"
        "Generate visualization recommendations."
    )

    llm_result: Dict[str, Any] = {}
    try:
        provider = get_databricks_llm_provider(endpoint_name=llm_model)
        raw = provider.generate(
            prompt=f"{_SYSTEM_PROMPT}\n\n{user_prompt}",
            temperature=0.2,
            max_tokens=2000,
        )
        text = raw.get("response", "").strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
        text = text.strip().rstrip("`").strip()
        llm_result = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("AI viz: LLM returned invalid JSON: %s", exc)
        raise HTTPException(status_code=502, detail="LLM returned malformed JSON. Try again.")
    except Exception as exc:
        logger.error("AI viz: LLM call failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}")

    raw_charts = llm_result.get("charts", [])
    charts: List[ChartSpec] = []
    VALID_TYPES = {"bar", "line", "pie", "scatter", "area", "kpi"}

    for i, rc in enumerate(raw_charts[:6]):
        ct = str(rc.get("chart_type", "bar")).lower()
        if ct not in VALID_TYPES:
            ct = "bar"
        agg = rc.get("agg")
        if agg and agg not in ("sum", "avg", "count", "min", "max"):
            agg = "count"
        charts.append(ChartSpec(
            chart_type=ct,
            title=str(rc.get("title", f"Chart {i+1}")),
            insight=str(rc.get("insight", "")),
            x_column=rc.get("x_column"),
            y_column=rc.get("y_column"),
            agg=agg,
            group_by=rc.get("group_by"),
            color_palette=rc.get("color_palette", ["#6366f1", "#8b5cf6", "#ec4899"]),
            priority=int(rc.get("priority", i + 1)),
        ))

    charts.sort(key=lambda c: c.priority)

    return AIInsightResponse(
        table_name=req.table_name,
        table_summary=llm_result.get("table_summary", ""),
        charts=charts,
        columns=columns,
        sample_rows=sample_rows,
    )
