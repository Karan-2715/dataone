"""Conversational NL-to-SQL pipeline for AskData Bot (ADB-T1/T2/T3/T5).

Grounds generation in the persisted Schema Intel catalog (falls back to
live schema introspection if the connection hasn't been scanned yet — see
_ground_schema), reuses NL2SQLService for the actual generation logic
(Ollama-or-template — shared with Query Studio's legacy NL2SQL surface,
not duplicated here), then enforces read-only execution via
statement_classifier (also shared with Query Studio: AskData must NEVER
write, unlike Query Studio's gated write path) and masks PII columns for
the viewer role using SecurityService's existing keyword classifier.

Conversation context (ADB-T5) is folded into the question text sent to
NL2SQLService.generate_sql rather than passed as a separate parameter —
that function doesn't have a history parameter, and this is the smallest
way to give the LLM path prior turns without forking it. Known tradeoff:
generate_sql's fast-path template matching (`pattern in question.lower()`)
also sees the history-prefixed text, so if a *prior* turn happened to
contain a fast-path phrase like "show all tables", a fresh question could
misfire down that path. Judged low-risk (those are quite specific idioms a
user wouldn't casually restate) rather than worth forking the shared
generation function.

Summarization is deterministic (row-count / single-value framing), not
another LLM call — keeps this path fast and testable, matching Autopilot's
precedent of avoiding LLM calls where a rule-based answer is sufficient.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.services.dba_intent_classifier import classify_intent
from app.services.nl2sql_service import NL2SQLService
from app.services.schema_catalog_service import SchemaCatalogService
from app.services.schema_service import SchemaService, get_connector
from app.services.security_service import SecurityService
from app.services.statement_classifier import StatementType, classify

logger = logging.getLogger(__name__)

# Roles that get PII columns redacted in results — mirrors the "viewer is
# the most-restricted role" convention already established for the
# dashboard (viewer role masking).
PII_MASK_ROLES = {"viewer"}
MAX_HISTORY_MESSAGES = 6


def _ground_schema(db: Session, connection: DBConnection, table_name: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Returns (schema_context, grounded).

    grounded=False means the connection hasn't been scanned into the Schema
    Intel catalog yet, so this fell back to live introspection.
    """
    schema = {}
    grounded = False
    catalog_tables = SchemaCatalogService.get_catalog(db, connection.id)
    if catalog_tables:
        schema = {
            t.table_name: [
                {
                    "name": c.column_name,
                    "type": c.data_type,
                    "nullable": c.nullable,
                    "primary_key": c.is_primary_key,
                }
                for c in t.columns
            ]
            for t in catalog_tables
        }
        grounded = True
    else:
        try:
            schema = SchemaService.get_full_schema(connection)
        except Exception as exc:
            logger.warning("AskData: live schema fallback failed for connection %s: %s", connection.id, exc)
            return {}, False

    if table_name and schema:
        if table_name in schema:
            schema = {table_name: schema[table_name]}
        else:
            for t_name in schema.keys():
                if t_name.endswith("." + table_name.split(".")[-1]) or table_name.endswith(t_name):
                    schema = {t_name: schema[t_name]}
                    break

    return schema, grounded


def _augment_with_history(question: str, history: List[Dict[str, str]]) -> str:
    if not history:
        return question
    recent = history[-MAX_HISTORY_MESSAGES:]
    context_lines = [f"{h['role']}: {h['content']}" for h in recent]
    return (
        "Given this recent conversation:\n" + "\n".join(context_lines) +
        f"\n\nNow answer this follow-up question: {question}"
    )


def _pii_columns(schema: Dict[str, Any], tables_referenced: Optional[List[str]]) -> List[str]:
    tables = tables_referenced or list(schema.keys())
    pii_cols: List[str] = []
    for t in tables:
        for col in schema.get(t, []):
            name = col["name"] if isinstance(col, dict) else str(col)
            if SecurityService.classify_column(name).get("level") == "High":
                pii_cols.append(name)
    return pii_cols


def _summarize_report(report_type: str, results: Any) -> Optional[str]:
    if report_type == "pii_scan":
        n = len(results) if isinstance(results, list) else 0
        return f"Found {n} potentially sensitive column(s)."
    if report_type == "health":
        score = (results or {}).get("health_score")
        return f"Database health score: {score}%." if score is not None else None
    return None


def _summarize_rows(rows: List[Dict[str, Any]]) -> str:
    n = len(rows)
    if n == 0:
        return "No rows matched your question."
    if n == 1 and len(rows[0]) == 1:
        return f"The answer is {next(iter(rows[0].values()))}."
    return f"Found {n} row{'s' if n != 1 else ''}."


def _dispatch_plan_generation(plan_id: int) -> None:
    """Celery dispatch, separated so tests can run generation inline."""
    from app.tasks.agentic_dba_tasks import generate_plan_task
    generate_plan_task.delay(plan_id)


# ── external_action intent handling (aci_integration_tasks #4) ───────────

import re as _re

# A Slack channel reference must start with a letter — bare "#500" is an
# issue/PR number, not a channel, and must not be mistaken for one.
_CHANNEL_RE = _re.compile(r"#[A-Za-z][\w-]*")
_EMAIL_RE = _re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_TICKET_NOUN_RE = _re.compile(
    r"\b(ticket|issue|jira|linear|github|pull\s+request|pr|bug)\b", _re.IGNORECASE)
_TICKET_VERB_RE = _re.compile(r"\b(open|file|raise|create|log|submit)\b", _re.IGNORECASE)


def _is_ticket_request(question: str) -> bool:
    """A ticket request needs both a ticketing noun AND a creation verb
    ("open a jira ticket", "file a github issue"). This keeps an incidental
    ticketing word ("email bob@x.com about the issue") from being routed as a
    ticket, while making an explicit ticket request win over a cc'd email
    address or an issue number that also appears in the text."""
    return bool(_TICKET_NOUN_RE.search(question) and _TICKET_VERB_RE.search(question))


def _resolve_external_target(question: str) -> Optional[Dict[str, Any]]:
    """Map a request to a governed action type + payload, or None when the
    target isn't resolvable (→ clarifying question, never a guess).

    Precedence: an explicit ticket request first (so "open a jira ticket …
    cc bob@corp.com" or "open a github issue for bug #500" route to a ticket,
    not to email/a #500 channel), then an email address, then a #channel, then
    a weak ticketing-word fallback."""
    if _is_ticket_request(question):
        return {
            "action_type": "external_ticket_create",
            "payload": {"title": question[:80], "body": question},
            "subject": f"ticket:{question[:60]}",
        }
    email = _EMAIL_RE.search(question)
    if email:
        return {
            "action_type": "external_email_send",
            "payload": {"to": email.group(0), "subject": question[:80],
                        "body": question},
            "subject": f"email:{email.group(0)}",
        }
    channel = _CHANNEL_RE.search(question)
    if channel:
        return {
            "action_type": "external_message_send",
            "payload": {"destination": channel.group(0), "body": question},
            "subject": f"channel:{channel.group(0)}",
        }
    if _TICKET_NOUN_RE.search(question):
        return {
            "action_type": "external_ticket_create",
            "payload": {"title": question[:80], "body": question},
            "subject": f"ticket:{question[:60]}",
        }
    return None


def _handle_external_action(db: Session, question: str, actor: str,
                            connection: DBConnection,
                            result: Dict[str, Any]) -> Dict[str, Any]:
    """Route an external_action request through ACI tool discovery + the
    governance registry's approval queue. Never NL2SQL, never ungated
    execution — even the discovery step degrades fast and clearly when ACI
    is down (circuit breaker), leaving the rest of AskData untouched."""
    from app.services.aci_client_service import (
        AciNotConfigured, CircuitBreakerOpen, aci_client,
    )
    from app.services.audit_helper import emit_audit_event
    from app.services.autopilot_service import AutopilotService

    result["method"] = "intent_gate"

    target = _resolve_external_target(question)
    if target is None:
        result["needs_clarification"] = True
        result["summary"] = (
            "This looks like a request to act on an external tool, but I can't "
            "tell where it should go. Name a destination — an email address, a "
            "#channel, or 'open a ticket/issue' — and I'll queue it for approval."
        )
        return result

    try:
        tools = aci_client.search_tools(question)
    except AciNotConfigured:
        result["error"] = ("External actions aren't available: the ACI integration "
                           "isn't configured (ACI_API_KEY is unset).")
        return result
    except CircuitBreakerOpen:
        result["error"] = ("External actions are temporarily unavailable — the ACI "
                           "service is unreachable (circuit open). Everything else "
                           "keeps working; try again shortly.")
        return result
    except Exception as exc:
        logger.warning("[askdata] external_action tool discovery failed: %s", exc)
        result["error"] = f"External tool discovery failed: {exc}"
        return result

    rec, created = AutopilotService.upsert_recommendation(
        db,
        action_type=target["action_type"],
        subject=target["subject"],
        payload=target["payload"],
        rationale={
            "summary": f"Requested via AskData: {question[:200]}",
            "matched_tools": [t.get("name") or t.get("function_name")
                              for t in tools[:3]],
        },
        confidence=80.0,
        created_by=actor,
    )
    emit_audit_event(
        db, event_type="aci.external_action_requested", actor=actor,
        module="aci_integration", target_type="recommendation", target_id=rec.id,
        summary=f"{target['action_type']}: {question[:150]}",
        outcome="success",
        metadata={"action_type": target["action_type"],
                  "recommendation_id": rec.id, "created": created,
                  "matched_tools": [t.get("name") or t.get("function_name")
                                    for t in tools[:3]]},
    )
    result["recommendation_id"] = rec.id
    result["summary"] = (
        f"This is an external action ({target['action_type']}). It's been queued "
        f"for approval in AI Autopilot (recommendation #{rec.id}) — external "
        f"side effects always require explicit human approval before executing."
    )
    return result


def _handle_platform_insight(db: Session, question: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Enterprise v2, E09 — the copilot's own new skill, not a wrapper
    around an existing engine: answers questions about the platform's
    governance/risk/quality metadata by calling E05/E06/E08's real
    services directly, never NL2SQL (which would generate SQL against
    the wrong database) and never an LLM guess. Grounded, deterministic,
    read-only."""
    from app.services.dq_service import DataQualityService
    from app.services.governance_service import GovernanceService
    from app.services.risk_service import RiskAggregationService

    q = question.lower()
    result["method"] = "platform_insight"
    parts = []
    insight: Dict[str, Any] = {}

    # Word-boundary matching (build-validation B-E09-13): plain substring
    # checks would false-positive on a table/column literally named e.g.
    # "unobtainium_risk_factors" or "quality_assurance" — this keyword
    # detection only decides which platform data to load, not routing, but
    # loading unnecessary data for a false positive is still wasted work.
    wants_risk = bool(_re.search(r"\b(risk|risks|critical)\b", q))
    wants_dq = bool(_re.search(r"\b(quality|dq)\b", q))
    wants_governance = bool(_re.search(r"\b(governance|compliance)\b", q))

    # build-validation B-E09-11: only load the risk register — the most
    # expensive adapter, with live connector round-trips via
    # _mapping_diff_findings — when the question actually asked about risk.
    # The previous `or not (wants_dq or wants_governance)` fallback loaded
    # it by default for ANY platform-insight question that didn't mention
    # quality/governance, which is wasteful and, with every pattern in
    # _PLATFORM_INSIGHT_PATTERNS containing one of these three keyword
    # groups, was also unreachable as a genuine "nothing else matched"
    # fallback — it only ever fired as a needless default.
    if wants_risk:
        register = RiskAggregationService(db).get_register()
        insight["risk"] = {"total": register["total"], "by_severity": register["facets"]["by_severity"]}
        critical = register["facets"]["by_severity"].get("critical", 0)
        high = register["facets"]["by_severity"].get("high", 0)
        parts.append(
            f"{register['total']} open risk finding(s) — {critical} critical, {high} high."
            if register["total"] else "No open risk findings."
        )

    if wants_dq:
        dq = DataQualityService(db).get_all_scorecards_summary()
        insight["data_quality"] = dq
        parts.append(
            f"Data quality score is {dq['overall']}% ({dq['profiled_column_count']}/{dq['column_count']} columns profiled)."
            if dq["overall"] is not None else "No profiled columns yet — data quality score is not available."
        )

    if wants_governance:
        gov = GovernanceService(db).get_score()
        insight["governance"] = gov
        parts.append(
            f"Governance coverage score is {gov['score']}% "
            f"(owner {gov['owner_coverage']}%, classification {gov['classification_coverage']}%, "
            f"retention {gov['retention_coverage']}%)."
            if gov["table_count"] else "No cataloged tables to govern yet."
        )

    result["platform_insight"] = insight
    result["summary"] = " ".join(parts)
    return result


def ask(
    db: Session,
    connection: DBConnection,
    question: str,
    role: str,
    history: List[Dict[str, str]],
    actor: str = "unknown",
    session_id: Optional[str] = None,
    user_llm_model: Optional[str] = None,
    table_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one conversational turn: classify intent, ground, generate, classify, execute, mask, summarize."""
    # Intent gate (agentic_dba_tasks #1): classify BEFORE grounding/generation
    # on the raw question (not the history-augmented text — prior turns must
    # not tip a fresh read question into the build bucket or vice versa).
    intent = classify_intent(question, user_llm_model=user_llm_model)
    logger.info("[askdata] stage=intent_classified intent=%s confidence=%s", intent.intent, intent.confidence)

    if intent.intent == "platform_insight":
        # E09: this is a question about the platform's own governance/risk/
        # quality metadata, not the connected source — skip schema grounding
        # entirely, it isn't needed and isn't relevant.
        result = {
            "sql": None, "grounded": False, "confidence": 0, "method": "none",
            "executed": False, "columns": [], "rows": [], "row_count": 0,
            "masked_columns": [], "summary": None, "warnings": [], "error": None,
            "intent": intent.intent, "intent_confidence": intent.confidence,
            "intent_signal": intent.matched_signal,
            "plan_id": None, "needs_clarification": False,
            "recommendation_id": None, "platform_insight": None,
        }
        return _handle_platform_insight(db, question, result)

    schema, grounded = _ground_schema(db, connection, table_name)
    result: Dict[str, Any] = {
        "sql": None, "grounded": grounded, "confidence": 0, "method": "none",
        "executed": False, "columns": [], "rows": [], "row_count": 0,
        "masked_columns": [], "summary": None, "warnings": [], "error": None,
        "intent": intent.intent, "intent_confidence": intent.confidence,
        "intent_signal": intent.matched_signal,
        "plan_id": None, "needs_clarification": False,
        "recommendation_id": None, "platform_insight": None,
    }

    if intent.intent == "external_action":
        # aci_integration_tasks #4: route to ACI tool discovery + the
        # governed approval queue — never NL2SQL, never ungated execution.
        return _handle_external_action(db, question, actor, connection, result)

    if intent.intent == "schema_design":
        # Do NOT attempt NL2SQL — the old fallback returned a meaningless
        # `SELECT * FROM {first_table} LIMIT 50` for exactly this class of
        # request. Route to the Agentic DBA planning engine instead.
        result["method"] = "intent_gate"

        # Clarifying-question flow (agentic_dba_tasks #10): a connection
        # with no Schema Intel catalog can't ground a plan — ask, don't
        # guess from live introspection alone.
        if not grounded:
            result["needs_clarification"] = True
            result["summary"] = (
                f"This looks like a schema design request, but connection "
                f"'{connection.name}' hasn't been scanned into the Schema Intel "
                f"catalog yet — a plan grounded in profiling needs that first. "
                f"Scan it (Schema Intel → Scan catalog, ideally Profile columns "
                f"too), then re-ask."
            )
            return result

        from app.services.agentic_dba_engine import create_plan
        plan = create_plan(
            db, question=question, connection_id=connection.id,
            session_id=session_id, actor=actor,
        )
        _dispatch_plan_generation(plan.id)
        result["plan_id"] = plan.id
        result["summary"] = (
            "This is a schema/pipeline design request — generating a design plan "
            "for review (proposed tables, data-quality rules, transformations, "
            "and DDL). Nothing is created or executed without your explicit "
            "approval."
        )
        return result

    if intent.intent == "ambiguous":
        # Low-confidence classification: if the question names a known table,
        # today's read-query behavior is a reasonable guess; otherwise ask
        # instead of guessing (agentic_dba_tasks #10). Threshold judgment
        # call flagged in the epic INDEX — tune after real usage.
        question_lower = question.lower()
        mentions_table = any(t.lower() in question_lower for t in schema)
        if not mentions_table:
            result["method"] = "intent_gate"
            result["needs_clarification"] = True
            result["summary"] = (
                "I couldn't tell whether you want to query existing data or "
                "design something new. Try a question like 'show me all rows in "
                "<table>', or a design request like 'create a target schema for "
                "<domain> based on profiling'."
            )
            return result
    if not schema:
        result["error"] = "No schema available for this connection — try scanning it first."
        return result

    augmented = _augment_with_history(question, history)
    gen = NL2SQLService.generate_sql(augmented, schema, connection.type, user_llm_model=user_llm_model)
    result["sql"] = gen.get("sql")
    result["confidence"] = gen.get("confidence", 0)
    result["method"] = gen.get("method", "unknown")

    if gen.get("blocked"):
        result["error"] = gen.get("reason", "Query blocked for safety.")
        return result

    # Pre-computed report types (health/pii_scan) aren't a SQL statement to
    # execute — they're already the answer.
    if gen.get("report_type"):
        rows = gen.get("results")
        result["executed"] = True
        result["rows"] = rows if isinstance(rows, list) else []
        result["row_count"] = len(result["rows"])
        result["summary"] = _summarize_report(gen["report_type"], rows)
        return result

    sql = gen.get("sql") or ""
    classified = classify(sql)
    SAFE_TYPES = {StatementType.SELECT, StatementType.SHOW}
    if classified.type not in SAFE_TYPES:
        result["error"] = "Generated SQL was not a read-only statement — refusing to execute for safety."
        result["warnings"].append(f"Classified as {classified.type.value}.")
        return result

    connector = get_connector(connection)
    try:
        exec_result = NL2SQLService.execute_safe_query(connector, sql)
    finally:
        connector.close()

    if exec_result.get("error"):
        result["error"] = exec_result["error"]
        return result

    rows = exec_result.get("results", [])
    masked_columns = _pii_columns(schema, classified.tables_referenced) if role in PII_MASK_ROLES else []
    if masked_columns and rows:
        rows = [
            {k: ("***REDACTED***" if k in masked_columns else v) for k, v in row.items()}
            for row in rows
        ]

    result["executed"] = True
    result["columns"] = list(rows[0].keys()) if rows else []
    result["rows"] = rows
    result["row_count"] = len(rows)
    result["masked_columns"] = masked_columns
    result["summary"] = _summarize_rows(rows)
    return result
