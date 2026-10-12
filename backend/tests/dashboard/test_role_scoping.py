"""Tests for role-scoped dashboard data (dashboard_tasks #7)."""
from tests.dashboard.conftest import _make_user

RESTRICTED_LABELS = {"PII Columns", "Security Alerts", "AI Autopilot Actions", "Critical Risks"}


class TestDashboardRoleScoping:
    def test_admin_sees_all_tiles_loaded(self, make_client, admin, seed_data):
        tiles = make_client(admin).get("/api/v1/dashboard/summary").json()["kpis"]
        assert all(t["status"] == "loaded" for t in tiles)

    def test_analyst_sees_all_tiles_loaded(self, make_client, analyst, seed_data):
        tiles = make_client(analyst).get("/api/v1/dashboard/summary").json()["kpis"]
        assert all(t["status"] == "loaded" for t in tiles)

    def test_viewer_gets_restricted_placeholders(self, make_client, viewer, seed_data):
        resp = make_client(viewer).get("/api/v1/dashboard/summary")
        assert resp.status_code == 200  # in-band filtering, never 403
        assert resp.json()["available_views"] == ["operational"]
        tiles = {t["label"]: t for t in resp.json()["kpis"]}
        # All tiles remain present — restricted ones are masked, not dropped.
        assert len(tiles) == 17
        for label in RESTRICTED_LABELS:
            assert tiles[label]["status"] == "unavailable"
            assert tiles[label]["value"] == 0
            assert tiles[label]["subtitle"] == "Restricted"
            assert tiles[label]["link_url"] == ""
        for label, t in tiles.items():
            if label not in RESTRICTED_LABELS:
                assert t["status"] == "loaded"

    def test_viewer_feed_drops_restricted_modules(self, make_client, viewer, seed_data):
        feed = make_client(viewer).get("/api/v1/dashboard/summary").json()["feed"]
        assert len(feed) > 0
        assert all(item["module"] not in ("security", "autopilot", "audit")
                   for item in feed)
        # The security_alert and autopilot events were seeded — verify they
        # were actually filtered, not just absent.
        assert "security_alert" not in {i["event_type"] for i in feed}

    def test_unknown_role_defaults_to_viewer(self, make_client, db, seed_data):
        intern = _make_user(db, "intern@test.local", "intern")
        tiles = make_client(intern).get("/api/v1/dashboard/summary").json()["kpis"]
        assert any(t["status"] == "unavailable" for t in tiles)

    def test_viewer_and_admin_get_different_cached_views(self, make_client, admin, viewer, seed_data):
        """Cache is keyed per user — a viewer's masked summary must not be
        served to an admin (or vice versa) inside the TTL window."""
        viewer_tiles = make_client(viewer).get("/api/v1/dashboard/summary").json()["kpis"]
        admin_tiles = make_client(admin).get("/api/v1/dashboard/summary").json()["kpis"]
        assert any(t["status"] == "unavailable" for t in viewer_tiles)
        assert all(t["status"] == "loaded" for t in admin_tiles)
