"""Tests for the dockable workspace layout persistence API (Enterprise v2, E11-8)."""


class TestWorkspaceLayoutApi:
    def test_get_unknown_workspace_returns_empty_layout(self, client):
        resp = client.get("/api/v1/workspace-layout/schema-mapper")
        assert resp.status_code == 200
        assert resp.json() == {"workspace_key": "schema-mapper", "layout": {}}

    def test_put_then_get_roundtrips(self, client):
        layout = {"panels": {"properties": {"state": "normal", "width": 340}}}
        put_resp = client.put("/api/v1/workspace-layout/schema-mapper", json={"layout": layout})
        assert put_resp.status_code == 200
        assert put_resp.json()["layout"] == layout

        get_resp = client.get("/api/v1/workspace-layout/schema-mapper")
        assert get_resp.json()["layout"] == layout

    def test_put_overwrites_previous_layout(self, client):
        client.put("/api/v1/workspace-layout/schema-mapper", json={"layout": {"panels": {"properties": {"state": "normal", "width": 300}}}})
        second = {"panels": {"properties": {"state": "collapsed", "width": 300}}}
        client.put("/api/v1/workspace-layout/schema-mapper", json={"layout": second})
        assert client.get("/api/v1/workspace-layout/schema-mapper").json()["layout"] == second

    def test_layout_is_scoped_per_user(self, make_client, admin, analyst):
        make_client(admin).put(
            "/api/v1/workspace-layout/schema-mapper",
            json={"layout": {"panels": {"properties": {"state": "maximized", "width": 400}}}},
        )
        analyst_layout = make_client(analyst).get("/api/v1/workspace-layout/schema-mapper").json()
        assert analyst_layout["layout"] == {}

    def test_unknown_workspace_key_returns_422(self, client):
        resp = client.get("/api/v1/workspace-layout/not-a-real-workspace")
        assert resp.status_code == 422
        put_resp = client.put("/api/v1/workspace-layout/not-a-real-workspace", json={"layout": {}})
        assert put_resp.status_code == 422

    def test_oversized_layout_returns_422(self, client):
        huge = {"panels": {"properties": {"state": "normal", "width": 1, "junk": "x" * 30_000}}}
        resp = client.put("/api/v1/workspace-layout/schema-mapper", json={"layout": huge})
        assert resp.status_code == 422

    def test_anonymous_returns_401(self, make_client):
        resp = make_client(None).get("/api/v1/workspace-layout/schema-mapper")
        assert resp.status_code == 401
