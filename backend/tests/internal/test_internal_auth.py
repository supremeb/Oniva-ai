"""U1 / R1, R2, R19 / AE1: /internal is closed unless configured and token-guarded."""
import logging

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import Settings

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
PREVIOUS = "p" * 12 + "Previous-Token-0123456789-abcdefXY"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"


def make_settings(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    from app.auth import middleware as _mw

    async def _false(*a, **k):
        return False

    monkeypatch.setattr(_mw.token_blacklist, "is_blacklisted", _false)
    monkeypatch.setattr(_mw.token_blacklist, "is_user_invalidated", _false)


def build_app(settings: Settings) -> FastAPI:
    """Same auth/CSRF/tenant stack as app.main, plus the internal mount."""
    from app.auth.middleware import AuthMiddleware
    from app.internal_api.router import mount_internal_router
    from app.middleware.csrf import CSRFMiddleware
    from app.middleware.tenant import TenantMiddleware

    app = FastAPI()
    app.add_middleware(TenantMiddleware, fail_open=True)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(AuthMiddleware)
    mount_internal_router(app, settings)
    return app


@pytest.fixture
def client():
    return TestClient(build_app(make_settings()), raise_server_exceptions=False)


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---- AE1: 401 without the service token --------------------------------

def test_valid_token_is_served(client):
    r = client.get("/internal/ping", headers=bearer(GATEWAY))
    assert r.status_code == 200


def test_no_token_401(client):
    r = client.get("/internal/ping")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}
    assert "www-authenticate" not in r.headers


def test_wrong_token_401(client):
    r = client.get("/internal/ping", headers=bearer("nope"))
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}


def test_same_length_different_bytes_401(client):
    wrong = "X" + GATEWAY[1:]
    assert len(wrong) == len(GATEWAY)
    assert client.get("/internal/ping", headers=bearer(wrong)).status_code == 401


def test_engine_service_token_is_not_the_gateway_token(client):
    # Two secrets, one per direction (KTD2).
    assert client.get("/internal/ping", headers=bearer(ENGINE_SVC)).status_code == 401


def test_user_jwt_is_rejected(client):
    tok = create_access_token(
        user_id="00000000-0000-0000-0000-0000000000aa", email="a@example.com",
        tenant_id="11111111-1111-1111-1111-111111111111", tenant_slug="a", role="admin",
    )
    assert client.get("/internal/ping", headers=bearer(tok)).status_code == 401
    assert client.get("/internal/ping", cookies={"access_token": tok}).status_code == 401


def test_query_token_and_cookie_are_not_read(client):
    assert client.get(f"/internal/ping?token={GATEWAY}").status_code == 401
    assert client.get("/internal/ping", cookies={"access_token": GATEWAY}).status_code == 401


def test_non_bearer_scheme_401(client):
    assert client.get("/internal/ping", headers={"Authorization": f"Basic {GATEWAY}"}).status_code == 401


def test_post_with_token_is_not_blocked_by_csrf(client):
    # POST /internal/ping does not exist (405), but it must get past CSRF/auth to say so.
    r = client.post("/internal/ping", headers=bearer(GATEWAY))
    assert r.status_code == 405


def test_previous_token_accepted_during_rotation():
    c = TestClient(build_app(make_settings(ENGINE_GATEWAY_TOKEN_PREVIOUS=PREVIOUS)))
    assert c.get("/internal/ping", headers=bearer(PREVIOUS)).status_code == 200
    assert c.get("/internal/ping", headers=bearer(GATEWAY)).status_code == 200


def test_previous_token_rejected_when_not_configured(client):
    assert client.get("/internal/ping", headers=bearer(PREVIOUS)).status_code == 401


def test_user_jwt_cookie_plus_service_token_is_served_as_service(client):
    tok = create_access_token(
        user_id="00000000-0000-0000-0000-0000000000aa", email="a@example.com",
        tenant_id="11111111-1111-1111-1111-111111111111", tenant_slug="a", role="admin",
    )
    # Header carries the service token, cookie carries a valid user JWT.
    app = build_app(make_settings())

    @app.get("/internal/_probe")  # added after the mount: runs behind Auth/CSRF/Tenant middleware
    async def _probe(request: Request):
        return {"user": getattr(request.state, "user", "unset"),
                "tenant": getattr(request.state, "tenant_id", "unset")}

    r = TestClient(app).get("/internal/_probe", headers=bearer(GATEWAY), cookies={"access_token": tok})
    assert r.status_code == 200
    assert r.json() == {"user": None, "tenant": None}


# ---- Closed unless configured -------------------------------------------

def test_unmounted_when_token_unset():
    s = make_settings(ENGINE_GATEWAY_TOKEN=None)
    app = build_app(s)
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/internal")]
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/internal/tools/manifest", headers=bearer(GATEWAY)).status_code == 404


def test_real_app_has_no_internal_routes_by_default():
    from app.main import app

    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/internal")]


# ---- Settings validation -------------------------------------------------

def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.ENGINE_GATEWAY_TOKEN is None and s.ENABLE_ENGINE_API is False
    assert s.INTERNAL_ALLOW_PLAINTEXT is False


def test_equal_tokens_rejected():
    with pytest.raises(ValueError, match="differ"):
        make_settings(ENGINE_SERVICE_TOKEN=GATEWAY)


def test_short_tokens_rejected():
    with pytest.raises(ValueError, match="32"):
        make_settings(ENGINE_GATEWAY_TOKEN="short")
    with pytest.raises(ValueError, match="32"):
        make_settings(ENGINE_SERVICE_TOKEN="short")
    with pytest.raises(ValueError, match="32"):
        make_settings(ENGINE_GATEWAY_TOKEN_PREVIOUS="short")


def test_token_equal_to_secret_key_rejected():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        make_settings(ENGINE_GATEWAY_TOKEN=SECRET)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        make_settings(ENGINE_SERVICE_TOKEN=SECRET)


def test_previous_equal_to_current_rejected():
    with pytest.raises(ValueError, match="differ"):
        make_settings(ENGINE_GATEWAY_TOKEN_PREVIOUS=GATEWAY)


# ---- Log masking ---------------------------------------------------------

def test_log_filter_masks_authorization_and_api_key():
    from app.internal_api.logging import RedactingFilter

    f = RedactingFilter()
    rec = logging.LogRecord("x", logging.INFO, "f", 1,
                            'Authorization: Bearer abc.def {"apiKey": "sk-secret"} apiKey=sk-other', None, None)
    assert f.filter(rec) is True
    out = rec.getMessage()
    assert "abc.def" not in out and "sk-secret" not in out and "sk-other" not in out
