"""U2 / R1 / KTD3: the API is deny-by-default.

Every route in the app's route table must answer 401 to an unauthenticated
request unless it is on the explicit public allow-list. The allow-list is
pinned here (PINNED_PUBLIC), so widening it in app code without updating this
test fails the suite.
"""
import re

import pytest
from fastapi import routing
from starlette.testclient import TestClient

from app.main import app

# Exactly what may be reached without a user token. Anything else is a bug.
PINNED_PUBLIC = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    # Interactive API docs (non-production only; see test_docs_denied_in_production)
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    # Auth bootstrap
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/auth/login/{provider}"),
    ("POST", "/api/v1/auth/callback/{provider}"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/auth/providers"),
    ("GET", "/api/v1/auth/stub-users"),
    ("GET", "/api/v1/auth/check-email/{email}"),
    ("POST", "/api/v1/auth/signup"),
    # SAML flows (assertion/signature authenticated)
    ("GET", "/api/v1/auth/saml/status"),
    ("GET", "/api/v1/auth/saml/metadata"),
    ("GET", "/api/v1/auth/saml/login"),
    ("POST", "/api/v1/auth/saml/acs"),
    ("GET", "/api/v1/auth/saml/logout"),
    ("GET", "/api/v1/auth/saml/sls"),
    ("POST", "/api/v1/auth/saml/sls"),
    # Onboarding bootstrap
    ("POST", "/api/v1/onboarding/check-email"),
    ("POST", "/api/v1/onboarding/register"),
    ("GET", "/api/v1/onboarding/check-slug/{slug}"),
    # Machine-authenticated
    ("POST", "/api/v1/oauth/token"),  # client credentials
    ("POST", "/api/v1/webhooks/{url_token}"),  # HMAC / per-trigger auth
    ("POST", "/api/v1/sandbox-proxy/v1/chat/completions"),  # sandbox proxy token
    ("POST", "/api/v1/csp-report"),  # browser-originated CSP reports
}


def _walk(routes):
    for r in routes:
        if isinstance(r, routing._IncludedRouter):
            ctx = r.effective_route_contexts
            yield from _walk(ctx() if callable(ctx) else ctx)
        else:
            yield r


def _all_routes():
    out = []
    for r in _walk(app.routes):
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        if not methods or not path:
            continue
        for m in sorted(methods):
            if m in ("HEAD", "OPTIONS"):
                continue
            out.append((m, path))
    return sorted(set(out))


ROUTES = _all_routes()
# /internal is a distinct class: it bypasses user JWT auth but is guarded by the
# engine service token (InternalAuth dependency). It is never in PINNED_PUBLIC; the
# router is only mounted when ENGINE_GATEWAY_TOKEN is set, so the default app has none.
INTERNAL_PREFIX = "/internal/"
PROTECTED = [(m, p) for m, p in ROUTES
             if (m, p) not in PINNED_PUBLIC and not p.startswith(INTERNAL_PREFIX)]


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)



@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Rate limiter and token blacklist talk to Redis; unit tier has none."""
    from app.auth import middleware as _mw
    from app.middleware.rate_limit import RateLimiter

    async def _allowed(self, identifier, limit_override=None):
        return True, {"limit": 1000, "remaining": 999, "reset": 0}

    async def _false(*a, **k):
        return False

    monkeypatch.setattr(RateLimiter, "is_allowed", _allowed)
    monkeypatch.setattr(_mw.token_blacklist, "is_blacklisted", _false)
    monkeypatch.setattr(_mw.token_blacklist, "is_user_invalidated", _false)


@pytest.fixture
def client():
    # No lifespan: we only care about the middleware stack.
    return TestClient(app, raise_server_exceptions=False)


def test_route_table_is_nonempty():
    assert len(ROUTES) > 300, "route enumeration broke; the deny-by-default test would pass vacuously"
    assert len(PROTECTED) > 300


def test_pinned_public_routes_all_exist():
    existing = set(ROUTES)
    missing = PINNED_PUBLIC - existing
    assert not missing, f"stale allow-list entries: {sorted(missing)}"


@pytest.mark.parametrize("method,path", PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
def test_protected_route_returns_401_without_auth(client, method, path):
    resp = client.request(method, _concrete(path))
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", PROTECTED[:40], ids=[f"{m} {p}" for m, p in PROTECTED[:40]])
def test_protected_route_returns_401_with_garbage_bearer(client, method, path):
    """A bearer header bypasses CSRF, so this is the path a scripted attacker takes."""
    resp = client.request(method, _concrete(path), headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_unknown_path_is_denied_not_404(client, method):
    resp = client.request(method, "/api/v1/definitely/not/a/route")
    assert resp.status_code == 401


def test_junk_api_key_does_not_authenticate(client):
    resp = client.get("/api/v1/connectors/", headers={"X-API-Key": "junk"})
    assert resp.status_code == 401


def test_public_prefix_lookalikes_are_not_public(client):
    # Old prefix matching treated anything starting with /docs or /redoc as public.
    for p in ("/docsfoo", "/redoc-secret", "/api/v1/auth/check-email", "/api/v1/auth/loginx"):
        assert client.get(p).status_code == 401, p


def test_public_route_is_method_specific(client):
    # POST /webhooks/{token} is public, DELETE /webhooks/{id} is not.
    assert client.delete("/api/v1/webhooks/abc").status_code == 401
    # GET /api/v1/auth/login (no provider) is not a public route either.
    assert client.put("/api/v1/auth/login").status_code == 401


def test_preflight_is_not_blocked_by_auth(client):
    resp = client.options(
        "/api/v1/connectors/",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code != 401


class TestPublicRoutesStillWork:
    def test_health(self, client):
        assert client.get("/health").status_code != 401
        assert client.get("/health/live").status_code == 200

    def test_root(self, client):
        assert client.get("/").status_code == 200

    def test_login_reaches_handler(self, client):
        # Handler runs (422 for missing body), i.e. it is not rejected by the auth gate.
        assert client.post("/api/v1/auth/login", json={}).status_code != 401

    def test_webhook_reaches_handler_without_user_token(self, client):
        # Unknown token: handler answers (404/4xx from the webhook service), never the auth gate's 401.
        resp = client.post("/api/v1/webhooks/unknown-token", json={"a": 1})
        assert resp.status_code != 401 or "Authentication required" not in resp.text

    def test_sandbox_proxy_uses_its_own_token(self, client):
        resp = client.post(
            "/api/v1/sandbox-proxy/v1/chat/completions",
            json={},
            headers={"Authorization": "Bearer sbx_tok_bogus"},
        )
        # Passes the user-auth gate (and CSRF); its own handler decides, not "Authentication required".
        assert "Authentication required" not in resp.text

    def test_webhook_post_is_not_blocked_by_csrf(self, client):
        # Webhook senders have no cookies and no CSRF token.
        resp = client.post("/api/v1/webhooks/unknown-token", json={"a": 1})
        assert resp.status_code != 403 or "CSRF" not in resp.text

    def test_valid_token_passes_gate(self, client):
        from app.auth.jwt import create_access_token

        tok = create_access_token(
            user_id="00000000-0000-0000-0000-0000000000aa",
            email="a@example.com",
            tenant_id="11111111-1111-1111-1111-111111111111",
            tenant_slug="a",
            role="admin",
        )
        resp = client.get("/api/v1/connectors/", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 200


def test_docs_denied_in_production(monkeypatch):
    from app.auth import middleware

    monkeypatch.setattr(middleware, "_is_production", lambda: True)
    assert not middleware.is_public_route("GET", "/docs")
    assert not middleware.is_public_route("GET", "/openapi.json")
    monkeypatch.setattr(middleware, "_is_production", lambda: False)
    assert middleware.is_public_route("GET", "/docs")


# ---- /internal: token-guarded, not public, not user-JWT-authenticated -------

def _internal_app():
    from fastapi import FastAPI

    from app.auth.middleware import AuthMiddleware
    from app.config import Settings
    from app.internal_api.router import mount_internal_router
    from app.middleware.csrf import CSRFMiddleware
    from app.middleware.tenant import TenantMiddleware

    gw = "g" * 40
    s = Settings(_env_file=None, ENGINE_GATEWAY_TOKEN=gw, ENGINE_SERVICE_TOKEN="e" * 40, SECRET_KEY="k" * 40)
    a = FastAPI()
    a.add_middleware(TenantMiddleware, fail_open=True)
    a.add_middleware(CSRFMiddleware)
    a.add_middleware(AuthMiddleware)
    mount_internal_router(a, s)
    return a, gw


def test_default_app_mounts_no_internal_routes():
    assert not [p for _, p in ROUTES if p.startswith("/internal")]
    assert not [(m, p) for m, p in PINNED_PUBLIC if p.startswith("/internal")]


def test_internal_routes_are_not_public_by_the_user_auth_allow_list():
    from app.auth.middleware import is_public_route

    assert not is_public_route("GET", "/internal/ping")
    assert not is_public_route("POST", "/internal/tools/invoke")


def test_every_internal_route_401_without_token_and_with_wrong_token():
    a, gw = _internal_app()
    internal = [(m, p) for r in _walk(a.routes) for m in sorted(getattr(r, "methods", None) or ())
                for p in [getattr(r, "path", "")] if p.startswith("/internal") and m not in ("HEAD", "OPTIONS")]
    assert internal, "no /internal routes enumerated"
    c = TestClient(a, raise_server_exceptions=False)
    for m, p in internal:
        assert c.request(m, _concrete(p)).status_code == 401, (m, p)
        assert c.request(m, _concrete(p), headers={"Authorization": "Bearer wrong"}).status_code == 401, (m, p)


def test_browser_refresh_with_empty_body_reaches_the_cookie_check(client):
    # Browsers send `{}` and carry the refresh token in the httpOnly cookie. Rejecting the empty body
    # with 422 logged every user out when the access token expired.
    r = client.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 401, r.text
