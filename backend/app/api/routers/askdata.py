import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.api.routers.auth import get_current_user
from app.core.database import get_db
from app.models.chat_session import ChatMessage
from app.models.connection import DBConnection
from app.models.user import User
from app.schemas.askdata import (
    AskDataAskRequest,
    AskDataAskResponse,
    ChatMessageEntry,
    SessionMessagesResponse,
)
from app.services import askdata_pipeline_service
from app.services.schema_service import SchemaService
from app.services.diff_service import DiffService
from app.services.security_service import SecurityService
from app.services.ai_service import AIService
from app.services.askdata_service import AskDataService
from app.services.audit_helper import emit_audit_event
from app.tasks.ai_tasks import nl2sql_task

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class NL2SQLRequest(BaseModel):
    connection_id: int
    question: str
    execute: bool = False  # noqa: F841 — accepted for API stability


@router.post("/ask", response_model=AskDataAskResponse)
def ask(
    req: AskDataAskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Conversational NL-to-SQL turn (ADB-T1/T2/T3/T5).

    Grounds generation in the Schema Intel catalog, executes read-only
    (refuses anything the statement classifier doesn't call SELECT), masks
    PII columns for the viewer role, and persists both sides of the
    exchange to the session's chat history for follow-up context.
    """
    conn = db.query(DBConnection).filter(DBConnection.id == req.connection_id).first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    session_id = req.session_id or str(uuid.uuid4())
    prior = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    if prior and any(message.connection_id != conn.id for message in prior):
        raise HTTPException(
            status_code=409,
            detail="Session belongs to a different connection; start a new conversation",
        )
    history = [{"role": m.role, "content": m.content} for m in prior]

    result = askdata_pipeline_service.ask(
        db, conn, req.question, user.role, history,
        actor=user.email, session_id=session_id, user_llm_model=user.llm_model,
        table_name=req.table_name
    )

    db.add(ChatMessage(
        session_id=session_id, role="user", content=req.question, connection_id=conn.id,
    ))
    assistant_content = result.get("summary") or result.get("error") or "No response generated."
    db.add(ChatMessage(
        session_id=session_id, role="assistant", content=assistant_content,
        connection_id=conn.id, sql_text=result.get("sql"), row_count=result.get("row_count"),
    ))

    # Intent gate (agentic_dba_tasks #1): audit the classification itself so
    # its accuracy can be reviewed from the audit log, not just app logs.
    emit_audit_event(
        db,
        event_type="askdata.intent_classified",
        actor=user.email,
        module="askdata",
        target_type="connection",
        target_id=conn.id,
        target_name=conn.name,
        summary=f"intent={result.get('intent')} for: {req.question[:150]}",
        outcome="success",
        metadata={
            "intent": result.get("intent"),
            "confidence": result.get("intent_confidence"),
            "matched_signal": result.get("intent_signal"),
        },
    )

    outcome = "failure" if result.get("error") else "success"
    emit_audit_event(
        db,
        event_type="askdata.question_answered",
        actor=user.email,
        module="askdata",
        target_type="connection",
        target_id=conn.id,
        target_name=conn.name,
        summary=req.question[:200],
        outcome=outcome,
        metadata={
            "question": req.question,
            "sql": result.get("sql"),
            "grounded": result.get("grounded"),
            "masked_columns": result.get("masked_columns"),
            "row_count": result.get("row_count"),
            "error": result.get("error"),
        },
    )
    db.commit()

    return AskDataAskResponse(session_id=session_id, **result)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(
    session_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return SessionMessagesResponse(
        session_id=session_id,
        messages=[ChatMessageEntry.model_validate(r) for r in rows],
    )


@router.post("/chat")
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """Chat with AskData AI about database issues, needs, and challenges."""
    session_id = req.session_id or str(uuid.uuid4())

    # Build comprehensive context from all connections
    context = _build_full_context(db)

    try:
        response = AskDataService.chat(
            message=req.message,
            session_id=session_id,
            context=context,
            db=db,
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AskData chat failed: {str(e)}")


@router.post("/nl2sql")
def nl2sql(req: NL2SQLRequest, db: Session = Depends(get_db)):
    """Enqueue an NL→SQL translation job and return its Celery task id."""
    conn = db.query(DBConnection).filter(DBConnection.id == req.connection_id).first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    task = nl2sql_task.delay(
        connection_id=req.connection_id,
        question=req.question,
        execute=req.execute,
    )
    return {"task_id": task.id, "status": "PENDING"}


@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)):
    """Get contextual question suggestions based on current database state."""
    context = _build_full_context(db)
    suggestions = AskDataService.get_suggestions(context)
    return {"suggestions": suggestions}


@router.delete("/session/{session_id}")
def clear_session(session_id: str, db: Session = Depends(get_db)):
    """Clear a chat session's history."""
    AskDataService.clear_session(session_id, db=db)
    return {"status": "cleared", "session_id": session_id}


def _build_full_context(db: Session) -> Dict[str, Any]:
    """Build comprehensive context from all connected databases."""
    connections = (
        db.query(DBConnection)
        .filter(DBConnection.is_deleted == False)  # noqa: E712
        .all()
    )

    schemas: Dict[str, Any] = {}
    classifications: Dict[str, Any] = {}
    diffs: Dict[str, Any] = {}
    matches: Dict[str, Any] = {}

    for conn in connections:
        try:
            schema = SchemaService.get_full_schema(conn)
            schemas[conn.name] = schema
            classifications[conn.name] = SecurityService.classify_schema(schema)
        except Exception as e:
            logger.warning("Failed to load schema for connection '%s': %s", conn.name, e)

    conn_list = list(connections)
    for i in range(len(conn_list)):
        for j in range(i + 1, len(conn_list)):
            try:
                s1 = schemas.get(conn_list[i].name, {})
                s2 = schemas.get(conn_list[j].name, {})
                diff_result = DiffService.compare_schemas(s1, s2)
                diff_key = f"{conn_list[i].name} ↔ {conn_list[j].name}"
                diffs[diff_key] = diff_result

                for src_table in s1:
                    for tgt_table in s2:
                        try:
                            match_result = AIService.match_schemas(
                                source_name=src_table,
                                source_schema=s1[src_table],
                                target_name=tgt_table,
                                target_schema=s2[tgt_table],
                            )
                            match_key = f"{src_table} → {tgt_table}"
                            matches[match_key] = match_result
                        except Exception as e:
                            logger.warning("AI match failed for %s → %s: %s", src_table, tgt_table, e)
            except Exception as e:
                logger.warning("Diff failed for '%s' ↔ '%s': %s", conn_list[i].name, conn_list[j].name, e)

    return {
        "schemas": schemas,
        "classifications": classifications,
        "diffs": diffs,
        "matches": matches,
        "connection_count": len(connections),
    }
