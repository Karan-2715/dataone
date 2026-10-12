"""Tests for classification + confidence scoring (Task #3, FR3/AC2) and
manual override (Task #7, FR5/FR8)."""
import pytest
from fastapi import HTTPException

from app.services.security_service import SecurityService
from app.services.schema_catalog_service import SchemaCatalogService


def test_exact_keyword_match_has_higher_confidence_than_substring():
    exact = SecurityService.classify_column("email")
    substring = SecurityService.classify_column("email_backup_unused")
    assert exact["label"] == "PII"
    assert substring["label"] == "PII"
    assert exact["confidence"] == 0.9
    assert substring["confidence"] == 0.6
    assert exact["method"] == "keyword"


def test_no_keyword_match_classifies_public():
    result = SecurityService.classify_column("widget_count")
    assert result["label"] == "Public"
    assert result["method"] == "keyword"


def test_value_pattern_overrides_misleading_column_name():
    """AC2's example: a column named 'contact' (no keyword match) whose
    sampled values are email-formatted must classify as PII via content,
    not name."""
    samples = ["alice@example.com", "bob@example.com", "carol@example.com",
               "not-an-email", "dave@example.com"]
    result = SecurityService.classify_column("contact", sample_values=samples)
    assert result["label"] == "PII"
    assert result["method"] == "value_pattern"
    assert result["confidence"] == pytest.approx(0.8)  # 4/5 matched


def test_value_pattern_below_threshold_falls_back_to_keyword():
    """Only 1/3 values match the email pattern — below the 0.6 threshold —
    so classification falls back to the name-based heuristic."""
    samples = ["alice@example.com", "not-an-email", "also not one"]
    result = SecurityService.classify_column("misc_field", sample_values=samples)
    assert result["method"] == "keyword"
    assert result["label"] == "Public"


def test_value_pattern_empty_samples_falls_back_to_keyword():
    result = SecurityService.classify_column("email", sample_values=[])
    assert result["method"] == "keyword"


def test_value_pattern_detects_phone():
    samples = ["+1-555-0101", "+1-555-0102", "555-0103", "garbage!!"]
    result = SecurityService.classify_column("contact_number_2", sample_values=samples)
    assert result["method"] == "value_pattern"
    assert result["value_pattern_kind"] == "phone"


# ── Manual override (Task #7) ─────────────────────────────────────────


def _seed_column(db, physical_sqlite_connection):
    from app.models.schema_catalog import CatalogColumn
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    return db.query(CatalogColumn).filter(CatalogColumn.column_name == "contact").first()


def test_override_classification_creates_row(db, physical_sqlite_connection):
    col = _seed_column(db, physical_sqlite_connection)
    row = SchemaCatalogService.override_classification(
        db, col.id, label="PII", level="High", actor="admin@test.local",
    )
    assert row.method == "manual_override"
    assert row.confidence == 1.0
    assert row.overridden_by == "admin@test.local"
    assert row.overridden_at is not None


def test_override_classification_missing_column_404(db):
    with pytest.raises(HTTPException) as e:
        SchemaCatalogService.override_classification(
            db, 99999, label="PII", level="High", actor="admin@test.local",
        )
    assert e.value.status_code == 404


def test_override_classification_emits_audit_event(db, physical_sqlite_connection):
    from app.models.audit import AuditLog
    col = _seed_column(db, physical_sqlite_connection)
    SchemaCatalogService.override_classification(
        db, col.id, label="Sensitive", level="Medium", actor="admin@test.local",
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "classification_overridden")
        .first()
    )
    assert audit is not None
    assert audit.payload["after"]["label"] == "Sensitive"


def test_reprofiling_does_not_clobber_manual_override(db, physical_sqlite_connection):
    """A human decision is a decision, not a cache — re-running the
    profiling/classification task must not silently overwrite an override."""
    from unittest.mock import patch
    from app.models.schema_catalog import ColumnClassification
    import app.tasks.schema_intel_tasks as sit_module

    col = _seed_column(db, physical_sqlite_connection)
    SchemaCatalogService.override_classification(
        db, col.id, label="Public", level="Low", actor="admin@test.local",
    )

    class _NoCloseSession:
        def __init__(self, s):
            self._s = s

        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    with patch("app.core.database.SessionLocal", lambda: _NoCloseSession(db)):
        sit_module.profile_column_task(
            connection_id=physical_sqlite_connection.id,
            table_name="people", column_id=col.id, column_name="contact",
        )

    classification = (
        db.query(ColumnClassification)
        .filter(ColumnClassification.column_id == col.id)
        .first()
    )
    assert classification.method == "manual_override"
    assert classification.label == "Public"
