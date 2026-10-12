"""Row-level access filter enforcement (SEC-T4, FR5) — enforced inside
VizService.run_query, ANDed onto the caller's own filters.
"""
import pytest
from fastapi import HTTPException

from app.services.rbac_service import RowAccessPolicyCRUD
from app.services.viz_service import VizService


def test_row_filter_restricts_rows_for_scoped_role(seeded, sales_connection):
    RowAccessPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales",
        filter_conditions=[{"field": "region", "operator": "=", "value": "west"}],
        applies_to_roles=["viewer"], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[], filters=[],
        requester_role="viewer",
    )
    regions = {r[0] for r in result["rows"]}
    assert regions == {"west"}


def test_row_filter_does_not_apply_to_unscoped_role(seeded, sales_connection):
    RowAccessPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales",
        filter_conditions=[{"field": "region", "operator": "=", "value": "west"}],
        applies_to_roles=["viewer"], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[], filters=[],
        requester_role="admin",
    )
    regions = {r[0] for r in result["rows"]}
    assert regions == {"west", "east", "north"}


def test_row_filter_ands_with_callers_own_filter(seeded, sales_connection):
    RowAccessPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales",
        filter_conditions=[{"field": "amount", "operator": ">", "value": 60}],
        applies_to_roles=["viewer"], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[],
        filters=[{"field": "region", "operator": "eq", "value": "east"}],
        requester_role="viewer",
    )
    # east rows: amount=200 (>60, kept), amount=50 (<=60, dropped by policy)
    assert result["row_count"] == 1


def test_row_filter_in_operator(seeded, sales_connection):
    RowAccessPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales",
        filter_conditions=[{"field": "region", "operator": "in", "value": ["west", "north"]}],
        applies_to_roles=["viewer"], actor="admin@test.local",
    )
    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[], filters=[],
        requester_role="viewer",
    )
    regions = {r[0] for r in result["rows"]}
    assert regions == {"west", "north"}


def test_create_row_filter_rejects_empty_conditions(seeded, sales_connection):
    with pytest.raises(HTTPException) as e:
        RowAccessPolicyCRUD.create_policy(
            seeded, connection_id=sales_connection.id, table_name="sales",
            filter_conditions=[], applies_to_roles=["viewer"], actor="admin@test.local",
        )
    assert e.value.status_code == 400


def test_create_row_filter_rejects_bad_operator(seeded, sales_connection):
    with pytest.raises(HTTPException) as e:
        RowAccessPolicyCRUD.create_policy(
            seeded, connection_id=sales_connection.id, table_name="sales",
            filter_conditions=[{"field": "region", "operator": "LIKE", "value": "w%"}],
            applies_to_roles=["viewer"], actor="admin@test.local",
        )
    assert e.value.status_code == 400


def test_create_row_filter_rejects_unknown_role(seeded, sales_connection):
    with pytest.raises(HTTPException) as e:
        RowAccessPolicyCRUD.create_policy(
            seeded, connection_id=sales_connection.id, table_name="sales",
            filter_conditions=[{"field": "region", "operator": "=", "value": "west"}],
            applies_to_roles=["not_a_role"], actor="admin@test.local",
        )
    assert e.value.status_code == 400


def test_delete_row_filter_removes_enforcement(seeded, sales_connection):
    policy = RowAccessPolicyCRUD.create_policy(
        seeded, connection_id=sales_connection.id, table_name="sales",
        filter_conditions=[{"field": "region", "operator": "=", "value": "west"}],
        applies_to_roles=["viewer"], actor="admin@test.local",
    )
    RowAccessPolicyCRUD.delete_policy(seeded, policy.id, actor="admin@test.local")

    result = VizService.run_query(
        seeded, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"], measures=[], filters=[],
        requester_role="viewer",
    )
    regions = {r[0] for r in result["rows"]}
    assert regions == {"west", "east", "north"}
