"""U4 / AE3: sandboxes never receive real provider keys.

E2B is mocked everywhere; no network.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.sandbox_proxy import SandboxProxyChatMessage, SandboxProxyChatRequest
from app.services.sandbox_executor import (
    _MAX_COST_PER_EXECUTION,
    SandboxExecutor,
)
from app.services.sandbox_tokens import SandboxTokenMetadata

REAL_KEYS = {
    "openai": "sk-REALOPENAIKEY1234567890",
    "anthropic": "sk-ant-REALANTHROPICKEY123",
    "openrouter": "sk-or-REALOPENROUTERKEY12",
}
PROXY = "https://api.example.test"


def _settings(**over):
    base = dict(
        ENABLE_SANDBOX=True,
        E2B_API_KEY="e2b-test",
        E2B_TEMPLATE_ID="",
        ENABLE_SANDBOX_CONTROL_PLANE=True,
        SANDBOX_PROXY_URL=PROXY,
        SANDBOX_EGRESS_ALLOW="pypi.org",
        SANDBOX_ALLOWED_MODELS="gpt-4o-mini",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _assert_no_real_keys(env: dict | None):
    blob = " ".join(f"{k}={v}" for k, v in (env or {}).items())
    for key in REAL_KEYS.values():
        assert key not in blob
    for forbidden in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        assert forbidden not in (env or {})
    if env and "OPENAI_API_KEY" in env:
        assert env["OPENAI_API_KEY"].startswith("sbx_tok_")


class _StopAfterCreate(Exception):
    pass


def _engine():
    from app.services.deep_work.engine import DeepWorkEngine

    eng = object.__new__(DeepWorkEngine)
    eng._db = MagicMock()
    eng._tenant_id = str(uuid4())
    eng._user_id = str(uuid4())
    eng._session_id = "sess-1"
    eng._agent_config = None
    eng._semaphore = __import__("asyncio").Semaphore(2)
    eng._emit = AsyncMock()
    eng._set_phase = AsyncMock()
    eng._event_sink = MagicMock()
    return eng


class TestDeepWorkEnvHasNoProviderKey:
    @pytest.mark.asyncio
    async def test_output_agent_env_is_proxy_token_only(self):
        from app.services.deep_work.models import DeepWorkPlan

        captured = {}

        async def fake_get_or_create(**kw):
            captured.update(kw)
            raise _StopAfterCreate

        executor = MagicMock()
        executor.get_or_create = fake_get_or_create
        plan = DeepWorkPlan.model_construct(
            deliverable="pdf", deliverable_description="d",
        )
        eng = _engine()
        with patch("app.services.deep_work.engine.get_settings", return_value=_settings()), \
             patch("app.services.sandbox_executor.get_sandbox_executor", return_value=executor), \
             patch("app.services.sandbox_tokens.create_sandbox_token",
                   new=AsyncMock(return_value="sbx_tok_abc")):
            with pytest.raises(_StopAfterCreate):
                await eng._generate_output(plan, "report", [], dict(REAL_KEYS), {})

        _assert_no_real_keys(captured["env_vars"])
        assert captured["env_vars"]["OPENAI_API_KEY"] == "sbx_tok_abc"
        assert captured["env_vars"]["OPENAI_BASE_URL"].startswith(PROXY)

    @pytest.mark.asyncio
    async def test_worker_env_is_proxy_token_only(self):
        from app.services.deep_work.models import WorkerTask

        captured = {}

        async def fake_get_or_create(**kw):
            captured.update(kw)
            raise RuntimeError("stop")  # engine swallows -> worker continues w/o sandbox

        executor = MagicMock()
        executor.get_or_create = fake_get_or_create
        eng = _engine()
        task = WorkerTask(worker_id="w0", sub_query="q", tools=[])
        with patch("app.services.deep_work.engine.get_settings", return_value=_settings()), \
             patch("app.services.sandbox_executor.get_sandbox_executor", return_value=executor), \
             patch("app.services.sandbox_tokens.create_sandbox_token",
                   new=AsyncMock(return_value="sbx_tok_abc")), \
             patch("app.services.deep_work.worker.DeepWorkWorker") as worker_cls:
            worker_cls.return_value.research = AsyncMock(side_effect=RuntimeError("done"))
            try:
                await eng._run_workers([task], {"mid": "m", "low": "m"}, dict(REAL_KEYS))
            except Exception:
                pass

        assert captured, "sandbox was never requested"
        _assert_no_real_keys(captured["env_vars"])
        assert captured["env_vars"]["OPENAI_API_KEY"] == "sbx_tok_abc"

    @pytest.mark.asyncio
    async def test_fail_closed_without_proxy_url(self):
        from app.services.deep_work.models import DeepWorkPlan

        executor = MagicMock()
        executor.get_or_create = AsyncMock()
        plan = DeepWorkPlan.model_construct(deliverable="pdf", deliverable_description="d")
        eng = _engine()
        with patch("app.services.deep_work.engine.get_settings",
                   return_value=_settings(SANDBOX_PROXY_URL="")), \
             patch("app.services.sandbox_executor.get_sandbox_executor", return_value=executor):
            with pytest.raises(RuntimeError, match="SANDBOX_PROXY_URL"):
                await eng._generate_output(plan, "report", [], dict(REAL_KEYS), {})
        executor.get_or_create.assert_not_called()

    def test_real_key_builder_is_gone(self):
        import app.services.deep_work.engine as engine

        assert not hasattr(engine, "_build_sandbox_env_vars")

    def test_control_plane_default_on(self):
        from app.config import Settings

        assert Settings.model_fields["ENABLE_SANDBOX_CONTROL_PLANE"].default is True


class TestExecutorFailClosed:
    @pytest.mark.asyncio
    async def test_refuses_without_proxy_url(self):
        with patch("app.services.sandbox_executor.get_settings",
                   return_value=_settings(SANDBOX_PROXY_URL="")), \
             patch("app.services.sandbox_executor.get_session_storage"):
            ex = SandboxExecutor()
            with pytest.raises(RuntimeError, match="SANDBOX_PROXY_URL"):
                await ex.get_or_create("s", "w")

    @pytest.mark.asyncio
    async def test_rejects_real_provider_key_in_env(self):
        with patch("app.services.sandbox_executor.get_settings", return_value=_settings()), \
             patch("app.services.sandbox_executor.get_session_storage"):
            ex = SandboxExecutor()
            with pytest.raises(RuntimeError, match="provider key"):
                await ex.get_or_create("s", "w", env_vars={"OPENAI_API_KEY": REAL_KEYS["openai"]})
            with pytest.raises(RuntimeError, match="provider key"):
                await ex.get_or_create("s", "w", env_vars={"ANTHROPIC_API_KEY": "x"})


class TestEgressConfig:
    @pytest.mark.asyncio
    async def test_egress_allow_list_passed_to_e2b_create(self):
        created = {}

        class FakeSandbox:
            sandbox_id = "sbx-1"

            @classmethod
            async def create(cls, **kw):
                created.update(kw)
                return cls()

        fake_mod = types.ModuleType("e2b_code_interpreter")
        fake_mod.AsyncSandbox = FakeSandbox
        with patch.dict(sys.modules, {"e2b_code_interpreter": fake_mod}), \
             patch("app.services.sandbox_executor.get_settings", return_value=_settings()), \
             patch("app.services.sandbox_executor.get_session_storage"), \
             patch("app.services.sandbox_executor._redis_get_sandbox", new=AsyncMock(return_value=None)), \
             patch("app.services.sandbox_executor._redis_set_sandbox", new=AsyncMock(return_value=True)):
            ex = SandboxExecutor()
            await ex.get_or_create("s", "w", env_vars={"OPENAI_API_KEY": "sbx_tok_x"})

        net = created["network"]
        assert "api.example.test" in net["allow_out"]  # proxy host always reachable
        assert "pypi.org" in net["allow_out"]
        assert "0.0.0.0/0" in net["deny_out"]


def _meta(**over):
    d = dict(
        tenant_id=str(uuid4()), user_id=str(uuid4()), session_id="s", worker_id="w",
        created_at=1.0, tokens_used=0, requests_count=0,
    )
    d.update(over)
    return SandboxTokenMetadata(**d)


def _body(model="gpt-4o-mini"):
    return SandboxProxyChatRequest(
        model=model, messages=[SandboxProxyChatMessage(role="user", content="hi")],
    )


def _req():
    r = MagicMock()
    r.headers = {"Authorization": "Bearer sbx_tok_abc"}
    return r


class TestProxyEnforcement:
    @pytest.mark.asyncio
    async def test_model_outside_allow_list_rejected(self):
        from app.api.v1.sandbox_proxy import proxy_chat_completions

        gateway = MagicMock()
        gateway.complete = AsyncMock()
        with patch("app.api.v1.sandbox_proxy.get_settings", return_value=_settings()), \
             patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=_meta()), \
             patch("app.api.v1.sandbox_proxy.LLMGateway", return_value=gateway):
            with pytest.raises(HTTPException) as e:
                await proxy_chat_completions(_body("gpt-5-ultra-expensive"), _req(), AsyncMock())
        assert e.value.status_code == 403
        gateway.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_accumulated_real_cost_over_cap_stops_execution(self):
        from app.api.v1.sandbox_proxy import proxy_chat_completions

        gateway = MagicMock()
        gateway.complete = AsyncMock()
        meta = _meta(cost_usd=_MAX_COST_PER_EXECUTION + 0.01)
        with patch("app.api.v1.sandbox_proxy.get_settings", return_value=_settings()), \
             patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=meta), \
             patch("app.api.v1.sandbox_proxy.LLMGateway", return_value=gateway):
            with pytest.raises(HTTPException) as e:
                await proxy_chat_completions(_body(), _req(), AsyncMock())
        assert e.value.status_code == 429
        gateway.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_records_real_cost_from_gateway(self):
        from app.api.v1.sandbox_proxy import proxy_chat_completions

        resp = MagicMock(content="ok", model="gpt-4o-mini", input_tokens=10,
                         output_tokens=5, cost_usd=0.123)
        gateway = MagicMock()
        gateway.complete = AsyncMock(return_value=resp)
        incr = AsyncMock()
        with patch("app.api.v1.sandbox_proxy.get_settings", return_value=_settings()), \
             patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=_meta()), \
             patch("app.api.v1.sandbox_proxy.LLMGateway", return_value=gateway), \
             patch("app.api.v1.sandbox_proxy.increment_sandbox_token_usage", new=incr):
            out = await proxy_chat_completions(_body(), _req(), AsyncMock())
        assert out.usage.total_tokens == 15
        assert incr.await_args.kwargs.get("cost_usd") == pytest.approx(0.123)
