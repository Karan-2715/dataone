"""Databricks OAuth sign-in (authorization code + PKCE) — SDK calls faked, state/DB/redirect logic real."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.models.oauth_login_state import OAuthLoginState
from app.models.user import User
from app.services import databricks_auth_service as mod
from app.services.databricks_auth_service import DatabricksAuthService, safe_redirect_path

HOST = "https://dbc-test.cloud.databricks.com"


class FakeConsent:
    authorization_url = f"{HOST}/oidc/v1/authorize?state=st-1&code_challenge=abc"

    def as_dict(self):
        return {"state": "st-1", "verifier": "ver", "authorization_url": self.authorization_url,
                "redirect_url": "r", "token_endpoint": "t", "client_id": "cid"}


class FakeExchange:
    def __init__(self, raw, client_secret=None):
        self.raw = raw

    def exchange_callback_parameters(self, query):
        assert query["code"] == "the-code" and query["state"] == self.raw["state"]
        return SimpleNamespace(token=lambda: SimpleNamespace(
            access_token="access-1", refresh_token="refresh-1", expiry=datetime.now() + timedelta(hours=1)))


@pytest.fixture()
def oauth(monkeypatch):
    svc = DatabricksAuthService()
    svc.workspace_url, svc.client_id, svc.client_secret = HOST, "cid", None
    monkeypatch.setattr(mod, "databricks_auth_service", svc)
    monkeypatch.setattr("app.api.routes.databricks_auth.databricks_auth_service", svc)
    monkeypatch.setattr(mod.OAuthClient, "from_host", staticmethod(lambda **kw: SimpleNamespace(initiate_consent=FakeConsent)))
    monkeypatch.setattr(mod.Consent, "from_dict", staticmethod(lambda raw, client_secret=None: FakeExchange(raw)))
    monkeypatch.setattr(svc, "get_user_info", lambda tok: {"email": "new@corp.com", "display_name": "New", "id": "9", "active": True})
    monkeypatch.setattr(svc, "get_user_permissions", lambda tok: ["sql:execute", "unity_catalog:read"])
    return svc


def test_default_scopes_cover_the_installer():
    assert "all-apis" in settings.DATABRICKS_OAUTH_SCOPES.split()
    assert "offline_access" in settings.DATABRICKS_OAUTH_SCOPES.split()


@pytest.mark.parametrize("given,expected", [
    ("/dashboard/workspace-setup", "/dashboard/workspace-setup"),
    ("//evil.com", "/dashboard"), ("https://evil.com", "/dashboard"), ("", "/dashboard"), (None, "/dashboard"),
])
def test_redirect_target_is_same_site_only(given, expected):
    assert safe_redirect_path(given) == expected


class TestLoginFlow:
    def test_login_redirects_to_databricks_and_persists_pkce_state(self, make_client, db, oauth):
        resp = make_client(None).get("/api/v1/auth/databricks/login?redirect_to=/dashboard/workspace-setup",
                                     follow_redirects=False)
        assert resp.status_code in (302, 307) and resp.headers["location"] == FakeConsent.authorization_url
        row = db.get(OAuthLoginState, "st-1")
        assert row.redirect_to == "/dashboard/workspace-setup" and row.consent["verifier"] == "ver"

    def test_login_never_stores_an_external_redirect(self, make_client, db, oauth):
        make_client(None).get("/api/v1/auth/databricks/login?redirect_to=//evil.com", follow_redirects=False)
        assert db.get(OAuthLoginState, "st-1").redirect_to == "/dashboard"

    def test_callback_stores_the_users_own_tokens_and_signs_them_in(self, make_client, db, oauth):
        client = make_client(None)
        client.get("/api/v1/auth/databricks/login?redirect_to=/dashboard/workspace-setup", follow_redirects=False)
        resp = client.get("/api/v1/auth/databricks/callback?code=the-code&state=st-1", follow_redirects=False)

        assert resp.status_code in (302, 307)
        loc = urlparse(resp.headers["location"])
        assert loc.path == "/dashboard/workspace-setup" and parse_qs(loc.query)["token"]
        user = db.query(User).filter_by(email="new@corp.com").one()
        assert user.databricks_access_token == "access-1" and user.databricks_refresh_token == "refresh-1"
        assert user.databricks_token_expires_at is not None
        assert db.get(OAuthLoginState, "st-1") is None  # single use

    def test_replayed_or_unknown_state_is_a_400(self, make_client, db, oauth):
        client = make_client(None)
        client.get("/api/v1/auth/databricks/login", follow_redirects=False)
        client.get("/api/v1/auth/databricks/callback?code=the-code&state=st-1", follow_redirects=False)
        replay = client.get("/api/v1/auth/databricks/callback?code=the-code&state=st-1", follow_redirects=False)
        assert replay.status_code == 400
        assert client.get("/api/v1/auth/databricks/callback?code=x&state=nope", follow_redirects=False).status_code == 400

    def test_expired_attempt_is_rejected(self, make_client, db, oauth):
        client = make_client(None)
        client.get("/api/v1/auth/databricks/login", follow_redirects=False)
        row = db.get(OAuthLoginState, "st-1")
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        db.commit()
        assert client.get("/api/v1/auth/databricks/callback?code=the-code&state=st-1",
                          follow_redirects=False).status_code == 400

    def test_stale_attempts_are_purged_on_next_login(self, make_client, db, oauth):
        db.add(OAuthLoginState(state="old", consent={}, created_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        db.commit()
        make_client(None).get("/api/v1/auth/databricks/login", follow_redirects=False)
        assert db.get(OAuthLoginState, "old") is None

    def test_status_reports_configuration(self, make_client, oauth):
        body = make_client(None).get("/api/v1/auth/databricks/status").json()
        assert body["oauth_configured"] is True and body["workspace_url"] == HOST


class TestTokenRefresh:
    def _user(self, db, expires, refresh="r-old"):
        u = User(email="u@corp.com", role="analyst", is_active=True, databricks_access_token="a-old",
                 databricks_refresh_token=refresh, databricks_token_expires_at=expires)
        db.add(u); db.commit()
        return u

    def test_valid_token_is_returned_untouched(self, db, oauth):
        u = self._user(db, datetime.now(timezone.utc) + timedelta(hours=1))
        assert oauth.ensure_token_valid(u, db) == "a-old"

    def test_expired_token_is_refreshed_and_saved(self, db, oauth, monkeypatch):
        seen = {}

        def fake_creds(**kw):
            seen.update(kw)
            return SimpleNamespace(token=lambda: SimpleNamespace(
                access_token="a-new", refresh_token="r-new", expiry=datetime.now() + timedelta(hours=1)))

        monkeypatch.setattr(mod, "SessionCredentials", fake_creds)
        u = self._user(db, datetime.now(timezone.utc) - timedelta(minutes=1))
        assert oauth.ensure_token_valid(u, db) == "a-new"
        assert seen["token"].refresh_token == "r-old" and seen["token_endpoint"] == f"{HOST}/oidc/v1/token"
        db.refresh(u)
        assert u.databricks_refresh_token == "r-new" and u.databricks_token_expires_at is not None

    def test_expired_without_refresh_token_asks_to_sign_in_again(self, db, oauth):
        u = self._user(db, datetime.now(timezone.utc) - timedelta(minutes=1), refresh=None)
        with pytest.raises(ValueError, match="sign in"):
            oauth.ensure_token_valid(u, db)
