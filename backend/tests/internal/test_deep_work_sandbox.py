"""Deep Work U5 / R8, R10, AE5, AE7: sandbox and output gateway tools.

E2B, Redis, the proxy-token store and the artifact store are faked; no network. The security model
(proxy token only, fail closed without SANDBOX_PROXY_URL) is asserted on the env the executor sees.
"""
import json
from types import SimpleNamespace

import pytest

from app.internal_api.tool_specs import TOOL_SPECS
from app.services import deep_work_state as dws
from app.services.deep_work import engine_sandbox
from app.services.sandbox_executor import ExecutionResult, ShellResult
from tests.internal.test_deep_work_tools import dw, learning, workers  # noqa: F401  (fixtures and helpers)
from tests.internal.test_invoke import body, effects, factory, gw, headers, invoke, maker  # noqa: F401

REAL_KEY = "sk-REALOPENAIKEY1234567890"


class FakeRedis:
    def __init__(self):
        self.h = {}

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hset(self, key, mapping):
        self.h.setdefault(key, {}).update(mapping)

    async def expire(self, key, seconds):
        return True

    async def delete(self, *keys):
        for k in keys:
            self.h.pop(k, None)


class FakeFiles:
    async def read(self, path, format="text"):
        return b"%PDF-bytes" if format == "bytes" else "text"


class FakeHandle:
    def __init__(self, world, key):
        self.world, self.key, self.token = world, key, None
        self._sandbox = SimpleNamespace(files=FakeFiles())

    async def run_shell(self, command, timeout=0):
        self.world.shell.append((self.key, command))
        return ShellResult(stdout=f"ran {command}", stderr="", exit_code=0)

    async def execute_code(self, code, timeout=0):
        self.world.executed += 1
        out = json.dumps({"path": "/workspace/r.pdf", "exists": True, "size_bytes": 10, "passed": True, "issues": []})
        return ExecutionResult(success=True, stdout=out, stderr="", duration_seconds=0.0)


class FakeExecutor:
    def __init__(self, world):
        self.world = world
        self._sandboxes = {}

    async def get_or_create(self, session_id, worker_id=None, api_key=None, env_vars=None):
        for k, v in (env_vars or {}).items():  # the executor's own guard, applied to the fake too
            assert not v.startswith("sk-"), "real provider key reached the sandbox"
        key = f"{session_id}:{worker_id}"
        if key not in self._sandboxes:
            self._sandboxes[key] = object()
            self.world.created.append((key, dict(env_vars or {})))
        return FakeHandle(self.world, key)

    async def teardown(self, session_id, worker_id=None):
        key = f"{session_id}:{worker_id}"
        self._sandboxes.pop(key, None)
        self.world.torn.append(key)

    async def teardown_session(self, session_id):
        for key in [k for k in self._sandboxes if k.startswith(f"{session_id}:")]:
            self._sandboxes.pop(key)
            self.world.torn.append(key)


class FakeArtifacts:
    store = {}

    async def write(self, tenant_id, session_id, file_path, content, metadata=None, content_type=None):
        self.store[file_path] = content
        return f"artifact://{session_id}/{file_path}"

    async def get_download_url(self, tenant_id, session_id, file_path):
        return f"https://dl.test/{session_id}/{file_path}"


@pytest.fixture
def sbx(dw, monkeypatch):
    world = SimpleNamespace(created=[], torn=[], shell=[], executed=0, revoked=[], minted=[], redis=FakeRedis())
    world.settings = SimpleNamespace(
        ENABLE_SANDBOX=True, E2B_API_KEY="e2b-test", SANDBOX_PROXY_URL="https://proxy.test",
    )
    executor = FakeExecutor(world)
    monkeypatch.setattr(engine_sandbox, "get_settings", lambda: world.settings)
    monkeypatch.setattr(engine_sandbox, "get_sandbox_executor", lambda: executor)

    async def redis():
        return world.redis

    monkeypatch.setattr(engine_sandbox, "get_redis_service", redis)

    async def create(tenant_id, user_id, session_id, worker_id, ttl_seconds=3600):
        token = f"sbx_tok_{worker_id}_{len(world.minted)}"
        world.minted.append(token)
        return token

    async def validate(token):
        return None if token in world.revoked else object()

    async def revoke(token):
        world.revoked.append(token)

    import app.services.sandbox_tokens as tokens
    import app.services.sandbox_executor as sandbox_executor
    import app.services.artifacts as artifacts

    monkeypatch.setattr(tokens, "create_sandbox_token", create)
    monkeypatch.setattr(tokens, "validate_sandbox_token", validate)
    monkeypatch.setattr(tokens, "revoke_sandbox_token", revoke)
    monkeypatch.setattr(artifacts, "ArtifactStorage", FakeArtifacts)
    FakeArtifacts.store = {}

    async def no_key(tenant_id, user_id):
        return None

    monkeypatch.setattr(sandbox_executor, "resolve_e2b_key", no_key)
    dw.sbx, dw.executor = world, executor
    return dw


async def shell(dw, worker="w1", cmd="ls", **kw):
    return await dw.call("shell_exec", {"command": cmd, "worker_id": worker}, **kw)


# ---- AE7: sandbox unavailable -------------------------------------------------------


@pytest.mark.parametrize("field,value", [("ENABLE_SANDBOX", False), ("E2B_API_KEY", ""), ("SANDBOX_PROXY_URL", "")])
async def test_unavailable_sandbox_fails_closed_and_the_run_still_completes_AE7(sbx, field, value):
    setattr(sbx.sbx.settings, field, value)
    assert not engine_sandbox.sandbox_available()
    await sbx.research("w1")
    r = await shell(sbx)
    assert r["isError"] and r["details"]["error"] == "sandbox_unavailable"
    assert sbx.sbx.created == []  # nothing was created, so no sandbox without the proxy ever exists
    assert (await sbx.call("dw_write_section", {"heading": "Findings", "markdown": "Body."}))["isError"] is False
    done = await sbx.call("dw_finalize")
    assert done["isError"] is False and done["data"]["artifacts"] == []
    assert (await sbx.state()).status == "completed"


async def test_manifest_gate_reports_availability(sbx):
    assert engine_sandbox.sandbox_available()
    assert engine_sandbox.sandbox_available("tenant-key") and engine_sandbox.sandbox_unavailable_reason() is None


# ---- worker identity, tokens, teardown -----------------------------------------------


async def test_worker_id_is_validated_against_the_round(sbx):
    await sbx.research("w1", save=False)
    r = await shell(sbx, worker="w9")
    assert r["isError"] and r["details"]["error"] == "unknown_worker"
    r = await shell(sbx, worker="../etc")
    assert r["isError"] and r["details"]["error"] == "unknown_worker"
    assert sbx.sbx.created == []


async def test_token_minted_per_worker_carries_no_real_key_and_is_reused(sbx):
    await sbx.research("w1", "w2", save=False)
    assert not (await shell(sbx, "w1", "echo 1"))["isError"]
    assert not (await shell(sbx, "w1", "echo 2"))["isError"]
    assert not (await shell(sbx, "w2", "echo 3"))["isError"]
    assert [k.split(":")[1] for k, _ in sbx.sbx.created] == ["w1", "w2"]
    for _, env in sbx.sbx.created:
        assert env["OPENAI_API_KEY"].startswith("sbx_tok_")
        assert env["OPENAI_BASE_URL"] == "https://proxy.test/api/v1/sandbox-proxy/v1"
        assert REAL_KEY not in json.dumps(env) and set(env) == {"OPENAI_API_KEY", "OPENAI_BASE_URL", "SESSION_ID"}
    assert len(sbx.sbx.minted) == 2  # one token per worker, reused across calls


async def test_a_lost_sandbox_is_recreated_with_the_same_live_token(sbx):
    await sbx.research("w1", save=False)
    await shell(sbx, "w1", "echo 1")
    sbx.executor._sandboxes.clear()  # the sandbox expired or its pod died
    r = await shell(sbx, "w1", "echo 2")
    assert not r["isError"] and len(sbx.sbx.created) == 2
    assert sbx.sbx.created[0][1]["OPENAI_API_KEY"] == sbx.sbx.created[1][1]["OPENAI_API_KEY"]
    assert len(sbx.sbx.minted) == 1


async def test_saving_learnings_tears_down_the_worker_sandbox_and_revokes_its_token(sbx):
    await sbx.research("w1", "w2", save=False)
    await shell(sbx, "w1")
    await shell(sbx, "w2")
    t1 = sbx.sbx.minted[0]
    assert not (await sbx.call("dw_save_learnings", {"worker_id": "w1", "learnings": [learning(1)]}))["isError"]
    assert sbx.sbx.torn == [f"{sbx.run_id}:w1"]
    assert sbx.sbx.revoked == [t1]
    assert f"{sbx.run_id}:w2" in sbx.executor._sandboxes  # the other worker keeps its sandbox


async def test_cancel_revokes_every_token_and_tears_down_each_sandbox_once_AE5(sbx):
    await sbx.research("w1", "w2", save=False)
    await shell(sbx, "w1")
    await shell(sbx, "w2")
    await sbx.call("inspect_artifact", {"path": "/workspace/r.pdf"})  # output_agent sandbox too
    await engine_sandbox.teardown_all(sbx.run_id)
    assert sorted(sbx.sbx.torn) == sorted(f"{sbx.run_id}:{w}" for w in ("w1", "w2", "output_agent"))
    assert sorted(sbx.sbx.revoked) == sorted(sbx.sbx.minted) and len(sbx.sbx.minted) == 3
    await engine_sandbox.teardown_all(sbx.run_id)  # repeating is a no-op
    assert len(sbx.sbx.torn) == 3 and len(sbx.sbx.revoked) == 3


async def test_reaper_runs_only_for_a_terminal_run(sbx):
    await sbx.research("w1", save=False)
    await shell(sbx, "w1")
    assert await engine_sandbox.reap_if_terminal(sbx.factory, sbx.tenant_a, sbx.conv_a) is False
    assert sbx.sbx.torn == []
    await dws.update_run(sbx.factory, sbx.tenant_a, sbx.conv_a, status="cancelled")
    assert await engine_sandbox.reap_if_terminal(sbx.factory, sbx.tenant_a, sbx.conv_a) is True
    assert sbx.sbx.torn == [f"{sbx.run_id}:w1"] and sbx.sbx.revoked == sbx.sbx.minted


async def test_a_cancelled_run_takes_no_more_sandbox_calls_AE5(sbx):
    await sbx.research("w1", save=False)
    await dws.update_run(sbx.factory, sbx.tenant_a, sbx.conv_a, status="cancelled")
    r = await shell(sbx)
    assert r["isError"] and r["details"]["error"] == "run_terminal" and sbx.sbx.created == []


# ---- idempotency classes --------------------------------------------------------------


async def test_replayed_inspect_is_safe_and_shell_stays_non_idempotent(sbx):
    assert TOOL_SPECS["inspect_artifact"].idempotent and TOOL_SPECS["inspect_artifact"].side_effect == "none"
    for name in ("shell_exec", "dw_browser_navigate", "publish_artifact"):
        assert TOOL_SPECS[name].side_effect == "write" and not TOOL_SPECS[name].idempotent
    a = await sbx.call("inspect_artifact", {"path": "/workspace/r.pdf"}, key="same")
    b = await sbx.call("inspect_artifact", {"path": "/workspace/r.pdf"}, key="same")
    assert a == b and a["data"]["passed"] is True and sbx.sbx.executed == 1


# ---- publish --------------------------------------------------------------------------


async def test_publish_returns_file_part_records_artifact_and_replaces_by_filename(sbx):
    args = {"path": "/workspace/report.pdf", "title": "Report", "artifact_type": "pdf_report", "description": "d"}
    first = await sbx.call("publish_artifact", args)
    assert first["isError"] is False
    d = first["data"]
    assert d["status"] == "published" and d["filename"] == "report.pdf" and d["size"] == len(b"%PDF-bytes")
    assert d["artifact_uri"].endswith("deliverables/report.pdf") and d["download_url"].startswith("https://dl.test/")
    assert d["file_part"]["type"] == "file" and d["file_part"]["fileType"] == "application/pdf"
    assert d["file_part"]["artifactType"] == "pdf_report" and d["file_part"]["downloadUrl"] == d["download_url"]
    assert sbx.sbx.torn == [f"{sbx.run_id}:output_agent"]  # the output sandbox ended with publishing

    second = await sbx.call("publish_artifact", {**args, "title": "Report v2"})
    assert second["isError"] is False
    artifacts = (await sbx.state()).artifacts
    assert [a["filename"] for a in artifacts] == ["report.pdf"] and artifacts[0]["title"] == "Report v2"


async def test_finalize_returns_published_artifacts_and_clears_the_output_sandbox(sbx):
    await sbx.research("w1")
    await sbx.call("publish_artifact", {"path": "/workspace/a.pdf", "title": "A", "artifact_type": "document"})
    await sbx.call("inspect_artifact", {"path": "/workspace/a.pdf"})  # recreates the output sandbox
    await sbx.call("dw_write_section", {"heading": "Findings", "markdown": "Body."})
    done = await sbx.call("dw_finalize")
    assert [a["filename"] for a in done["data"]["artifacts"]] == ["a.pdf"]
    assert sbx.sbx.torn[-1] == f"{sbx.run_id}:output_agent"
    assert f"{sbx.run_id}:output_agent" not in sbx.executor._sandboxes
