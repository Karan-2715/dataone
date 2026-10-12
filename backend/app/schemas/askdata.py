from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AskDataAskRequest(BaseModel):
    """One conversational NL-to-SQL turn (ADB-T1/T2/T3/T5)."""
    connection_id: int
    question: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    table_name: Optional[str] = None


class AskDataAskResponse(BaseModel):
    session_id: str
    sql: Optional[str] = None
    grounded: bool = False
    confidence: int = 0
    method: str = "unknown"
    executed: bool = False
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    masked_columns: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    # Intent gate (agentic_dba_tasks #1): read_query | schema_design | ambiguous
    intent: str = "read_query"
    intent_confidence: float = 0.0
    # Agentic DBA plan reference (agentic_dba_tasks #3/#6): set when a
    # schema_design turn spawned a plan — the frontend polls
    # GET /agentic-dba/plans/{plan_id} while it generates.
    plan_id: Optional[int] = None
    needs_clarification: bool = False
    # aci_integration_tasks #4: set when an external_action request was
    # queued for approval in the Autopilot queue.
    recommendation_id: Optional[int] = None
    # Enterprise v2, E09: set when intent == "platform_insight" — structured
    # risk/DQ/governance data grounding the `summary` text, for a copilot
    # panel to render richly instead of just showing prose.
    platform_insight: Optional[Dict[str, Any]] = None


class ChatMessageEntry(BaseModel):
    id: int
    role: str
    content: str
    connection_id: Optional[int] = None
    sql_text: Optional[str] = None
    row_count: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[ChatMessageEntry]
