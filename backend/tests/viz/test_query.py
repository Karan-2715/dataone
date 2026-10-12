"""Tests for VizService.run_query (Visualize Task #1, VIZ-T1)."""
import pytest
from fastapi import HTTPException

from app.services.viz_service import VizService


def test_group_by_dimension_with_sum_measure(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"],
        measures=[{"field": "amount", "aggregation": "sum"}],
        filters=[],
    )
    assert result["columns"] == ["region", "sum_amount"]
    rows_by_region = {r[0]: r[1] for r in result["rows"]}
    assert rows_by_region["west"] == pytest.approx(250.0)
    assert rows_by_region["east"] == pytest.approx(250.0)
    assert rows_by_region["north"] == pytest.approx(300.0)


def test_multiple_measures(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"],
        measures=[
            {"field": "amount", "aggregation": "sum"},
            {"field": "qty", "aggregation": "avg"},
        ],
        filters=[],
    )
    assert result["columns"] == ["region", "sum_amount", "avg_qty"]


def test_filter_eq(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=["region"],
        measures=[{"field": "amount", "aggregation": "sum"}],
        filters=[{"field": "region", "operator": "eq", "value": "west"}],
    )
    assert len(result["rows"]) == 1
    assert result["rows"][0][0] == "west"


def test_filter_gt(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=[],
        measures=[{"field": "amount", "aggregation": "count", "label": "n"}],
        filters=[{"field": "amount", "operator": "gt", "value": 100}],
    )
    assert result["rows"][0][0] == 3  # 150, 200, 300


def test_filter_between(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=[],
        measures=[{"field": "amount", "aggregation": "count", "label": "n"}],
        filters=[{"field": "amount", "operator": "between", "value": [100, 200]}],
    )
    assert result["rows"][0][0] == 3  # 100, 150, 200


def test_rejects_sql_injection_attempt_in_field_name(db, sales_connection):
    with pytest.raises(HTTPException) as e:
        VizService.run_query(
            db, connection_id=sales_connection.id, table_name="sales",
            dimensions=["region; DROP TABLE sales; --"],
            measures=[],
            filters=[],
        )
    assert e.value.status_code == 422


def test_rejects_invalid_table_name(db, sales_connection):
    with pytest.raises(HTTPException) as e:
        VizService.run_query(
            db, connection_id=sales_connection.id, table_name="sales; DROP TABLE sales",
            dimensions=[], measures=[{"field": "amount", "aggregation": "sum"}], filters=[],
        )
    assert e.value.status_code == 422


def test_missing_connection_404(db):
    with pytest.raises(HTTPException) as e:
        VizService.run_query(
            db, connection_id=99999, table_name="sales",
            dimensions=["region"], measures=[], filters=[],
        )
    assert e.value.status_code == 404


def test_requires_dimension_or_measure(db, sales_connection):
    with pytest.raises(HTTPException) as e:
        VizService.run_query(
            db, connection_id=sales_connection.id, table_name="sales",
            dimensions=[], measures=[], filters=[],
        )
    assert e.value.status_code == 422


def test_no_dimensions_returns_single_aggregate_row(db, sales_connection):
    result = VizService.run_query(
        db, connection_id=sales_connection.id, table_name="sales",
        dimensions=[], measures=[{"field": "amount", "aggregation": "sum"}], filters=[],
    )
    assert len(result["rows"]) == 1
    assert result["rows"][0][0] == pytest.approx(800.0)
