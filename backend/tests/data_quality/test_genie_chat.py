"""Genie chat: service normalisation of Genie Conversation API objects + the REST endpoints (Genie mocked)."""
from types import SimpleNamespace as NS

import pytest

from app.services import genie_chat_service as gc

SPACE = "01f1bd7234a4137eb153f8956cc6d01d"
BASE = f"/api/2.0/genie/spaces/{SPACE}"


def _msg(status, attachments=None, error=None):
    m = {"status": status, "attachments": attachments or []}
    if error:
        m["error"] = {"error": error, "type": "SQL_EXECUTION_EXCEPTION"}
    return m


def _query_att(att_id="att1", row_count=2, truncated=False, id_key="attachment_id"):
    return {id_key: att_id, "query": {
        "query": "SELECT 1", "title": "One", "description": "Returns one",
        "query_result_metadata": {"row_count": row_count, "is_truncated": truncated}}}


def _rows_response(n=2):
    return {"statement_response": {
        "manifest": {"schema": {"columns": [
            {"name": "project", "type_name": "STRING", "type_text": "string"},
            {"name": "score", "type_name": "DOUBLE", "type_text": "double"}]}},
        "result": {"data_array": [[f"p{i}", str(90 + i)] for i in range(n)]}}}


class _FakeApi:
    """Stands in for ``WorkspaceClient.api_client`` — records calls and answers Genie REST paths."""

    def __init__(self, message, rows=None):
        self.message, self.rows, self.calls = message, rows, []

    def do(self, method, path, query=None, body=None):
        self.calls.append((method, path, body))
        if path == "/api/2.0/genie/spaces":
            return {"spaces": [{"space_id": SPACE, "title": "DataOne AskData", "description": "d"}]}
        if path == f"{BASE}/start-conversation":
            return {"conversation_id": "c1", "message_id": "m1"}
        if path.endswith("/messages") and method == "POST":
            return {"message_id": "m2"}
        if "/attachments/" in path and path.endswith("/query-result"):
            if isinstance(self.rows, Exception):
                raise self.rows
            return self.rows
        if method == "GET" and "/messages/" in path:
            return self.message
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture()
def genie(monkeypatch):
    def install(message, rows=None):
        fake = _FakeApi(message, rows)
        monkeypatch.setattr(gc, "_client", lambda: NS(api_client=fake))
        return fake
    monkeypatch.setattr(gc.settings, "DATABRICKS_GENIE_SPACE_ID", SPACE)
    return install


class TestService:
    def test_start_new_and_follow_up(self, genie):
        fake = genie(_msg("SUBMITTED"))
        assert gc.start_chat("top projects?") == {"space_id": SPACE, "conversation_id": "c1", "message_id": "m1"}
        out = gc.start_chat("and last month?", conversation_id="c1")
        assert out["message_id"] == "m2"
        assert fake.calls == [
            ("POST", f"{BASE}/start-conversation", {"content": "top projects?"}),
            ("POST", f"{BASE}/conversations/c1/messages", {"content": "and last month?"}),
        ]

    def test_in_progress_is_not_done(self, genie):
        genie(_msg("EXECUTING_QUERY"))
        r = gc.get_result(None, "c1", "m1")
        assert r["status"] == "EXECUTING_QUERY" and r["done"] is False and r["rows"] == []

    def test_completed_returns_answer_sql_and_rows(self, genie):
        text = {"attachment_id": "t1", "text": {"content": " Project p0 scored highest. "}}
        sugg = {"attachment_id": "s1", "suggested_questions": {"questions": ["By month?", " "]}}
        genie(_msg("COMPLETED", [text, _query_att(), sugg]), _rows_response(2))
        r = gc.get_result(None, "c1", "m1")
        assert r["done"] and r["status"] == "COMPLETED"
        assert r["answer"] == "Project p0 scored highest."
        assert r["sql"] == "SELECT 1" and r["sql_title"] == "One" and r["sql_description"] == "Returns one"
        assert r["columns"] == [{"name": "project", "type": "STRING"}, {"name": "score", "type": "DOUBLE"}]
        assert r["rows"] == [["p0", "90"], ["p1", "91"]] and r["row_count"] == 2 and r["truncated"] is False
        assert r["suggested_questions"] == ["By month?"]  # blanks dropped
        assert r["error"] is None

    def test_rows_are_capped(self, genie, monkeypatch):
        monkeypatch.setattr(gc.settings, "GENIE_CHAT_MAX_ROWS", 3)
        genie(_msg("COMPLETED", [_query_att(row_count=10)]), _rows_response(10))
        r = gc.get_result(None, "c1", "m1")
        assert len(r["rows"]) == 3 and r["row_count"] == 10 and r["truncated"] is True

    def test_failed_message_carries_the_error(self, genie):
        genie(_msg("FAILED", error="[INSUFFICIENT_PERMISSIONS] no SELECT"))
        r = gc.get_result(None, "c1", "m1")
        assert r["done"] and r["error"] == "[INSUFFICIENT_PERMISSIONS] no SELECT"

    def test_row_fetch_failure_keeps_the_sql(self, genie):
        genie(_msg("COMPLETED", [_query_att()]), RuntimeError("expired"))
        r = gc.get_result(None, "c1", "m1")
        assert r["sql"] == "SELECT 1" and "expired" in r["error"] and r["rows"] == []

    def test_older_workspaces_name_the_attachment_id_just_id(self, genie):
        genie(_msg("COMPLETED", [_query_att(id_key="id")]), _rows_response(1))
        assert gc.get_result(None, "c1", "m1")["rows"] == [["p0", "90"]]

    def test_space_id_validation(self, monkeypatch):
        monkeypatch.setattr(gc.settings, "DATABRICKS_GENIE_SPACE_ID", None)
        with pytest.raises(gc.GenieChatError):
            gc.resolve_space_id(None)
        with pytest.raises(gc.GenieChatError):
            gc.resolve_space_id("../../etc/passwd")


class TestEndpoints:
    def test_ask_and_poll(self, client, genie):
        genie(_msg("COMPLETED", [_query_att()]), _rows_response(1))
        started = client.post("/api/v1/genie/chat", json={"question": "top projects?"})
        assert started.status_code == 202 and started.json()["conversation_id"] == "c1"
        r = client.get("/api/v1/genie/chat/c1/m1").json()
        assert r["status"] == "COMPLETED" and r["rows"] == [["p0", "90"]]

    def test_follow_up_passes_the_conversation(self, client, genie):
        fake = genie(_msg("SUBMITTED"))
        client.post("/api/v1/genie/chat", json={"question": "and by month?", "conversation_id": "c9"})
        assert fake.calls[0][:2] == ("POST", f"{BASE}/conversations/c9/messages")

    def test_spaces_and_status(self, client, genie):
        genie(_msg("SUBMITTED"))
        body = client.get("/api/v1/genie/spaces").json()
        assert body["spaces"][0]["title"] == "DataOne AskData" and body["default_space_id"] == SPACE
        assert client.get("/api/v1/genie/status").json()["configured"] is True

    def test_not_configured_is_a_400_with_a_clear_message(self, client, monkeypatch):
        monkeypatch.setattr(gc.settings, "DATABRICKS_GENIE_SPACE_ID", None)
        resp = client.post("/api/v1/genie/chat", json={"question": "hi"})
        assert resp.status_code == 400 and "Genie" in resp.json()["detail"]

    def test_genie_errors_surface_as_502(self, client, genie, monkeypatch):
        genie(_msg("SUBMITTED"))
        def boom(*a, **k):
            raise RuntimeError("workspace unreachable")
        monkeypatch.setattr(gc, "start_chat", boom)
        resp = client.post("/api/v1/genie/chat", json={"question": "hi"})
        assert resp.status_code == 502 and "workspace unreachable" in resp.json()["detail"]

    def test_empty_question_rejected(self, client):
        assert client.post("/api/v1/genie/chat", json={"question": ""}).status_code == 422

    def test_viewer_is_forbidden(self, make_client, viewer):
        c = make_client(viewer)
        assert c.post("/api/v1/genie/chat", json={"question": "hi"}).status_code == 403
        assert c.get("/api/v1/genie/spaces").status_code == 403


class _FakeUC:
    """tables.get / tables.list / warehouses.list stand-ins for ensure_data_space."""

    def __init__(self, tables=("titanic", "company_stocks"), missing=()):
        self.missing = set(missing)
        self.tables = NS(
            get=self._get,
            list=lambda catalog_name, schema_name: [NS(name=n) for n in tables],
        )
        self.warehouses = NS(list=lambda: [NS(id="wh1")])

    def _get(self, full_name):
        if full_name.split(".")[-1] in self.missing:
            raise RuntimeError("PERMISSION_DENIED")
        return NS(name=full_name)


class _SpaceApi:
    def __init__(self, existing=()):
        self.existing, self.created, self.updated = list(existing), [], []

    def do(self, method, path, query=None, body=None):
        if method == "GET" and path == "/api/2.0/genie/spaces":
            return {"spaces": self.existing}
        if method == "POST" and path == "/api/2.0/genie/spaces":
            self.created.append(body)
            return {"space_id": "newspace0001"}
        if method == "PATCH" and path.startswith("/api/2.0/genie/spaces/"):
            self.updated.append(body)
            return {}
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture()
def uc(monkeypatch):
    def install(api, fake_uc=None):
        wc = fake_uc or _FakeUC()
        wc.api_client = api
        monkeypatch.setattr(gc, "_client", lambda: wc)
        return wc
    return install


class TestDataSpace:
    def test_creates_an_agent_scoped_to_one_table(self, uc):
        api = _SpaceApi()
        uc(api)
        out = gc.ensure_data_space("workspace", "default", "titanic")
        assert out == {"space_id": "newspace0001", "title": "DataOne data: workspace.default.titanic",
                       "created": True, "tables": ["titanic"]}
        body = api.created[0]
        assert body["warehouse_id"] == "wh1" and body["title"] == out["title"]
        import json as _json
        assert _json.loads(body["serialized_space"]) == {
            "version": 2, "data_sources": {"tables": [
                {"identifier": "workspace.default.titanic"},
                {"identifier": "workspace.dataone_dq.file_quality_results"},
            ]}}

    def test_schema_scope_attaches_all_its_tables_sorted(self, uc):
        api = _SpaceApi()
        uc(api)
        out = gc.ensure_data_space("workspace", "default")
        import json as _json
        tables = _json.loads(api.created[0]["serialized_space"])["data_sources"]["tables"]
        assert [t["identifier"] for t in tables] == [
            "workspace.default.company_stocks",
            "workspace.default.titanic",
            "workspace.dataone_dq.file_quality_results",
        ]
        assert out["title"] == "DataOne data: workspace.default"

    def test_reuses_an_existing_agent_instead_of_creating_another(self, uc):
        api = _SpaceApi(existing=[{"space_id": "old", "title": "DataOne data: workspace.default.titanic"}])
        uc(api)
        out = gc.ensure_data_space("workspace", "default", "titanic")
        assert out["space_id"] == "old" and out["created"] is False and api.created == []
        assert len(api.updated) == 1

    def test_missing_quality_results_keeps_source_agent_usable(self, uc):
        api = _SpaceApi()
        uc(api, _FakeUC(missing=["file_quality_results"]))
        gc.ensure_data_space("workspace", "default", "titanic")
        import json as _json
        tables = _json.loads(api.created[0]["serialized_space"])["data_sources"]["tables"]
        assert tables == [{"identifier": "workspace.default.titanic"}]

    def test_unreadable_table_is_a_clear_error(self, uc):
        uc(_SpaceApi(), _FakeUC(missing=["titanic"]))
        with pytest.raises(gc.GenieChatError, match="cannot read workspace.default.titanic"):
            gc.ensure_data_space("workspace", "default", "titanic")

    def test_empty_schema_is_rejected(self, uc):
        uc(_SpaceApi(), _FakeUC(tables=()))
        with pytest.raises(gc.GenieChatError, match="no tables"):
            gc.ensure_data_space("workspace", "empty")

    @pytest.mark.parametrize("c,s,t", [("work space", "default", None), ("workspace", "de;fault", None), ("workspace", "default", "a.b")])
    def test_names_are_validated(self, uc, c, s, t):
        uc(_SpaceApi())
        with pytest.raises(gc.GenieChatError, match="Invalid"):
            gc.ensure_data_space(c, s, t)

    def test_list_spaces_flags_auto_created_agents(self, uc):
        uc(_SpaceApi(existing=[{"space_id": "a", "title": "DataOne AskData"},
                               {"space_id": "b", "title": "DataOne data: workspace.default"}]))
        flags = {s["space_id"]: s["auto"] for s in gc.list_spaces()}
        assert flags == {"a": False, "b": True}

    def test_endpoint_creates_audits_and_returns_the_agent(self, client, uc):
        uc(_SpaceApi())
        resp = client.post("/api/v1/genie/data-space", json={"catalog": "workspace", "schema": "default", "table": "titanic"})
        assert resp.status_code == 200 and resp.json()["space_id"] == "newspace0001"

    def test_endpoint_surfaces_scope_errors_as_400(self, client, uc):
        uc(_SpaceApi(), _FakeUC(tables=()))
        resp = client.post("/api/v1/genie/data-space", json={"catalog": "workspace", "schema": "empty"})
        assert resp.status_code == 400 and "no tables" in resp.json()["detail"]
