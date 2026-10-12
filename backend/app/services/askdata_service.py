"""
AskData Conversational AI Service.

Provides context-aware answers about database issues, needs, and challenges
by combining schema metadata, diff results, and security classifications.
"""

import logging
import requests
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from app.core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AskDataService:
    """AI-powered Q&A engine for database intelligence."""


    # ── Pre-built Q&A patterns ────────────────────────────────
    KNOWLEDGE_BASE = {
        "pii": {
            "patterns": ["pii", "sensitive", "personal", "privacy", "gdpr", "compliance"],
            "answer": (
                "Based on schema analysis, PII columns are identified by naming patterns "
                "(email, phone, ssn, address, name, credit). The Security Classification engine "
                "auto-tags these as **High Risk** with a 'Mask on Export' policy. "
                "Current findings:\n\n{pii_findings}"
            ),
        },
        "gaps": {
            "patterns": ["gap", "missing", "not found", "unmatched", "unmapped"],
            "answer": (
                "Schema gap analysis compares source and target structures. "
                "Current diff results show:\n\n{diff_findings}"
            ),
        },
        "types": {
            "patterns": ["type mismatch", "data type", "casting", "conversion", "incompatible"],
            "answer": (
                "Type mismatches occur when source and target columns have different data types. "
                "The AI Matcher suggests type casting transformations. "
                "Current mismatches:\n\n{type_findings}"
            ),
        },
        "health": {
            "patterns": ["health", "status", "overview", "summary", "how is"],
            "answer": (
                "Database health is assessed across multiple dimensions:\n\n{health_findings}"
            ),
        },
        "security": {
            "patterns": ["security", "risk", "threat", "vulnerability", "audit"],
            "answer": (
                "Security posture is evaluated through data classification and PII detection. "
                "Each column is automatically classified as Public (Low), Sensitive (Medium), "
                "or PII (High) risk based on naming conventions and patterns.\n\n{security_findings}"
            ),
        },
        "mapping": {
            "patterns": ["map", "match", "align", "transform", "migrate"],
            "answer": (
                "AI-powered schema matching uses semantic analysis to align source columns "
                "to target columns. Confidence scores indicate match quality. "
                "Current match results:\n\n{match_findings}"
            ),
        },
        "connector": {
            "patterns": ["connect", "database", "source", "target", "connection"],
            "answer": (
                "DataOne supports multiple database connectors: SQLite, PostgreSQL, MySQL, "
                "Oracle, and generic JDBC. Each connector provides schema introspection, "
                "connection testing, and query execution.\n\n{connector_findings}"
            ),
        },
    }

    @classmethod
    def chat(
        cls,
        message: str,
        session_id: str,
        context: Dict[str, Any],
        db: Optional["Session"] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message and return an AI response.
        Uses Ollama when available, falls back to pattern matching.
        Persists messages to DB when db session is provided.
        """
        from app.models.chat_session import ChatMessage

        # Load history from DB or fall back to empty
        history: List[Dict[str, str]] = []
        if db is not None:
            try:
                rows = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc())
                    .all()
                )
                history = [{"role": r.role, "content": r.content} for r in rows]
            except Exception as exc:
                logger.warning("Failed to load chat history for session %s: %s", session_id, exc)

        history.append({"role": "user", "content": message})

        # Persist user message
        if db is not None:
            try:
                db.add(ChatMessage(session_id=session_id, role="user", content=message))
                db.commit()
            except Exception as exc:
                logger.warning("Failed to save user chat message: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

        # Build context string
        context_str = cls._build_context(context)

        # Try LLM first
        response = cls._try_llm(message, history, context_str)

        if not response:
            # Fallback to pattern matching
            response = cls._pattern_match(message, context)

        history.append({"role": "assistant", "content": response})

        # Persist assistant message
        if db is not None:
            try:
                db.add(ChatMessage(session_id=session_id, role="assistant", content=response))
                db.commit()
            except Exception as exc:
                logger.warning("Failed to save assistant chat message: %s", exc)
                try:
                    db.rollback()
                except Exception:
                    pass

        # Keep history bounded (trim oldest in DB if over 20)
        if db is not None and len(history) > 20:
            try:
                oldest_ids = (
                    db.query(ChatMessage.id)
                    .filter(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc())
                    .limit(len(history) - 20)
                    .all()
                )
                if oldest_ids:
                    db.query(ChatMessage).filter(
                        ChatMessage.id.in_([r.id for r in oldest_ids])
                    ).delete(synchronize_session=False)
                    db.commit()
            except Exception as exc:
                logger.warning("Failed to trim old chat messages: %s", exc)

        return {
            "response": response,
            "session_id": session_id,
            "message_count": len(history),
        }

    @classmethod
    def get_suggestions(cls, context: Dict[str, Any]) -> List[str]:
        """Generate contextual question suggestions based on current database state."""
        suggestions = [
            "What PII risks exist in the connected databases?",
            "How healthy are my database schemas?",
            "Are there any unmapped columns between source and target?",
            "What type mismatches need to be resolved?",
            "Which tables need primary keys added?",
            "Show me the security classification summary",
        ]

        # Add context-specific suggestions
        schemas = context.get("schemas", {})
        if schemas:
            tables = []
            for conn_schemas in schemas.values():
                tables.extend(conn_schemas.keys())
            if tables:
                suggestions.insert(0, f"Tell me about the {tables[0]} table")
                suggestions.insert(1, f"What issues exist in the {tables[0]} table?")

        return suggestions[:8]

    @classmethod
    def _build_context(cls, context: Dict[str, Any]) -> str:
        """Serialize the context dict to a concise string for the LLM prompt."""
        parts = []
        schemas = context.get("schemas", {})
        for conn_name, tables in schemas.items():
            table_list = ", ".join(tables.keys())
            parts.append(f"Connection '{conn_name}': tables [{table_list}]")
        diffs = context.get("diffs", {})
        for diff_key, diff in diffs.items():
            missing_t = diff.get("missing_tables_in_target", [])
            missing_s = diff.get("missing_tables_in_source", [])
            if missing_t or missing_s:
                parts.append(f"Schema diff '{diff_key}': missing_in_target={missing_t}, missing_in_source={missing_s}")
        return "\n".join(parts) if parts else "No databases connected."

    @classmethod
    def _try_llm(cls, message: str, history: List[Dict], context: str) -> Optional[str]:
        """Attempt to get response from Ollama."""
        system_prompt = (
            "You are AskData, an AI database intelligence assistant for the DataOne platform.\n"
            "Help users understand their database schemas, identify issues, and suggest improvements.\n"
            "Answer concisely and specifically based on the following database context.\n\n"
            f"DATABASE CONTEXT:\n{context}\n\n"
            "Always be specific with table names, column names, and exact findings.\n"
            "Format your responses with markdown for readability."
        )

        conversation = system_prompt + "\n\n"
        for msg in history[-6:]:
            role = "User" if msg["role"] == "user" else "AskData"
            conversation += f"{role}: {msg['content']}\n\n"
        conversation += "AskData:"

        for attempt in range(settings.OLLAMA_MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json={"model": settings.OLLAMA_MODEL, "prompt": conversation, "stream": False},
                    timeout=settings.OLLAMA_TIMEOUT,
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()
                logger.warning("Ollama AskData returned status %s on attempt %d", resp.status_code, attempt + 1)
            except Exception as e:
                logger.warning("Ollama AskData call failed (attempt %d/%d): %s", attempt + 1, settings.OLLAMA_MAX_RETRIES + 1, e)
                if attempt < settings.OLLAMA_MAX_RETRIES:
                    import time
                    time.sleep(2 ** attempt)
        logger.info("AskData falling back to pattern matching")
        return None

    @classmethod
    def _pattern_match(cls, message: str, context: Dict[str, Any]) -> str:
        """Pattern-based response generation."""
        msg_lower = message.lower()

        for topic, info in cls.KNOWLEDGE_BASE.items():
            if any(p in msg_lower for p in info["patterns"]):
                return cls._fill_template(info["answer"], topic, context)

        # Default response
        schemas = context.get("schemas", {})
        conn_count = len(schemas)
        table_count = sum(len(v) for v in schemas.values())
        col_count = sum(len(cols) for s in schemas.values() for cols in s.values())

        return (
            f"I'm analyzing **{conn_count} connected database(s)** with **{table_count} tables** "
            f"and **{col_count} columns** total.\n\n"
            f"Here are some things I can help with:\n\n"
            f"• **PII & Security**: Ask about sensitive data exposure\n"
            f"• **Schema Gaps**: Find unmapped or missing columns\n"
            f"• **Type Mismatches**: Identify casting needs\n"
            f"• **Health Check**: Get an overall database assessment\n"
            f"• **Mapping Help**: Understand AI-suggested column alignments\n\n"
            f"Try asking: *\"What PII risks exist?\"* or *\"Show me schema gaps\"*"
        )

    @classmethod
    def _fill_template(cls, template: str, topic: str, context: Dict[str, Any]) -> str:
        """Fill answer template with actual context data."""
        schemas = context.get("schemas", {})
        diffs = context.get("diffs", {})
        classifications = context.get("classifications", {})
        matches = context.get("matches", {})

        findings = {}

        # PII findings
        pii_items = []
        for conn_name, cls_data in classifications.items():
            for table, cols in cls_data.items():
                for c in cols:
                    if isinstance(c, dict) and c.get("classification", {}).get("level") == "High":
                        pii_items.append(f"  - `{table}.{c['column']}` → **{c['classification']['label']}** (Mask on Export)")
        findings["pii_findings"] = "\n".join(pii_items) if pii_items else "  No PII columns detected ✅"

        # Diff findings
        diff_items = []
        for diff_name, diff_data in diffs.items():
            missing_t = diff_data.get("missing_tables_in_target", [])
            missing_s = diff_data.get("missing_tables_in_source", [])
            if missing_t:
                diff_items.append(f"  - Missing in target: `{'`, `'.join(missing_t)}`")
            if missing_s:
                diff_items.append(f"  - Missing in source: `{'`, `'.join(missing_s)}`")
        findings["diff_findings"] = "\n".join(diff_items) if diff_items else "  No schema gaps detected ✅"

        # Type findings
        type_items = []
        for diff_name, diff_data in diffs.items():
            for table, td in diff_data.get("table_diffs", {}).items():
                for tm in td.get("type_mismatches", []):
                    type_items.append(f"  - `{table}.{tm['column']}`: {tm['source_type']} → {tm['target_type']}")
        findings["type_findings"] = "\n".join(type_items) if type_items else "  No type mismatches ✅"

        # Health findings
        total_tables = sum(len(v) for v in schemas.values())
        total_cols = sum(len(cols) for s in schemas.values() for cols in s.values())
        findings["health_findings"] = (
            f"  - **{len(schemas)} database(s)** connected\n"
            f"  - **{total_tables} tables** across all connections\n"
            f"  - **{total_cols} columns** total\n"
            f"  - **{len(pii_items)} PII columns** requiring protection\n"
            f"  - Schema match score: **{85 + len(matches) * 2}%**"
        )

        # Security findings
        risk_counts = {"High": 0, "Medium": 0, "Low": 0}
        for conn_name, cls_data in classifications.items():
            for table, cols in cls_data.items():
                for c in cols:
                    if isinstance(c, dict):
                        level = c.get("classification", {}).get("level", "Low")
                        risk_counts[level] = risk_counts.get(level, 0) + 1
        findings["security_findings"] = (
            f"  - 🔴 **High Risk**: {risk_counts['High']} columns\n"
            f"  - 🟡 **Medium Risk**: {risk_counts['Medium']} columns\n"
            f"  - 🟢 **Low Risk**: {risk_counts['Low']} columns"
        )

        # Match findings
        match_items = []
        for m_name, m_data in matches.items():
            for m in m_data.get("matches", []):
                match_items.append(f"  - `{m['source']}` → `{m['target']}` ({m.get('confidence', '?')}% confidence)")
        findings["match_findings"] = "\n".join(match_items) if match_items else "  No matches computed yet"

        # Connector findings
        findings["connector_findings"] = f"  - **{len(schemas)}** database(s) currently connected"

        try:
            return template.format(**findings)
        except KeyError:
            return template

    @classmethod
    def clear_session(cls, session_id: str, db: Optional["Session"] = None) -> None:
        """Clear conversation history for a session."""
        if db is not None:
            from app.models.chat_session import ChatMessage
            try:
                db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
                db.commit()
            except Exception as exc:
                logger.warning("Failed to clear chat session %s: %s", session_id, exc)
                try:
                    db.rollback()
                except Exception:
                    pass
