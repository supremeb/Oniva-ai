"""Engine sharding: automation runs go to a background shard, chat stays on the default one.

Unsharded (the default) every session is the one configured session and every URL the one ``ENGINE_URL``.
"""
import uuid

import httpx
import pytest

from app.config import Settings
from app.services import engine_client as ec
from app.services.engine_directory import BACKGROUND_RUN_KINDS, EngineDirectory

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SVC = "s" * 12 + "Engine-Svc-Token-0123456789-abcdXY"


def cfg(**kw) -> Settings:
    base = dict(_env_file=None, SECRET_KEY=SECRET, ENGINE_SERVICE_TOKEN=SVC, ENGINE_URL="http://engine-i:8080",
                ENGINE_SESSION_ID="sess-i", ENABLE_ENGINE_API=True, ENABLE_ENGINE_FLEET=True,
                ENABLE_ENGINE_WORKFLOWS=True)
    base.update(kw)
    return Settings(**base)


SHARDED = dict(ENGINE_BACKGROUND_URL="http://engine-b:8080/", ENGINE_BACKGROUND_SESSION_ID="sess-b")


def test_unsharded_directory_is_unchanged():
    d = EngineDirectory(cfg())
    assert not d.sharded
    for kind in (*BACKGROUND_RUN_KINDS, "chat", "deep_work"):
        assert d.session_for(uuid.uuid4(), kind) == "sess-i"
    assert d.url_for("sess-i") == d.url_for("anything") == d.background_url == "http://engine-i:8080"


def test_sharded_directory_routes_automation_to_the_background_shard():
    d = EngineDirectory(cfg(**SHARDED))
    assert d.sharded
    assert d.default_session_id == "sess-i"
    for kind in BACKGROUND_RUN_KINDS:
        assert d.session_for(uuid.uuid4(), kind) == "sess-b"
    for kind in ("chat", "deep_work", "anything-else"):
        assert d.session_for(uuid.uuid4(), kind) == "sess-i"
    assert d.url_for("sess-b") == d.background_url == "http://engine-b:8080"
    assert d.url_for("sess-i") == "http://engine-i:8080"
    # A conversation bound before sharding keeps its recorded session and so its recorded engine.
    assert d.url_for("some-recorded-session") == "http://engine-i:8080"


def test_half_configured_background_shard_is_a_configuration_error():
    for half in (dict(ENGINE_BACKGROUND_URL="http://engine-b:8080"), dict(ENGINE_BACKGROUND_SESSION_ID="sess-b")):
        with pytest.raises(ValueError, match="must be set together"):
            cfg(**half)


def test_a_session_id_shared_by_both_shards_is_a_configuration_error():
    with pytest.raises(ValueError, match="must differ"):
        cfg(**{**SHARDED, "ENGINE_BACKGROUND_SESSION_ID": "sess-i"})


@pytest.fixture(autouse=True)
def _reset():
    ec.reset_capability_cache()
    yield
    ec.reset_capability_cache()
    ec._probe_transport = None


def _probe(monkeypatch, bodies: dict):
    """Serve /v1/health per host; return the hosts probed."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=bodies[request.url.host])

    monkeypatch.setattr(ec, "_probe_transport", httpx.MockTransport(handler))
    return hosts


WF = list(ec.WORKFLOW_TASK_KINDS)


def test_workflow_capability_is_read_from_the_background_shard(monkeypatch):
    # The interactive shard has no workflow kinds (by design); the background one has them.
    hosts = _probe(monkeypatch, {"engine-i": {"apiVersion": 1, "taskKinds": []},
                                "engine-b": {"apiVersion": 1, "taskKinds": WF}})
    s = cfg(**SHARDED)
    assert ec.engine_has_capability(s, tuple(WF), background=True) is True
    assert hosts == ["engine-b"]
    assert ec.engine_has_capability(s, tuple(WF)) is False  # the default shard lacks them


def test_capability_probe_without_a_background_shard_uses_the_one_engine(monkeypatch):
    hosts = _probe(monkeypatch, {"engine-i": {"apiVersion": 1, "taskKinds": WF}})
    assert ec.engine_has_capability(cfg(), tuple(WF), background=True) is True
    assert hosts == ["engine-i"]


async def test_warm_probes_both_shards(monkeypatch):
    hosts = _probe(monkeypatch, {"engine-i": {"apiVersion": 1, "taskKinds": []},
                                "engine-b": {"apiVersion": 1, "taskKinds": WF}})
    await ec.warm_engine_capability(cfg(**SHARDED))
    assert sorted(hosts) == ["engine-b", "engine-i"]


async def test_client_sends_each_call_to_its_sessions_engine():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path))
        return httpx.Response(200, json={"taskId": "t"})

    client = ec.EngineClient(cfg(**SHARDED), transport=httpx.MockTransport(handler))
    await client.start_run("sess-b", "t", "p", {"id": "c", "kind": "workflow_run"}, "k1")
    await client.submit("sess-i", "t", "p", "c2", {"text": "hi"}, "k2")
    await client.aclose()
    assert seen == [("engine-b", "/v1/runs"), ("engine-i", "/v1/conversations/c2/submissions")]
