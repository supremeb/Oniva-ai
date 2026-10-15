"""U4 / R5, R9 / AE8: scoped execution context and tool guards."""
import asyncio
from uuid import uuid4

import pytest

from app.agents.skills.registry import Skill, _registry
from app.internal_api import execution
from app.internal_api.execution import (
    MAX_OUTCOME_BYTES,
    ToolRateLimited,
    ToolRateLimiter,
    execute_tool,
    parse_rate_limit,
    resolve_tool_keys,
)
from app.internal_api.tool_specs import ToolSpec

TENANT = str(uuid4())
OTHER_TENANT = str(uuid4())
COMMON = dict(tenant_id=TENANT, project_id=str(uuid4()), user_id=str(uuid4()), conversation_id=str(uuid4()))


class FakeSession:
    def __init__(self):
        self.executed = []
        self.closed = False
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True

    @property
    def tenant(self):
        for sql, params in self.executed:
            if "app.current_tenant" in sql:
                return params["tenant_id"]
        return None


@pytest.fixture
def sessions(monkeypatch):
    made = []

    def factory():
        s = FakeSession()
        made.append(s)
        return s

    monkeypatch.setattr(execution, "_session_factory", lambda: factory)
    return made


STORE = {"tavily": "tvly-AAAAAAAAAAAA", "exa": "exa-BBBBBBBBBBBB", "openai": "sk-" + "C" * 30}


async def fake_keys(session, tenant_id, providers):
    # Honest resolver: returns whatever it is asked for from a store holding more.
    return {p: STORE[p] for p in providers if p in STORE}


@pytest.fixture
def spy():
    seen = {}

    async def handler(ctx, **kwargs):
        seen["ctx"] = ctx
        seen["kwargs"] = kwargs
        seen["tenant_at_call"] = None
        return {"ok": True}

    def register(name="spy_tool", fn=None):
        _registry[name] = Skill(name=name, description="spy", category="test", handler=fn or handler)
        return seen

    yield register
    for n in [k for k in _registry if k.startswith("spy_")]:
        del _registry[n]


async def run(spec, name="spy_tool", args=None, **kw):
    return await execute_tool(
        name, args or {}, spec=spec, key_resolver=kw.pop("key_resolver", fake_keys),
        connector_resolver=kw.pop("connector_resolver", None) or (lambda s, t: _none()),
        limiter=ToolRateLimiter(), run_id=str(uuid4()), tool_call_id="tc-1", **COMMON, **kw,
    )


async def _none():
    return {}


async def test_declared_key_only(spy, sessions):
    seen = spy()
    out = await run(ToolSpec(needs_keys=("exa",)))
    assert not out.is_error
    assert seen["ctx"].api_keys == {"exa": STORE["exa"]}
    assert seen["ctx"].db is None
    assert seen["ctx"].connectors == {}
    assert all(s.closed for s in sessions)  # key-lookup session closed before the tool ran


async def test_undeclared_gets_nothing_and_opens_no_session(spy, sessions):
    seen = spy()
    await run(ToolSpec())
    ctx = seen["ctx"]
    assert ctx.api_keys == {} and ctx.db is None and ctx.connectors == {}
    assert sessions == []


async def test_gateway_metadata(spy, sessions):
    seen = spy()
    await run(ToolSpec(), args={"q": 1})
    ctx = seen["ctx"]
    assert seen["kwargs"] == {"q": 1}
    assert ctx.session_id == ctx.metadata["run_id"] == str(ctx.workflow_run_id)
    assert ctx.metadata["tool_call_id"] == "tc-1"


async def test_needs_db_gets_fresh_rls_bound_session(spy, sessions):
    live = {}

    async def handler(ctx, **kw):
        live["closed_during_call"] = ctx.db.closed
        live["tenant_during_call"] = ctx.db.tenant
        live["db"] = ctx.db
        return "x"

    spy(fn=handler)
    await run(ToolSpec(needs_db=True))
    await run(ToolSpec(needs_db=True))
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert live["closed_during_call"] is False
    assert live["tenant_during_call"] == TENANT
    assert all(s.tenant == TENANT and s.closed and s.committed for s in sessions)


async def test_db_session_rolled_back_and_closed_on_error(spy, sessions):
    async def boom(ctx, **kw):
        raise RuntimeError("secret sk-" + "Z" * 30)

    spy(fn=boom)
    out = await run(ToolSpec(needs_db=True))
    assert out.is_error and out.details["error"] == "RuntimeError"
    assert "sk-" not in out.content and "Traceback" not in out.content
    assert sessions[0].rolled_back and sessions[0].closed and not sessions[0].committed


async def test_connectors_only_when_declared(spy, sessions):
    seen = spy()

    async def conns(session, tenant_id):
        return {"bank": {"provider": "bank"}}

    await run(ToolSpec(), connector_resolver=conns)
    assert seen["ctx"].connectors == {}
    await run(ToolSpec(needs_connectors=True), connector_resolver=conns)
    assert seen["ctx"].connectors == {"bank": {"provider": "bank"}}


async def test_default_resolver_returns_only_requested(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-env-key-123")
    monkeypatch.setenv("EXA_API_KEY", "exa-env-key-12345")

    class Broken(FakeSession):
        async def execute(self, *a, **k):
            raise RuntimeError("no db")

    assert await resolve_tool_keys(Broken(), TENANT, ("exa",)) == {"exa": "exa-env-key-12345"}
    assert await resolve_tool_keys(Broken(), TENANT, ()) == {}


async def test_timeout_cancels_tool(spy, sessions):
    state = {}

    async def slow(ctx, **kw):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    spy(fn=slow)
    out = await run(ToolSpec(), timeout_ms=50)
    assert out.is_error and out.details["error"] == "timeout"
    assert state["cancelled"]


async def test_timeout_comes_from_spec(spy, sessions):
    async def slow(ctx, **kw):
        await asyncio.sleep(5)

    spy(fn=slow)
    out = await run(ToolSpec(timeout_ms=30))
    assert out.details["error"] == "timeout"


async def test_provider_key_pattern_redacted(spy, sessions):
    leaked = "sk-" + "Q" * 30

    async def handler(ctx, **kw):
        return {"note": f"found {leaked}", "nested": [f"again {leaked}"]}

    spy(fn=handler)
    out = await run(ToolSpec())
    assert leaked not in out.content and "REDACTED" in out.content
    assert out.details["redactions"] >= 1


async def test_held_key_value_redacted_even_without_pattern(spy, sessions):
    async def handler(ctx, **kw):
        return f"echo {ctx.api_keys['exa']}"

    spy(fn=handler)
    out = await run(ToolSpec(needs_keys=("exa",)))
    assert STORE["exa"] not in out.content and "REDACTED" in out.content


async def test_oversized_result_truncated_with_marker(spy, sessions):
    async def handler(ctx, **kw):
        return "a" * (MAX_OUTCOME_BYTES + 5000)

    spy(fn=handler)
    out = await run(ToolSpec())
    assert out.content.endswith(execution.TRUNCATION_MARKER)
    assert len(out.content.encode()) <= MAX_OUTCOME_BYTES + len(execution.TRUNCATION_MARKER)
    assert out.details["truncated"] is True and out.details["originalBytes"] > MAX_OUTCOME_BYTES


class FakeRedis:
    def __init__(self):
        self.counts = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        pass


async def test_rate_limit_429_with_retry_after_per_tenant_and_tool(spy, sessions):
    spy()
    limiter = ToolRateLimiter(FakeRedis())

    async def call(tenant=TENANT):
        kw = dict(COMMON, tenant_id=tenant)
        return await execute_tool("spy_tool", {}, spec=ToolSpec(), rate_limit="2/min", limiter=limiter, **kw)

    assert not (await call()).is_error
    assert not (await call()).is_error
    with pytest.raises(ToolRateLimited) as ei:
        await call()
    assert ei.value.status_code == 429
    assert int(ei.value.headers["Retry-After"]) >= 1
    assert not (await call(OTHER_TENANT)).is_error  # other tenant unaffected


async def test_no_redis_means_no_limit(spy, sessions, caplog):
    spy()
    limiter = ToolRateLimiter(None)
    for _ in range(5):
        out = await execute_tool("spy_tool", {}, spec=ToolSpec(), rate_limit="1/min", limiter=limiter, **COMMON)
        assert not out.is_error
    assert "rate limiting disabled" in caplog.text


def test_parse_rate_limit():
    assert parse_rate_limit("30/min") == (30, 60)
    assert parse_rate_limit(10) == (10, 60)
    assert parse_rate_limit("5/hour") == (5, 3600)
    assert parse_rate_limit(None) is None
    assert parse_rate_limit("fast") is None
    assert parse_rate_limit("0/min") is None


async def test_unknown_tool_is_error_outcome(sessions):
    out = await run(ToolSpec(), name="spy_missing")
    assert out.is_error and out.details["error"] == "ValueError"
