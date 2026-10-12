"""Microsoft Entra SSO login (stage stopgap, feature/sso-fix).

Covers: disabled-by-default, CSRF state validation, tenant validation, the
default no-auto-provisioning rule (a Microsoft identity with no matching
DataOne user is rejected, never silently signed up), and the *opt-in*
ENTRA_AUTO_PROVISION_DOMAIN stopgap that lifts that rule for one domain.
"""
from app.models.user import User
from app.services.auth_service import AuthService

from .conftest import TEST_TENANT_ID


def test_entra_login_503_when_not_configured(client):
    r = client.get("/api/v1/auth/entra/login")
    assert r.status_code == 503


def test_entra_callback_503_when_not_configured(client):
    r = client.get("/api/v1/auth/entra/callback?code=abc&state=xyz")
    assert r.status_code == 503


def test_entra_login_redirects_and_sets_state_cookie(client, entra_configured, patch_entra_client):
    patch_entra_client()
    r = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "login.microsoftonline.com" in r.headers["location"]
    assert "entra_state" in r.cookies


def test_entra_callback_rejects_missing_code_or_state(client, entra_configured, patch_entra_client):
    patch_entra_client()
    r = client.get("/api/v1/auth/entra/callback", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=state_mismatch" in r.headers["location"]


def test_entra_callback_rejects_state_mismatch(client, entra_configured, patch_entra_client):
    patch_entra_client()
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    real_state = login.cookies["entra_state"]
    assert real_state  # sanity: cookie was actually set

    r = client.get(
        "/api/v1/auth/entra/callback?code=abc&state=not-the-real-state",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "entra_error=state_mismatch" in r.headers["location"]


def test_entra_callback_rejects_wrong_tenant(client, entra_configured, patch_entra_client, existing_user):
    patch_entra_client(claims={"tid": "some-other-tenant", "preferred_username": existing_user.email})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    # The Secure cookie attribute means httpx won't auto-resend it over the
    # TestClient's plain-http base_url — set it explicitly, as a real browser
    # would over the stage box's actual HTTPS.
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=tenant_mismatch" in r.headers["location"]


def test_entra_callback_rejects_unknown_email(client, entra_configured, patch_entra_client):
    patch_entra_client(claims={"tid": TEST_TENANT_ID, "preferred_username": "nobody@veltris.com"})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    # The Secure cookie attribute means httpx won't auto-resend it over the
    # TestClient's plain-http base_url — set it explicitly, as a real browser
    # would over the stage box's actual HTTPS.
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=no_account" in r.headers["location"]


def test_entra_callback_rejects_token_exchange_error(client, entra_configured, patch_entra_client):
    patch_entra_client(error="invalid_grant")
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    # The Secure cookie attribute means httpx won't auto-resend it over the
    # TestClient's plain-http base_url — set it explicitly, as a real browser
    # would over the stage box's actual HTTPS.
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=token_exchange_failed" in r.headers["location"]


def test_entra_callback_success_mints_jwt_for_existing_user(
    client, entra_configured, patch_entra_client, existing_user,
):
    # Case-insensitive match against the claim, matching how Entra UPNs are cased.
    patch_entra_client(claims={"tid": TEST_TENANT_ID, "preferred_username": existing_user.email.upper()})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    # The Secure cookie attribute means httpx won't auto-resend it over the
    # TestClient's plain-http base_url — set it explicitly, as a real browser
    # would over the stage box's actual HTTPS.
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert "entra_token=" in location

    token = location.split("entra_token=", 1)[1]
    payload = AuthService.decode_token(token)
    assert payload["sub"] == existing_user.email
    assert payload["role"] == existing_user.role


def test_entra_callback_auto_provisions_admin_when_domain_matches(
    client, entra_configured, entra_auto_provision_domain, patch_entra_client, db,
):
    patch_entra_client(claims={"tid": TEST_TENANT_ID, "preferred_username": "NewPerson@Veltris.com"})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert "entra_token=" in location

    token = location.split("entra_token=", 1)[1]
    payload = AuthService.decode_token(token)
    assert payload["sub"] == "newperson@veltris.com"
    assert payload["role"] == "admin"

    created = db.query(User).filter(User.email == "newperson@veltris.com").first()
    assert created is not None
    assert created.role == "admin"
    assert created.is_active is True


def test_entra_callback_does_not_auto_provision_off_domain(
    client, entra_configured, entra_auto_provision_domain, patch_entra_client, db,
):
    patch_entra_client(claims={"tid": TEST_TENANT_ID, "preferred_username": "someone@othercompany.com"})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=no_account" in r.headers["location"]
    assert db.query(User).filter(User.email == "someone@othercompany.com").first() is None


def test_entra_callback_rejects_inactive_user(client, entra_configured, patch_entra_client, db, existing_user):
    existing_user.is_active = False
    db.commit()

    patch_entra_client(claims={"tid": TEST_TENANT_ID, "preferred_username": existing_user.email})
    login = client.get("/api/v1/auth/entra/login", follow_redirects=False)
    state = login.cookies["entra_state"]
    # The Secure cookie attribute means httpx won't auto-resend it over the
    # TestClient's plain-http base_url — set it explicitly, as a real browser
    # would over the stage box's actual HTTPS.
    client.cookies.set("entra_state", state)

    r = client.get(f"/api/v1/auth/entra/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "entra_error=no_account" in r.headers["location"]
