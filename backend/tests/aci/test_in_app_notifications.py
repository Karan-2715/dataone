"""In-app feed reuses notify-out triggers without requiring ACI opt-in."""
from fastapi.testclient import TestClient

from app.api.routers.auth import get_current_user
from app.core import database as db_module
from app.main import app
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.notification_service import dispatch_notify_out


def test_disabled_outbound_still_creates_user_readable_feed(db):
    admin = User(email="feed-admin@test.local", hashed_password=AuthService.hash_password("x"), role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    # No NotificationSetting row: external delivery remains disabled.
    assert dispatch_notify_out(
        db, event_key="pipeline:run_failure", title="Pipeline failed",
        body="bad row", link="/dashboard/pipelines",
    ) is False
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[db_module.get_db] = lambda: db
    client = TestClient(app)
    try:
        feed = client.get("/api/v1/notifications")
        assert feed.status_code == 200
        item = feed.json()["notifications"][0]
        assert item["title"] == "Pipeline failed"
        assert item["read"] is False
        assert client.get("/api/v1/notifications/unread-count").json() == {"unread": 1}

        marked = client.patch(f"/api/v1/notifications/{item['id']}/read")
        assert marked.status_code == 200
        assert client.get("/api/v1/notifications/unread-count").json() == {"unread": 0}
    finally:
        app.dependency_overrides.clear()


def test_notification_feed_requires_authentication():
    assert TestClient(app).get("/api/v1/notifications").status_code == 401
