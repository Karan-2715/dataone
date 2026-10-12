import re
from typing import List, Dict, Any, Optional

# Value-pattern regexes (schema_intel_tasks #3, AC2: classify by column
# *content*, not just name). Kept intentionally simple/conservative —
# these are heuristics, not a validator; a match rate threshold (not a
# single hit) drives the decision so one stray value doesn't misclassify
# a whole column.
_VALUE_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "phone": re.compile(r"^\+?[\d\-\(\)\s]{7,15}$"),
    "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    "credit_card": re.compile(r"^\d{4}[\-\s]?\d{4}[\-\s]?\d{4}[\-\s]?\d{4}$"),
}
_VALUE_PATTERN_MATCH_THRESHOLD = 0.6  # >=60% of sampled non-null values must match


class SecurityService:
    @staticmethod
    def classify_column_by_value_pattern(
        sample_values: List[Any],
    ) -> Optional[Dict[str, Any]]:
        """Classify a column by inspecting its *content* rather than its
        name (AC2: a column named 'contact' whose values are email-formatted
        should classify as PII/Email, not 'Public'). Returns None if no
        pattern clears the match-rate threshold — the caller falls back to
        the name-based classification. ``sample_values`` is the in-memory
        list from BaseConnector.profile_column(); never persisted here or
        by the caller (schema_intel_tasks Task #8 Decision 1)."""
        non_null = [str(v) for v in sample_values if v is not None and str(v).strip()]
        if not non_null:
            return None

        best_kind: Optional[str] = None
        best_rate = 0.0
        for kind, pattern in _VALUE_PATTERNS.items():
            matches = sum(1 for v in non_null if pattern.match(v.strip()))
            rate = matches / len(non_null)
            if rate > best_rate:
                best_rate = rate
                best_kind = kind

        if best_kind is None or best_rate < _VALUE_PATTERN_MATCH_THRESHOLD:
            return None

        return {
            "label": "PII",
            "level": "High",
            "policy": "Mask on Export",
            "color": "red",
            "confidence": round(best_rate, 2),
            "method": "value_pattern",
            "value_pattern_kind": best_kind,
        }

    @staticmethod
    def classify_column(
        column_name: str, sample_values: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Classifies a column into PII/Sensitive/Public levels, with a
        confidence score (schema_intel_tasks #3, FR3).

        If ``sample_values`` is provided (from a recent profiling run), a
        value-pattern match takes priority over the name-based heuristic —
        content is a stronger signal than naming convention (AC2). Falls
        back to keyword matching on the column name otherwise.
        """
        if sample_values:
            value_result = SecurityService.classify_column_by_value_pattern(sample_values)
            if value_result is not None:
                return value_result

        name = column_name.lower()

        high_risk_keywords = ["email", "phone", "number", "ssn", "password", "cc", "credit", "card"]
        medium_risk_keywords = ["name", "zip", "city", "address", "state", "birth", "date_of"]

        # Exact match to a keyword is a stronger signal than the keyword
        # merely appearing as a substring (e.g. "email" vs
        # "email_backup_unused") — confidence reflects that distinction.
        if name in high_risk_keywords:
            confidence = 0.9
        elif any(w in name for w in high_risk_keywords):
            confidence = 0.6
        else:
            confidence = None

        if confidence is not None:
            return {
                "label": "PII",
                "level": "High",
                "policy": "Mask on Export",
                "color": "red",
                "confidence": confidence,
                "method": "keyword",
            }

        if name in medium_risk_keywords:
            confidence = 0.9
        elif any(w in name for w in medium_risk_keywords):
            confidence = 0.6
        else:
            confidence = None

        if confidence is not None:
            return {
                "label": "Sensitive",
                "level": "Medium",
                "policy": "Restrict Access",
                "color": "amber",
                "confidence": confidence,
                "method": "keyword",
            }

        return {
            "label": "Public",
            "level": "Low",
            "policy": "No Restrictions",
            "color": "green",
            "confidence": 0.5,  # No PII/sensitive marker found — not a confident negative
            "method": "keyword",
        }

    @staticmethod
    def classify_schema(schema_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Applies classification directly across multiple tables structures properly formats results sets.
        """
        classifications = {}
        for table, cols in schema_data.items():
            classifications[table] = []
            for col in cols:
                classifications[table].append({
                    "column": col["name"],
                    "classification": SecurityService.classify_column(col["name"])
                })
        return classifications
