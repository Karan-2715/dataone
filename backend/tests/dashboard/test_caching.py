"""Tests for the dashboard summary cache (dashboard_tasks #2)."""
import time

import pytest

from app.core.config import settings
from app.services import dashboard_cache
from app.services.dashboard_service import DashboardService


@pytest.fixture()
def count_fanouts(monkeypatch):
    """Wrap the real _do_get_summary with a call counter — cache hits skip it."""
    calls = {"n": 0}
    original = DashboardService._do_get_summary

    def counting(self, range):
        calls["n"] += 1
        return original(self, range=range)

    monkeypatch.setattr(DashboardService, "_do_get_summary", counting)
    return calls


class TestDashboardCaching:
    def test_second_request_is_cache_hit(self, client, seed_data, count_fanouts):
        r1 = client.get("/api/v1/dashboard/summary")
        r2 = client.get("/api/v1/dashboard/summary")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        assert count_fanouts["n"] == 1

    def test_different_ranges_are_separate_entries(self, client, seed_data, count_fanouts):
        assert client.get("/api/v1/dashboard/summary?range=24h").json()["range"] == "24h"
        assert client.get("/api/v1/dashboard/summary?range=7d").json()["range"] == "7d"
        assert count_fanouts["n"] == 2

    def test_different_users_are_separate_entries(self, make_client, admin, analyst,
                                                  seed_data, count_fanouts):
        make_client(admin).get("/api/v1/dashboard/summary")
        make_client(analyst).get("/api/v1/dashboard/summary")
        assert count_fanouts["n"] == 2

    def test_ttl_expiry_refetches(self, client, seed_data, count_fanouts, monkeypatch):
        monkeypatch.setattr(settings, "DASHBOARD_CACHE_TTL", 1)
        dashboard_cache.invalidate_all()  # rebuild singleton with the new TTL
        client.get("/api/v1/dashboard/summary")
        client.get("/api/v1/dashboard/summary")
        assert count_fanouts["n"] == 1
        time.sleep(1.1)
        client.get("/api/v1/dashboard/summary")
        assert count_fanouts["n"] == 2

    def test_ttl_zero_disables_caching(self, client, seed_data, count_fanouts, monkeypatch):
        monkeypatch.setattr(settings, "DASHBOARD_CACHE_TTL", 0)
        dashboard_cache.invalidate_all()
        client.get("/api/v1/dashboard/summary")
        client.get("/api/v1/dashboard/summary")
        assert count_fanouts["n"] == 2

    def test_invalidate_all_clears_entries(self, client, seed_data, count_fanouts):
        client.get("/api/v1/dashboard/summary")
        dashboard_cache.invalidate_all()
        client.get("/api/v1/dashboard/summary")
        assert count_fanouts["n"] == 2
