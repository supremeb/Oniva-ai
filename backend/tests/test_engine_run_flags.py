"""Plan 003 U2 / KTD12, KTD16: ENABLE_ENGINE_FLEET / ENABLE_ENGINE_WORKFLOWS, capability gate, run client."""
import json
import uuid

import httpx
import pytest

from app.config import Settings
from app.models.tenant import Tenant
from app.services import engine_client as ec
from app.services import feature_flags as ff

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
FLEET, WF = "ENABLE_ENGINE_FLEET", "ENABLE_ENGINE_WORKFLOWS"
WF_KINDS = ["workflow_run", "workflow_node", "workflow_fanout_lane", "workflow_gate"]


def cfg(**kw) -> Settings:
    base = dict(SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine.test",
                ENABLE_ENGINE_API=True, ENABLE_ENGINE_FLEET=True, ENABLE_ENGINE_WORKFLOWS=True)
    base.update(kw)
    return Settings(_env_file=None, **base)


def tenant(**flags) -> Tenant:
    return Tenant(id=uuid.uuid4(), name="t", slug=f"t-{uuid.uuid4().hex[:6]}",
                  settings={"features": dict(flags)})


class Health:
    """Scripted /v1/health with a call counter and a controllable clock."""

    def __init__(self, body=None, fail=False):
        self.body = body if body is not None else {"status": "ok", "apiVersion": 1, "taskKinds": WF_KINDS}
        self.fail = fail
        self.calls = 0
        self.now = 1000.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=self.body)


@pytest.fixture
def health(monkeypatch):
    h = Health()
    ec.reset_capability_cache()
    monkeypatch.setattr(ec, "_probe_transport", httpx.MockTransport(h.handler))
    monkeypatch.setattr(ec, "_monotonic", lambda: h.now)
    yield h
    ec.reset_capability_cache()


def test_defaults_off():
    s = Settings(_env_file=None, SECRET_KEY=SECRET)
    assert s.ENABLE_ENGINE_FLEET is False and s.ENABLE_ENGINE_WORKFLOWS is False


def test_flag_matrix(health):
    s = cfg()
    assert ff.engine_fleet_enabled(tenant(**{FLEET: True}), s) is True
    assert ff.engine_workflows_enabled(tenant(**{WF: True}), s) is True
    assert ff.engine_fleet_enabled(tenant(**{FLEET: False}), s) is False
    assert ff.engine_fleet_enabled(tenant(), s) is True  # absent: on (the rollout's opt-out default)
    assert ff.engine_workflows_enabled(tenant(), s) is True
    # each flag opts out on its own
    assert ff.engine_workflows_enabled(tenant(**{FLEET: False}), s) is True
    assert ff.engine_workflows_enabled(tenant(**{WF: False}), s) is False


@pytest.mark.parametrize("override", [
    dict(ENABLE_ENGINE_FLEET=False),
    dict(ENABLE_ENGINE_API=False),
    dict(ENGINE_URL=""),
    dict(ENGINE_SERVICE_TOKEN=None),
])
def test_global_off_beats_tenant_on_and_missing_config_disables(health, override):
    assert ff.engine_fleet_enabled(tenant(**{FLEET: True}), cfg(**override)) is False


def test_global_workflows_off_beats_tenant_on(health):
    assert ff.engine_workflows_enabled(tenant(**{WF: True}), cfg(ENABLE_ENGINE_WORKFLOWS=False)) is False


@pytest.mark.parametrize("body", [
    {"status": "ok"},                                                   # no apiVersion
    {"status": "ok", "apiVersion": 0, "taskKinds": WF_KINDS},           # older
    {"status": "ok", "apiVersion": "x", "taskKinds": WF_KINDS},         # garbage
])
def test_capability_missing_or_old_version_turns_both_flags_off(health, body):
    health.body = body
    s = cfg()
    assert ff.engine_fleet_enabled(tenant(**{FLEET: True}), s) is False
    assert ff.engine_workflows_enabled(tenant(**{WF: True}), s) is False


def test_missing_task_kind_turns_workflows_off_but_not_fleet(health):
    health.body = {"status": "ok", "apiVersion": 1, "taskKinds": ["workflow_run", "workflow_node"]}
    s = cfg()
    assert ff.engine_workflows_enabled(tenant(**{WF: True}), s) is False
    assert ff.engine_fleet_enabled(tenant(**{FLEET: True}), s) is True


def test_newer_engine_passes(health):
    health.body = {"status": "ok", "apiVersion": ec.REQUIRED_API_VERSION + 3,
                   "taskKinds": WF_KINDS + ["something_new"]}
    s = cfg()
    assert ff.engine_fleet_enabled(tenant(**{FLEET: True}), s) is True
    assert ff.engine_workflows_enabled(tenant(**{WF: True}), s) is True


def test_probe_is_cached_across_many_reads(health):
    s = cfg()
    t = tenant(**{FLEET: True, WF: True})
    for _ in range(20):
        ff.engine_fleet_enabled(t, s)
        ff.engine_workflows_enabled(t, s)
    assert health.calls == 1
    health.now += 61
    ff.engine_fleet_enabled(t, s)
    assert health.calls == 2


def test_failed_probe_reads_off_and_is_retried_after_ten_seconds(health):
    health.fail = True
    s = cfg()
    t = tenant(**{FLEET: True})
    assert ff.engine_fleet_enabled(t, s) is False
    assert ff.engine_fleet_enabled(t, s) is False
    assert health.calls == 1
    health.fail = False
    health.now += 5
    assert ff.engine_fleet_enabled(t, s) is False and health.calls == 1
    health.now += 6
    assert ff.engine_fleet_enabled(t, s) is True and health.calls == 2


def test_probe_not_made_when_flag_conditions_already_fail(health):
    ff.engine_fleet_enabled(tenant(**{FLEET: False}), cfg())  # tenant opted out
    ff.engine_fleet_enabled(tenant(**{FLEET: True}), cfg(ENABLE_ENGINE_FLEET=False))
    assert health.calls == 0


def test_get_tenant_features_includes_both_flags(health, monkeypatch):
    monkeypatch.setattr(ff, "get_settings", lambda: cfg())
    t = tenant(**{FLEET: True})
    feats = ff.get_tenant_features(t)
    assert feats[FLEET] is True and feats[WF] is True  # absent tenant setting: on
    assert ff.is_known_flag(FLEET) and ff.is_operator_only(WF)
    assert ff.get_feature(t, FLEET) is True


async def test_async_capabilities_share_the_cache(health):
    client = ec.EngineClient(cfg(), transport=httpx.MockTransport(health.handler))
    caps = await client.capabilities()
    assert caps == {"apiVersion": 1, "taskKinds": WF_KINDS}
    await client.capabilities()
    assert health.calls == 1
    await client.aclose()


# ---- run client --------------------------------------------------------------

def make_client(handler) -> ec.EngineClient:
    return ec.EngineClient(cfg(), transport=httpx.MockTransport(handler))


async def test_start_run_posts_with_idempotency_key_and_headers():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(method=req.method, path=req.url.path, headers=req.headers, body=json.loads(req.content))
        return httpx.Response(202, json={"conversationId": "c1", "taskId": "t1"})

    c = make_client(handler)
    body = {"id": "c1", "kind": "workflow_run", "input": {"a": 1}}
    out = await c.start_run("s", "ten", "proj", body, idempotency_key="wf:c1")
    assert out == {"conversationId": "c1", "taskId": "t1"}
    assert (seen["method"], seen["path"]) == ("POST", "/v1/runs")
    assert seen["headers"]["Idempotency-Key"] == "wf:c1"
    assert seen["headers"]["X-Oniva-Tenant"] == "ten" and seen["headers"]["X-Oniva-Project"] == "proj"
    assert seen["body"] == body
    await c.aclose()


async def test_start_run_does_not_retry_and_maps_not_owner():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(409, json={"error": "not_owner"})

    c = make_client(handler)
    with pytest.raises(ec.EngineError) as ei:
        await c.start_run("s", "ten", "proj", {"id": "c1"}, idempotency_key="k")
    assert ei.value.status == 409 and ei.value.error == "not_owner"
    assert len(calls) == 1
    await c.aclose()


async def test_signal_task_and_get_task_routes():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        return httpx.Response(200, json={"ok": True, "id": "t1", "status": "waiting"})

    c = make_client(handler)
    await c.signal_task("s", "ten", "proj", "c1", "t1", "approval", {"decision": "approve"})
    t = await c.get_task("s", "ten", "proj", "c1", "t1")
    assert t["status"] == "waiting"
    assert seen[0] == ("POST", "/v1/conversations/c1/tasks/t1/signal",
                       {"key": "approval", "payload": {"decision": "approve"}})
    assert seen[1][:2] == ("GET", "/v1/conversations/c1/tasks/t1")
    await c.aclose()


async def test_warm_probes_in_a_thread_not_on_the_event_loop_and_fills_the_cache(health, monkeypatch):
    import threading

    idents = []
    inner = health.handler

    def handler(request):
        idents.append(threading.get_ident())
        return inner(request)

    monkeypatch.setattr(ec, "_probe_transport", httpx.MockTransport(handler))
    s = cfg()
    await ec.warm_engine_capability(s)
    assert health.calls == 1 and idents and idents[0] != threading.get_ident()
    await ec.warm_engine_capability(s)  # fresh: no second probe
    assert ec.engine_has_capability(s, tuple(WF_KINDS)) is True and health.calls == 1
    health.now += 61.0  # expired: the next warm probes again, still off the loop
    await ec.warm_engine_capability(s)
    assert health.calls == 2 and all(i != threading.get_ident() for i in idents)


async def test_warm_without_an_engine_url_does_nothing(health):
    await ec.warm_engine_capability(cfg(ENGINE_URL=""))
    assert health.calls == 0
