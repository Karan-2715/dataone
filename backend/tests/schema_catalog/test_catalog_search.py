"""Tests for the catalog search/filter API (Task #4, FR4)."""
from app.services.schema_catalog_service import SchemaCatalogService


def test_search_by_table_name(db, physical_sqlite_connection):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    tables = SchemaCatalogService.get_catalog(db, physical_sqlite_connection.id, q="people")
    assert len(tables) == 1
    assert tables[0].table_name == "people"


def test_search_by_column_name(db, physical_sqlite_connection):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    tables = SchemaCatalogService.get_catalog(db, physical_sqlite_connection.id, q="contact")
    assert len(tables) == 1


def test_search_no_match_returns_empty(db, physical_sqlite_connection):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    tables = SchemaCatalogService.get_catalog(db, physical_sqlite_connection.id, q="nonexistent_xyz")
    assert tables == []


def test_filter_by_data_type(db, physical_sqlite_connection):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    tables = SchemaCatalogService.get_catalog(db, physical_sqlite_connection.id, data_type="INTEGER")
    assert len(tables) == 1  # 'people' has id/age as INTEGER columns


def test_filter_by_classification_label(db, physical_sqlite_connection):
    from app.tasks import schema_intel_tasks as sit_module
    from unittest.mock import patch
    from app.models.schema_catalog import CatalogColumn

    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    col = db.query(CatalogColumn).filter(CatalogColumn.column_name == "contact").first()

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

    tables = SchemaCatalogService.get_catalog(
        db, physical_sqlite_connection.id, classification_label="PII",
    )
    assert len(tables) == 1

    tables = SchemaCatalogService.get_catalog(
        db, physical_sqlite_connection.id, classification_label="Sensitive",
    )
    assert tables == []


def test_no_filters_returns_all_tables(db, physical_sqlite_connection):
    SchemaCatalogService.scan_connection(db, physical_sqlite_connection.id, actor="admin@test.local")
    tables = SchemaCatalogService.get_catalog(db, physical_sqlite_connection.id)
    assert len(tables) == 1
