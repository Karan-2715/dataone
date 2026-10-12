"""Genie chat: plain-English questions answered by a Databricks Genie agent (NL -> SQL -> rows -> answer).

GET  /api/v1/genie/status                              — is a Genie agent configured?
GET  /api/v1/genie/spaces                              — Genie agents the app can use
POST /api/v1/genie/data-space                          — Genie agent scoped to a catalog / schema / table
POST /api/v1/genie/chat                                — ask (or follow up); returns ids immediately
GET  /api/v1/genie/chat/{conversation_id}/{message_id} — poll status / answer / SQL / rows
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.database import get_db
from app.models.user import User
from app.services import genie_chat_service
from app.services.audit_helper import emit_audit_event
from app.services.genie_chat_service import GenieChatError

logger = logging.getLogger(__name__)
router = APIRouter()

# Genie answers from the app's service principal and cannot apply DataOne's per-role PII masking,
# so the read-only "viewer" role is excluded.
genie_roles = require_role("admin", "analyst", "editor")


class GenieChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    space_id: Optional[str] = None
    conversation_id: Optional[str] = None


class DataSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(alias="schema", min_length=1, max_length=255)
    table: Optional[str] = Field(default=None, max_length=255)


def _fail(exc: Exception, what: str) -> HTTPException:
    if isinstance(exc, GenieChatError):
        return HTTPException(status_code=400, detail=str(exc))
    logger.error("[genie_chat] %s failed: %s", what, exc, exc_info=True)
    return HTTPException(status_code=502, detail=f"Genie {what} failed: {exc}")


@router.get("/status")
def genie_status(_user: User = Depends(genie_roles)):
    return genie_chat_service.status()


@router.get("/spaces")
def genie_spaces(_user: User = Depends(genie_roles)):
    try:
        return {"spaces": genie_chat_service.list_spaces(), **genie_chat_service.status()}
    except Exception as exc:
        raise _fail(exc, "space listing") from exc


@router.post("/data-space")
def genie_data_space(req: DataSpaceRequest, db: Session = Depends(get_db), user: User = Depends(genie_roles)):
    """Genie agent over the selected data (created on first use, reused after)."""
    try:
        space = genie_chat_service.ensure_data_space(req.catalog, req.schema_name, req.table)
    except Exception as exc:
        raise _fail(exc, "data scope") from exc
    if space["created"]:
        emit_audit_event(
            db, event_type="genie.data_space_created", actor=user.email, module="genie_chat",
            target_type="genie_space", target_id=space["space_id"], summary=space["title"], outcome="success",
            metadata={"tables": space["tables"]},
        )
        db.commit()
    return space


@router.post("/chat", status_code=202)
def genie_ask(req: GenieChatRequest, db: Session = Depends(get_db), user: User = Depends(genie_roles)):
    try:
        started = genie_chat_service.start_chat(req.question.strip(), req.space_id, req.conversation_id)
    except Exception as exc:
        raise _fail(exc, "request") from exc
    emit_audit_event(
        db, event_type="genie.question_asked", actor=user.email, module="genie_chat",
        target_type="genie_space", target_id=started["space_id"], summary=req.question[:200], outcome="success",
        metadata={"conversation_id": started["conversation_id"], "message_id": started["message_id"],
                  "follow_up": bool(req.conversation_id)},
    )
    db.commit()
    return started


@router.get("/chat/{conversation_id}/{message_id}")
def genie_result(
    conversation_id: str, message_id: str,
    space_id: Optional[str] = Query(default=None),
    _user: User = Depends(genie_roles),
):
    try:
        return genie_chat_service.get_result(space_id, conversation_id, message_id)
    except Exception as exc:
        raise _fail(exc, "result") from exc
