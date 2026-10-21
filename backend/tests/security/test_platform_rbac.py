"""QA platform findings: RBAC on provider/audit/admin surfaces, provider-test SSRF, login lockout,
logout revocation, audit-chain verify honesty, same-password change."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1 import admin, audit, auth, llm_providers, llm_usage, observability_api
from app.auth import dependencies as deps
from app.auth.login_lockout import LoginLockout
from app.auth.providers.base import AuthUser
from app.config import get_settings
from app.core.database import get_db, get_db_no_tenant
from app.services.user_management import UserManagementService

TENANT = uuid.uuid4()
USER_ID = str(uuid.uuid4())


def _user(role):
    return AuthUser(id=USER_ID, email="u@x.io", name="U", tenant_id=str(TENANT),
                    tenant_slug="t", role=role, auth_provider="local")


class FakeDb:
    """Enough of AsyncSession for handlers that must be rejected before touching it."""
    def __init__(self):
        self.execute = AsyncMock(side_effect=AssertionError("db touched"))
        self.commit = AsyncMock()


def make_client(role, *routers):
    app = FastAPI()
    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
    user = _user(role)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_current_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = lambda: FakeDb()
    app.dependency_overrides[get_db_no_tenant] = lambda: FakeDb()
    return TestClient(app, raise_server_exceptions=False)


PROVIDER_ID = uuid.uuid4()
PROVIDER_WRITES = [
    ("post", "/llm-providers", {"name": "n", "display_name": "N", "provider_type": "openai"}),
    ("patch", f"/llm-providers/{PROVIDER_ID}", {"display_name": "x"}),
    ("delete", f"/llm-providers/{PROVIDER_ID}", None),
    ("post", "/llm-providers/test", {"provider_type": "openai", "api_key": "k"}),
    ("post", "/llm-providers/reorder", [str(PROVIDER_ID)]),
    ("get", "/llm-providers/browse-models", None),
]


@pytest.mark.parametrize("role", ["member", "viewer"])
@pytest.mark.parametrize("method,path,body", PROVIDER_WRITES)
def test_non_admin_cannot_write_or_probe_llm_providers(role, method, path, body):
    c = make_client(role, (llm_providers.router, "/api/v1/settings"))
    r = c.request(method, "/api/v1/settings" + path, json=body)
    assert r.status_code == 403, r.text


def test_member_can_still_list_providers_without_keys():
    c = make_client("member", (llm_providers.router, "/api/v1/settings"))
    # list is allowed past RBAC (it then hits the DB, which our stub rejects -> 500, not 403)
    assert c.get("/api/v1/settings/llm-providers").status_code != 403


def test_browse_models_route_not_shadowed_by_provider_id():
    c = make_client("admin", (llm_providers.router, "/api/v1/settings"))
    r = c.get("/api/v1/settings/llm-providers/browse-models", params={"provider_type": "bogus"})
    assert r.status_code == 400  # reached the handler, not a 422 UUID parse error


ADMIN_ONLY = [
    (audit.router, "/api/v1/audit", "/"),
    (audit.router, "/api/v1/audit", "/chain/verify"),
    (audit.router, "/api/v1/audit", "/compliance/stats"),
    (audit.router, "/api/v1/audit", f"/{uuid.uuid4()}"),
    (admin.router, "/api/v1", "/admin/overview"),
    (admin.router, "/api/v1", "/admin/agents"),
    (observability_api.router, "/api/v1/observability", "/traces"),
    (llm_usage.router, "/api/v1", "/llm-usage/summary"),
]


@pytest.mark.parametrize("router,prefix,path", ADMIN_ONLY)
@pytest.mark.parametrize("role", ["member", "viewer"])
def test_member_cannot_read_admin_audit_observability(router, prefix, path, role):
    c = make_client(role, (router, prefix))
    assert c.get(prefix + path).status_code == 403


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_admin_passes_rbac_gate(role):
    c = make_client(role, (audit.router, "/api/v1/audit"))
    assert c.get("/api/v1/audit/").status_code != 403


# --- SSRF on provider test ------------------------------------------------------------------

@pytest.mark.parametrize("base", [
    "http://localhost:8787", "http://127.0.0.1:11434/v1", "http://169.254.169.254/latest",
    "http://10.0.0.5", "http://[::1]:8000",
])
def test_provider_test_rejects_internal_api_base(base, monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(llm_providers, "verify_provider_connection", called)
    c = make_client("admin", (llm_providers.router, "/api/v1/settings"))
    r = c.post("/api/v1/settings/llm-providers/test",
               json={"provider_type": "custom", "api_base": base, "api_key": "k"})
    assert r.status_code == 400
    called.assert_not_called()


def test_provider_test_allows_internal_api_base_when_opted_in(monkeypatch):
    called = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(llm_providers, "verify_provider_connection", called)
    monkeypatch.setattr(get_settings(), "LLM_PROVIDER_ALLOW_PRIVATE_URLS", True)
    c = make_client("admin", (llm_providers.router, "/api/v1/settings"))
    r = c.post("/api/v1/settings/llm-providers/test",
               json={"provider_type": "custom", "api_base": "http://localhost:11434/v1", "api_key": "k"})
    assert r.status_code == 200
    called.assert_awaited_once()


# --- Login lockout --------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.data, self.ttls = {}, {}

    async def get(self, k): return self.data.get(k)
    async def incr(self, k):
        self.data[k] = int(self.data.get(k, 0)) + 1
        return self.data[k]
    async def expire(self, k, s): self.ttls[k] = s
    async def ttl(self, k): return self.ttls.get(k, -1)
    async def delete(self, k): self.data.pop(k, None)


@pytest.fixture
def login_env(monkeypatch):
    svc = MagicMock()
    svc.password_ok = False

    async def authenticate(provider, creds):
        if svc.password_ok:
            return _user("member"), {"access_token": "a", "token_type": "bearer", "expires_in": 900}
        return None, None

    svc.authenticate = authenticate
    svc.create_refresh_token_for_user = AsyncMock(return_value=("r", None))
    monkeypatch.setattr(auth, "get_auth_service", AsyncMock(return_value=svc))
    monkeypatch.setattr(auth, "login_lockout", LoginLockout(FakeRedis(), max_failures=3))
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1/auth")
    app.dependency_overrides[get_db_no_tenant] = lambda: FakeDb()
    svc.http = TestClient(app, raise_server_exceptions=False)
    return svc


def _login(env, email="a@x.io"):
    return env.http.post("/api/v1/auth/login", json={"email": email, "password": "nope"})


def test_account_locks_after_repeated_failures_even_for_correct_password(login_env):
    for _ in range(3):
        assert _login(login_env).status_code == 401
    login_env.password_ok = True
    r = _login(login_env)
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_lockout_is_indistinguishable_for_unknown_and_known_emails(login_env):
    bodies = set()
    for email in ("known@x.io", "ghost@x.io"):
        for _ in range(3):
            _login(login_env, email)
        bodies.add(_login(login_env, email).json()["detail"])
    assert len(bodies) == 1


def test_success_resets_failure_counter(login_env):
    for _ in range(2):
        _login(login_env)
    login_env.password_ok = True
    assert _login(login_env).status_code == 200
    login_env.password_ok = False
    for _ in range(2):
        assert _login(login_env).status_code == 401


# --- Logout ---------------------------------------------------------------------------------

@pytest.fixture
def logout_env(monkeypatch):
    svc = MagicMock()
    svc.revoke_all_user_tokens = AsyncMock(return_value=2)
    monkeypatch.setattr(auth, "get_auth_service", AsyncMock(return_value=svc))
    monkeypatch.setattr(auth.token_blacklist, "blacklist", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1/auth")
    app.dependency_overrides[deps.get_current_user] = lambda: _user("member")
    app.dependency_overrides[get_db_no_tenant] = lambda: FakeDb()
    svc.http = TestClient(app, raise_server_exceptions=False)
    return svc


@pytest.mark.parametrize("kwargs", [{}, {"json": {}}])
def test_logout_without_refresh_token_revokes_all_refresh_tokens(logout_env, kwargs):
    r = logout_env.http.post("/api/v1/auth/logout", **kwargs)
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 2
    logout_env.revoke_all_user_tokens.assert_awaited_once()


# --- Change password ------------------------------------------------------------------------

async def test_change_password_to_same_value_is_rejected(monkeypatch):
    pw = "Str0ng!Passw0rd#42"
    svc = UserManagementService(db=MagicMock())
    user = SimpleNamespace(email="u@x.io", hashed_password=svc.hash_password(pw))
    monkeypatch.setattr(svc, "get_user_by_id", AsyncMock(return_value=user))
    with pytest.raises(ValueError, match="different"):
        await svc.change_password(uuid.uuid4(), pw, pw, TENANT)
    other = "An0ther!Passw0rd#77"
    await svc.change_password(uuid.uuid4(), pw, other, TENANT)
    assert svc.verify_password(other, user.hashed_password)


# --- Audit chain verify ---------------------------------------------------------------------

def _verify(monkeypatch, chain_result, event_count):
    monkeypatch.setattr(audit.AuditChain, "verify_chain", AsyncMock(return_value=chain_result))
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: event_count))
    app = FastAPI()
    app.include_router(audit.router, prefix="/api/v1/audit")
    app.dependency_overrides[deps.get_current_user] = lambda: _user("admin")
    app.dependency_overrides[deps.get_current_tenant_id] = lambda: str(TENANT)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app).get("/api/v1/audit/chain/verify").json()


def test_chain_verify_is_not_vacuously_ok_when_events_exist(monkeypatch):
    body = _verify(monkeypatch, {"ok": True, "checked": 0, "legacy_unchained": 0, "first_break": None}, 55)
    assert body["ok"] is False
    assert body["unchained_audit_events"] == 55 and "Nothing could be verified" in body["message"]


def test_chain_verify_ok_when_empty_tenant(monkeypatch):
    body = _verify(monkeypatch, {"ok": True, "checked": 0, "legacy_unchained": 0, "first_break": None}, 0)
    assert body["ok"] is True


def test_chain_verify_reports_uncovered_events_alongside_ok(monkeypatch):
    body = _verify(monkeypatch, {"ok": True, "checked": 7, "legacy_unchained": 0, "first_break": None}, 3)
    assert body["ok"] is True and "not covered" in body["message"]
