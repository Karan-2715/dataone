"""Natural-language -> SQL chat backed by a Databricks Genie agent (Genie Conversation API).

Genie is the "LLM": it turns the question into SQL over the agent's curated Unity Catalog tables, runs it on the
agent's SQL warehouse and writes the answer. DataOne only relays the conversation:

    start_chat()  -> {conversation_id, message_id}            (returns immediately; Genie takes 10-120s)
    get_result()  -> status, answer text, generated SQL, rows  (poll until ``done``)

This is the same API the AppKit ``genie()`` plugin wraps (AppKit itself is TypeScript-only, so this FastAPI app talks
to the API directly). Calls run as the app's workspace client (service principal), which needs CAN RUN on the Genie
space, SELECT on its tables and CAN USE on its warehouse.

The REST endpoints are called directly (``api_client.do``) instead of through ``WorkspaceClient.genie``: the SDK's Genie
dataclasses changed field names across versions (e.g. ``GenieAttachment.id`` vs ``attachment_id``) and the version the
Databricks Apps build resolves is not the one we develop against.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_SPACE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
_UC_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,255}$")
DATA_SPACE_PREFIX = "DataOne data: "  # Genie agents the app creates for a selected catalog/schema/table
MAX_SCOPE_TABLES = 20  # Genie allows 30 per agent; fewer keeps answers accurate
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED")


class GenieChatError(RuntimeError):
    """Genie is not configured, the space id is invalid, or the Genie call failed."""


def _client():
    from app.services.databricks_ingestion_service import _get_workspace_client
    return _get_workspace_client(None)


def resolve_space_id(space_id: Optional[str]) -> str:
    resolved = (space_id or settings.DATABRICKS_GENIE_SPACE_ID or "").strip()
    if not resolved:
        raise GenieChatError("No Genie agent is configured. Set DATABRICKS_GENIE_SPACE_ID or pick an agent.")
    if not _SPACE_ID_RE.match(resolved):
        raise GenieChatError("Invalid Genie space id.")
    return resolved


def status() -> Dict[str, Any]:
    return {"configured": bool(settings.DATABRICKS_GENIE_SPACE_ID), "default_space_id": settings.DATABRICKS_GENIE_SPACE_ID}


def _api(wc, method: str, path: str, *, query: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None):
    return wc.api_client.do(method, path, query=query, body=body)


def list_spaces() -> List[Dict[str, Any]]:
    """Genie agents the app can use (the service principal only sees spaces it has been granted)."""
    spaces: List[Dict[str, Any]] = []
    token: Optional[str] = None
    wc = _client()
    for _ in range(10):  # page cap
        resp = _api(wc, "GET", "/api/2.0/genie/spaces", query={"page_size": 100, **({"page_token": token} if token else {})})
        for s in resp.get("spaces") or []:
            title = s.get("title")
            spaces.append({"space_id": s.get("space_id"), "title": title, "description": s.get("description"),
                           "auto": bool(title and title.startswith(DATA_SPACE_PREFIX))})
        token = resp.get("next_page_token")
        if not token:
            break
    return spaces


def start_chat(question: str, space_id: Optional[str] = None, conversation_id: Optional[str] = None) -> Dict[str, str]:
    """Ask a question. With ``conversation_id`` it is a follow-up and Genie keeps the context."""
    space = resolve_space_id(space_id)
    wc = _client()
    logger.info("[genie_chat] stage=start space=%s followup=%s question_len=%d", space, bool(conversation_id), len(question))
    base = f"/api/2.0/genie/spaces/{space}"
    if conversation_id:
        resp = _api(wc, "POST", f"{base}/conversations/{conversation_id}/messages", body={"content": question})
        conv = conversation_id
    else:
        resp = _api(wc, "POST", f"{base}/start-conversation", body={"content": question})
        conv = resp.get("conversation_id") or (resp.get("conversation") or {}).get("id")
    msg = resp.get("message_id") or (resp.get("message") or {}).get("id") or resp.get("id")
    if not conv or not msg:
        raise GenieChatError("Genie did not return a conversation id.")
    return {"space_id": space, "conversation_id": conv, "message_id": msg}


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def get_result(space_id: Optional[str], conversation_id: str, message_id: str) -> Dict[str, Any]:
    """Current state of one question. ``done`` is True once Genie has finished (or failed)."""
    space = resolve_space_id(space_id)
    wc = _client()
    message = _api(wc, "GET", f"/api/2.0/genie/spaces/{space}/conversations/{conversation_id}/messages/{message_id}")
    state = message.get("status") or "SUBMITTED"
    out: Dict[str, Any] = {
        "space_id": space, "conversation_id": conversation_id, "message_id": message_id,
        "status": state, "done": state in TERMINAL_STATUSES,
        "answer": None, "sql": None, "sql_title": None, "sql_description": None,
        "columns": [], "rows": [], "row_count": None, "truncated": False,
        "suggested_questions": [], "error": None,
    }
    err = message.get("error")
    if err:
        out["error"] = _text(err.get("error") if isinstance(err, dict) else str(err)) or "Genie could not answer this question."

    query_attachment_id: Optional[str] = None
    for att in message.get("attachments") or []:
        text = att.get("text") or {}
        if _text(text.get("content")):
            out["answer"] = _text(text.get("content"))
        query = att.get("query") or {}
        if _text(query.get("query")):
            out["sql"] = query["query"]
            out["sql_title"] = _text(query.get("title"))
            out["sql_description"] = _text(query.get("description"))
            query_attachment_id = att.get("attachment_id") or att.get("id")
            meta = query.get("query_result_metadata") or {}
            if meta.get("row_count") is not None:
                out["row_count"] = meta.get("row_count")
            out["truncated"] = bool(meta.get("is_truncated"))
        questions = (att.get("suggested_questions") or {}).get("questions") or []
        if questions:
            out["suggested_questions"] = [q for q in questions if _text(q)][:5]

    if state == "FAILED" and not out["error"]:
        out["error"] = "Genie could not answer this question."
    if state == "COMPLETED" and query_attachment_id:
        try:
            _attach_rows(wc, out, space, conversation_id, message_id, query_attachment_id)
        except Exception as exc:  # the SQL/answer is still useful when only the rows could not be fetched
            logger.warning("[genie_chat] could not fetch query result: %s", exc)
            out["error"] = f"Genie generated the SQL but the result could not be read: {exc}"
    return out


def _attach_rows(wc, out: Dict[str, Any], space: str, conversation_id: str, message_id: str, attachment_id: str) -> None:
    base = f"/api/2.0/genie/spaces/{space}/conversations/{conversation_id}/messages/{message_id}"
    try:
        result = _api(wc, "GET", f"{base}/attachments/{attachment_id}/query-result")
    except Exception as exc:  # older workspaces expose the legacy path
        if "404" not in str(exc) and "not found" not in str(exc).lower():
            raise
        result = _api(wc, "GET", f"{base}/query-result/{attachment_id}")
    stmt = result.get("statement_response") or {}
    schema = ((stmt.get("manifest") or {}).get("schema") or {})
    out["columns"] = [
        {"name": c.get("name"), "type": c.get("type_name") or c.get("type_text") or ""}
        for c in schema.get("columns") or []
    ]
    data = (stmt.get("result") or {}).get("data_array") or []
    cap = settings.GENIE_CHAT_MAX_ROWS
    out["rows"] = data[:cap]
    total = out["row_count"] if out["row_count"] is not None else len(data)
    out["row_count"] = total
    out["truncated"] = bool(out["truncated"] or len(data) > cap)


# ── Scope a chat to a selected catalog / schema / table ─────────────────────────────────────────────
# A Genie agent only answers from the tables attached to it, so "ask about workspace.default.titanic" needs an agent
# that has exactly that data. The app creates one per selection (reused by title afterwards) as its own principal.

def _warehouse_id(wc) -> str:
    warehouse_id = getattr(settings, "DATABRICKS_SQL_WAREHOUSE_ID", None)
    if warehouse_id:
        return warehouse_id
    warehouses = list(wc.warehouses.list())
    if not warehouses:
        raise GenieChatError("No SQL warehouse is available for Genie.")
    return warehouses[0].id


def ensure_data_space(catalog: str, schema: str, table: Optional[str] = None) -> Dict[str, Any]:
    """Genie agent over ``catalog.schema.table`` (or every table in the schema). Created on first use, then reused."""
    for label, name in (("catalog", catalog), ("schema", schema), ("table", table or "x")):
        if not _UC_NAME_RE.match(name):
            raise GenieChatError(f"Invalid {label} name.")
    wc = _client()
    if table:
        try:
            wc.tables.get(f"{catalog}.{schema}.{table}")
        except Exception as exc:
            raise GenieChatError(f"The app cannot read {catalog}.{schema}.{table}: {exc}") from exc
        names = [table]
    else:
        names = sorted(t.name for t in wc.tables.list(catalog_name=catalog, schema_name=schema) if t.name)[:MAX_SCOPE_TABLES]
        if not names:
            raise GenieChatError(f"{catalog}.{schema} has no tables the app can read.")

    scope = f"{catalog}.{schema}" + (f".{table}" if table else "")
    title = f"{DATA_SPACE_PREFIX}{scope}"
    for existing in list_spaces():
        if existing["title"] == title:
            return {"space_id": existing["space_id"], "title": title, "created": False, "tables": names}

    logger.info("[genie_chat] stage=create_data_space scope=%s tables=%d", scope, len(names))
    created = _api(wc, "POST", "/api/2.0/genie/spaces", body={
        "title": title,
        "description": f"Natural-language questions over {scope}. Created by DataOne.",
        "warehouse_id": _warehouse_id(wc),
        "serialized_space": json.dumps({
            "version": 2,
            "data_sources": {"tables": [{"identifier": f"{catalog}.{schema}.{n}"} for n in names]},
        }),
    })
    space_id = created.get("space_id") or created.get("id")
    if not space_id:
        raise GenieChatError("Genie did not return the new agent's id.")
    return {"space_id": space_id, "title": title, "created": True, "tables": names}
