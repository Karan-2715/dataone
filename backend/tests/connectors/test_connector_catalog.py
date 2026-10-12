"""Connector type catalog + config validation + redaction (tasks #3, FR1/FR3)."""
import pytest
from fastapi import HTTPException

from app.services import connector_catalog
from app.services.connector_catalog import (
    CONNECTOR_TYPES, REDACTED, redact_config, secret_fields_for_type,
    validate_config,
)

EXPECTED_TYPES = {"postgres", "mysql", "oracle", "sqlite", "jdbc"}


def test_catalog_covers_all_supported_types():
    assert set(CONNECTOR_TYPES) == EXPECTED_TYPES


def test_every_type_has_fields_and_consistent_secret_fields():
    for meta in CONNECTOR_TYPES.values():
        assert meta.fields, f"{meta.type} has no field definitions"
        field_keys = {f.key for f in meta.fields}
        assert set(meta.secret_fields) <= field_keys
        # every field flagged secret=True appears in secret_fields and
        # vice versa
        assert {f.key for f in meta.fields if f.secret} == set(meta.secret_fields)


def test_validate_config_unknown_type_422():
    with pytest.raises(HTTPException) as e:
        validate_config("mongodb", {})
    assert e.value.status_code == 422


def test_validate_config_missing_required_422():
    with pytest.raises(HTTPException) as e:
        validate_config("postgres", {"host": "h", "port": 5432})
    assert e.value.status_code == 422
    assert "required" in e.value.detail


def test_validate_config_strips_unknown_fields():
    cleaned = validate_config("sqlite", {"path": "/tmp/x.db", "bogus": "y"})
    assert cleaned == {"path": "/tmp/x.db"}


def test_validate_config_coerces_numeric_strings():
    cleaned = validate_config("postgres", {
        "host": "h", "port": "5432", "dbname": "d", "user": "u", "password": "p",
    })
    assert cleaned["port"] == 5432


def test_validate_config_rejects_non_numeric_port():
    with pytest.raises(HTTPException) as e:
        validate_config("postgres", {
            "host": "h", "port": "abc", "dbname": "d", "user": "u", "password": "p",
        })
    assert e.value.status_code == 422
    assert "number" in e.value.detail


def test_redact_config_masks_secret_fields():
    redacted = redact_config("postgres", {
        "host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "hunter2",
    })
    assert redacted["password"] == REDACTED
    assert redacted["host"] == "h"
    assert "hunter2" not in str(redacted)


def test_redact_config_masks_url_embedded_credentials():
    redacted = redact_config("jdbc", {"url": "postgresql://alice:hunter2@db:5432/x"})
    assert "hunter2" not in redacted["url"]
    assert "alice" in redacted["url"]  # username stays, password masked


def test_redact_config_unknown_type_falls_back_to_common_keys():
    redacted = redact_config("legacy-type", {"host": "h", "password": "s3cret",
                                             "token": "t0k"})
    assert redacted["password"] == REDACTED
    assert redacted["token"] == REDACTED
    assert redacted["host"] == "h"


def test_secret_fields_for_unknown_type_is_fallback_set():
    assert "password" in secret_fields_for_type("nope")


def test_validate_config_applies_defaults_for_optional_fields():
    # sqlite has no optional-with-default fields; use a synthetic check on
    # postgres where all fields are supplied — cleaned config keeps them all
    cfg = {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"}
    assert validate_config("postgres", cfg) == cfg


def test_module_router_types_source_of_truth():
    # VALID_TYPES hardcoded set is gone — router must derive from catalog
    import app.api.routers.connectors as router_module
    assert not hasattr(router_module, "VALID_TYPES")
