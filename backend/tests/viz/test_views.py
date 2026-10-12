"""Tests for VizView saved-view CRUD (Visualize Task #1, VIZ-T5)."""
import pytest
from fastapi import HTTPException

from app.schemas.viz import FilterSpec, MeasureSpec
from app.services.viz_service import VizService


def test_create_and_get_view(db, admin, sales_connection):
    view = VizService.create_view(
        db, actor=admin.email,
        name="Sales by Region", connection_id=sales_connection.id, table_name="sales",
        chart_type="bar", dimensions=["region"],
        measures=[MeasureSpec(field="amount", aggregation="sum")],
        filters=[],
    )
    assert view.id is not None
    assert view.measures == [{"field": "amount", "aggregation": "sum", "label": None}]

    fetched = VizService.get_view(db, view.id)
    assert fetched.name == "Sales by Region"


def test_list_views_ordered_by_recent(db, admin, sales_connection):
    VizService.create_view(
        db, actor=admin.email, name="View A", connection_id=sales_connection.id,
        table_name="sales", chart_type="table", dimensions=[], measures=[], filters=[],
    )
    VizService.create_view(
        db, actor=admin.email, name="View B", connection_id=sales_connection.id,
        table_name="sales", chart_type="table", dimensions=[], measures=[], filters=[],
    )
    items, total = VizService.list_views(db)
    assert total == 2
    assert {v.name for v in items} == {"View A", "View B"}


def test_delete_view(db, admin, sales_connection):
    view = VizService.create_view(
        db, actor=admin.email, name="Temp", connection_id=sales_connection.id,
        table_name="sales", chart_type="table", dimensions=[], measures=[], filters=[],
    )
    VizService.delete_view(db, view.id, actor=admin.email)
    with pytest.raises(HTTPException) as e:
        VizService.get_view(db, view.id)
    assert e.value.status_code == 404


def test_delete_view_emits_audit(db, admin, sales_connection):
    from app.models.audit import AuditLog

    view = VizService.create_view(
        db, actor=admin.email, name="Audited", connection_id=sales_connection.id,
        table_name="sales", chart_type="table", dimensions=[], measures=[], filters=[],
    )
    VizService.delete_view(db, view.id, actor=admin.email)
    audit = db.query(AuditLog).filter(AuditLog.event_type == "viz_view_deleted").first()
    assert audit is not None
    assert audit.payload["name"] == "Audited"


def test_filters_persist_with_operator_and_value(db, admin, sales_connection):
    view = VizService.create_view(
        db, actor=admin.email, name="Filtered", connection_id=sales_connection.id,
        table_name="sales", chart_type="table", dimensions=["region"], measures=[],
        filters=[FilterSpec(field="amount", operator="gt", value=100)],
    )
    fetched = VizService.get_view(db, view.id)
    assert fetched.filters == [{"field": "amount", "operator": "gt", "value": 100}]
