"""Enterprise shell global catalog search tests (E01-5)."""
import pytest
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.connection import DBConnection
from app.models.schema_catalog import CatalogColumn, CatalogTable


@pytest.fixture()
def client_analyst(db, analyst):
    app.dependency_overrides[get_current_user] = lambda: analyst

    def override_db():
        yield db

    app.dependency_overrides[db_module.get_db] = override_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def _seed(db):
    sales = DBConnection(name="Sales Warehouse", type="sqlite", config={"path": "/tmp/sales.db"})
    hidden = DBConnection(
        name="Deleted Sales", type="sqlite", config={"path": "/tmp/deleted.db"},
        is_deleted=True,
    )
    db.add_all([sales, hidden])
    db.flush()
    orders = CatalogTable(connection_id=sales.id, table_name="sales_orders")
    hidden_table = CatalogTable(connection_id=hidden.id, table_name="sales_archive")
    db.add_all([orders, hidden_table])
    db.flush()
    db.add_all([
        CatalogColumn(table_id=orders.id, column_name="sales_total", data_type="DECIMAL"),
        CatalogColumn(table_id=orders.id, column_name="customer_id", data_type="INTEGER"),
        CatalogColumn(table_id=hidden_table.id, column_name="sales_secret", data_type="TEXT"),
    ])
    db.commit()
    return sales


def test_search_returns_ranked_connection_table_and_column(client_analyst, db):
    sales = _seed(db)
    response = client_analyst.get("/api/v1/search?q=sales")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["kind"] for item in body["results"]] == [
        "connection", "table", "column",
    ]
    assert {item["connection_id"] for item in body["results"]} == {sales.id}
    assert body["results"][2]["data_type"] == "DECIMAL"


def test_search_is_paginated_and_validates_boundary(client_analyst, db):
    _seed(db)
    first = client_analyst.get("/api/v1/search?q=sales&page=1&page_size=2")
    second = client_analyst.get("/api/v1/search?q=sales&page=2&page_size=2")

    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert len(first.json()["results"]) == 2
    assert len(second.json()["results"]) == 1
    assert client_analyst.get("/api/v1/search?q=x").status_code == 422
    assert client_analyst.get("/api/v1/search?q=sales&page_size=51").status_code == 422


def test_search_requires_authentication():
    response = TestClient(app).get("/api/v1/search?q=sales")
    assert response.status_code == 401
