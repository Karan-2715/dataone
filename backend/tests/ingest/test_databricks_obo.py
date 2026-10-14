from types import SimpleNamespace

from starlette.requests import Request

from app.api.routers import databricks_ingest
from app.services import databricks_ingestion_service


def request_with_headers(**headers: str) -> Request:
    raw_headers = [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()]
    return Request({"type": "http", "headers": raw_headers})


def test_user_token_comes_from_databricks_forwarded_header() -> None:
    request = request_with_headers(
        x_forwarded_access_token="obo-token",
        authorization="Bearer dataone-session-token",
    )

    assert databricks_ingest._get_user_token(request) == "obo-token"


def test_catalog_discovery_uses_user_token(monkeypatch) -> None:
    captured = {}
    client = SimpleNamespace(
        catalogs=SimpleNamespace(list=lambda: [SimpleNamespace(name="workspace")]),
    )

    def get_client(token):
        captured["token"] = token
        return client

    monkeypatch.setattr(databricks_ingestion_service, "_get_workspace_client", get_client)

    result = databricks_ingest.get_databricks_catalogs(
        request_with_headers(x_forwarded_access_token="obo-token"),
        SimpleNamespace(email="user@example.com"),
    )

    assert captured == {"token": "obo-token"}
    assert result == {"catalogs": [{"name": "workspace"}]}
