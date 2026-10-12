"""Column-level PII masking enforcement (SEC-T3, FR4/AC2) — enforced inside
VizService.run_query, scoped to connection_id + table_name + column_name.
"""
import pytest
from fastapi import HTTPException

from app.services.rbac_service import MaskingPolicyCRUD
from app.services.viz_service import VizService


def _create_masking_policy(db, connection_id, exempt_roles=None):
    return MaskingPolicyCRUD.create_policy(
        db, connection_id=connection_id, table_name="sales", column_name="owner_email",
        masking_type="redact", exempt_roles=exempt_roles or [], actor="admin@test.local",
    )


def test_masks_column_for_role_not_exempt(seeded, sales_connection, viewer):
    _create_masking_policy(seeded, sales_connection.id, exempt_roles=["admin"])

    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="viewer",
    )
    emails = [r[0] for r in result["rows"]]
    assert all(e == "***" for e in emails)


def test_does_not_mask_for_exempt_role(seeded, sales_connection, admin):
    _create_masking_policy(seeded, sales_connection.id, exempt_roles=["admin"])

    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="admin",
    )
    emails = [r[0] for r in result["rows"]]
    assert any("@" in e for e in emails)


def test_no_policy_means_unmasked(seeded, sales_connection):
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="viewer",
    )
    emails = [r[0] for r in result["rows"]]
    assert any("@" in e for e in emails)


@pytest.mark.parametrize("masking_type,expect", [
    ("redact", "***"),
    ("substitute", "[MASKED]"),
    ("nullify", None),
])
def test_masking_types(seeded, sales_connection, masking_type, expect):
    MaskingPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales", column_name="owner_email",
        masking_type=masking_type, exempt_roles=[], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="viewer",
    )
    assert result["rows"][0][0] == expect


def test_hash_masking_is_deterministic_and_not_reversible(seeded, sales_connection):
    MaskingPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales", column_name="owner_email",
        masking_type="hash", exempt_roles=[], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="viewer",
    )
    hashed = [r[0] for r in result["rows"]]
    assert all(h.startswith("sha256:") for h in hashed)
    assert all("@" not in h for h in hashed)
    assert len(set(hashed)) == len(hashed)  # distinct emails hash distinctly


def test_create_masking_policy_rejects_unknown_role(seeded, sales_connection):
    with pytest.raises(HTTPException) as e:
        MaskingPolicyCRUD.create_policy(
            seeded, connection_id=sales_connection.id, table_name="sales", column_name="owner_email",
            masking_type="redact", exempt_roles=["not_a_role"], actor="admin@test.local",
        )
    assert e.value.status_code == 400


def test_create_masking_policy_rejects_duplicate_target(seeded, sales_connection):
    _create_masking_policy(seeded, sales_connection.id)
    with pytest.raises(HTTPException) as e:
        _create_masking_policy(seeded, sales_connection.id)
    assert e.value.status_code == 409


def test_delete_masking_policy_removes_enforcement(seeded, sales_connection):
    policy = _create_masking_policy(seeded, sales_connection.id)
    MaskingPolicyCRUD.delete_policy(seeded, policy.id, actor="admin@test.local")

    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["owner_email"], measures=[], filters=[],
        requester_role="viewer",
    )
    assert any("@" in r[0] for r in result["rows"])


def test_measure_aggregate_alias_is_never_masked(seeded, sales_connection):
    """A masking policy on 'amount' shouldn't touch a SUM(amount) alias —
    the alias name ('sum_amount') never matches the raw column name."""
    MaskingPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales", column_name="amount",
        masking_type="nullify", exempt_roles=[], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[{"field": "amount", "aggregation": "sum"}], filters=[],
        requester_role="viewer",
    )
    assert all(r[1] is not None for r in result["rows"])
