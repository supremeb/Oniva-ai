"""U1 / R1-R3 / AE1: per-tenant ENABLE_ENGINE_DEEP_WORK resolution and endpoint dispatch."""
import uuid

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1 import deep_work as dw
from app.api.v1 import deep_work_engine as dwe
from app.auth.dependencies import get_current_tenant_id, get_current_user_id
from app.config import Settings
from app.core.database import get_db
from app.models.tenant import Tenant
from app.services import feature_flags as ff

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
FLAG = "ENABLE_ENGINE_DEEP_WORK"


def cfg(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                ENABLE_ENGINE_API=True, ENABLE_ENGINE_DEEP_WORK=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


def tenant(flag=None) -> Tenant:
    features = {} if flag is None else {FLAG: flag}
    return Tenant(id=uuid.uuid4(), name="t", slug=f"t-{uuid.uuid4().hex[:6]}", settings={"features": features})


def test_defaults():
    s = Settings(_env_file=None, SECRET_KEY=SECRET)
    assert s.ENABLE_ENGINE_DEEP_WORK is False
    assert s.DEEP_WORK_MAX_WORKERS == 10


def test_tenant_on_off_and_unset():
    s = cfg()
    assert ff.engine_deep_work_enabled(tenant(True), s) is True
    assert ff.engine_deep_work_enabled(tenant(False), s) is False
    assert ff.engine_deep_work_enabled(tenant(None), s) is False


@pytest.mark.parametrize("override", [
    dict(ENABLE_ENGINE_DEEP_WORK=False),
    dict(ENABLE_ENGINE_API=False),
    dict(ENGINE_URL=""),
    dict(ENGINE_SERVICE_TOKEN=None),
])
def test_global_conditions_override_tenant_true(override):
    assert ff.engine_deep_work_enabled(tenant(True), cfg(**override)) is False


def test_chat_flag_does_not_enable_deep_work_and_vice_versa():
    s = cfg(ENABLE_ENGINE_CHAT=True)
    t = Tenant(id=uuid.uuid4(), name="t", slug="t", settings={"features": {"ENABLE_ENGINE_CHAT": True}})
    assert ff.engine_deep_work_enabled(t, s) is False
    t2 = tenant(True)
    assert ff.engine_chat_enabled(t2, s) is False


def test_feature_map_and_metadata(monkeypatch):
    on, glob_off = tenant(True), tenant(True)
    monkeypatch.setattr(ff, "get_settings", lambda: cfg())
    assert ff.get_tenant_features(on)[FLAG] is True
    assert ff.get_feature(on, FLAG) is True
    monkeypatch.setattr(ff, "get_settings", lambda: cfg(ENABLE_ENGINE_DEEP_WORK=False))
    assert ff.get_tenant_features(glob_off)[FLAG] is False
    assert ff.get_feature(glob_off, FLAG) is False
    assert ff.is_known_flag(FLAG) and ff.is_operator_only(FLAG)


# ---- dispatch (AE1) ---------------------------------------------------------

class FakeDB:
    def __init__(self, *tenants):
        self.tenants = {str(t.id): t for t in tenants}

    async def get(self, model, key):
        return self.tenants.get(str(key)) if model is Tenant else None


def make_client(t: Tenant, settings: Settings, *others: Tenant):
    app = FastAPI()
    app.state.engine_deep_work_settings = settings
    app.include_router(dw.router, prefix="/api/v1")

    db = FakeDB(t, *others)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_tenant_id] = lambda: t.id
    app.dependency_overrides[get_current_user_id] = lambda: uuid.uuid4()
    return TestClient(app, raise_server_exceptions=False)


def test_ae1_engine_tenant_dispatches_off_tenant_stays_legacy_and_gets_404_on_engine_only(monkeypatch):
    a, b = tenant(True), tenant(False)
    s = cfg()
    legacy_calls = []

    async def legacy_config(db, tenant_id):
        legacy_calls.append(tenant_id)
        raise RuntimeError("legacy handler reached")

    monkeypatch.setattr(dw, "_get_general_assistant_config", legacy_config)

    ca = make_client(a, s, b)
    for path, body in [("clarify", {"query": "research query"}), ("plan", {"query": "research query"}), ("", {"query": "research query"})]:
        r = ca.post(f"/api/v1/deep-work/engine/{path}".rstrip("/"), json=body)
        assert r.status_code in (422, 500), path  # engine handler (needs project / engine), not legacy
    assert legacy_calls == []
    # Steer passes the gate for the flagged tenant (then fails on the unknown session), unlike tenant B below.
    assert ca.post("/api/v1/deep-work/s1/steer", json={"text": "x"}).status_code == 404

    cb = make_client(b, s, a)
    for path in ["clarify", "plan"]:
        cb.post(f"/api/v1/deep-work/engine/{path}", json={"query": "research query"})
    assert len(legacy_calls) == 2  # tenant B reached the legacy handlers
    assert cb.post("/api/v1/deep-work/s1/steer").status_code == 404


def test_global_off_and_unconfigured_engine_keep_legacy(monkeypatch):
    a = tenant(True)
    reached = []

    async def legacy_config(db, tenant_id):
        reached.append(1)
        raise RuntimeError

    monkeypatch.setattr(dw, "_get_general_assistant_config", legacy_config)
    for s in (cfg(ENABLE_ENGINE_DEEP_WORK=False), cfg(ENGINE_URL="")):
        c = make_client(a, s)
        c.post("/api/v1/deep-work/engine/clarify", json={"query": "research query"})
        assert c.post("/api/v1/deep-work/s1/steer").status_code == 404
    assert len(reached) == 2


def test_legacy_session_routes_unchanged_by_flag():
    # Session-scoped legacy routes carry no engine dispatch, so a legacy session keeps working
    # after the flag flips on (R3); the engine cancel delegation arrives with is_engine in U3/U7.
    import inspect
    for fn in (dw.cancel_deep_work_engine, dw.stop_deep_work, dw.get_session_status, dw.deep_work_events):
        assert "tenant_uses_engine_deep_work" not in inspect.getsource(fn)
