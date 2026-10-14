"""Stream drivers for the golden event-contract fixtures (U12).

Each driver runs one real production code path with the LLM replaced by a fake
and returns plain JSON-able data (pre-normalization). No app code is modified;
all fakes are installed with unittest.mock.patch.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from .recorder import parse_sse_text, sse_events

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


# ─────────────────────────────────────────────────────────────────────────────
# Shared fakes
# ─────────────────────────────────────────────────────────────────────────────

def fake_session_factory():
    """Stand-in for AsyncSessionLocal: `async with factory() as db`."""

    @asynccontextmanager
    async def _cm():
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        yield db

    return lambda *a, **k: _cm()


# ─────────────────────────────────────────────────────────────────────────────
# Chat SSE  (api/v1/chat.py :: generate_enhanced_sse_stream)
# ─────────────────────────────────────────────────────────────────────────────

class _PydanticFakeAgent:
    """Minimal stand-in for BaseAgent: only stream_events(), backed by a real
    pydantic-ai Agent running a FunctionModel (so the real PartStart/PartDelta/
    FunctionToolCall/FunctionToolResult event types reach chat.py)."""

    model = "contract-test-model"
    _last_rag_results = None

    def __init__(self, stream_function, tools=()):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel

        self._agent = Agent(
            FunctionModel(stream_function=stream_function),
            tools=list(tools),
            retries=0,
        )

    async def stream_events(self, message, **kwargs):
        async for ev in self._agent.run_stream_events(message):
            yield ev


def _last_is_tool_return(messages) -> bool:
    from pydantic_ai.messages import ToolReturnPart

    last = messages[-1]
    return any(isinstance(p, ToolReturnPart) for p in getattr(last, "parts", []))


def _text_only_fn():
    async def fn(messages, info):
        yield "Hello"
        yield ", "
        yield "world."

    return fn


def _tool_call_fn():
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall

    async def fn(messages, info):
        n_returns = sum(
            1 for m in messages for p in getattr(m, "parts", [])
            if p.__class__.__name__ == "ToolReturnPart"
        )
        if n_returns == 0:
            yield {0: DeltaThinkingPart(content="Need to search the web. ")}
            yield {0: DeltaThinkingPart(content="Then write a report.")}
            yield {1: DeltaToolCall(
                name="web_search", json_args='{"query": "oniva contract"}',
                tool_call_id="call_search_1",
            )}
        elif n_returns == 1:
            yield {0: DeltaToolCall(
                name="save_report",
                json_args='{"filename": "report.md", "report_content": "# Report"}',
                tool_call_id="call_report_1",
            )}
        else:
            yield "Done: "
            yield "see the report."

    return fn


def _error_fn():
    async def fn(messages, info):
        yield "partial "
        raise RuntimeError("rate limit exceeded by upstream provider")

    return fn


def web_search(query: str) -> dict:
    """Search the web."""
    return {"results": [{"title": "Oniva", "url": "https://example.com/oniva", "snippet": "a snippet"}]}


def save_report(filename: str, report_content: str) -> dict:
    """Save a report."""
    return {"success": True, "filename": filename, "size": len(report_content),
            "download_url": "/files/report.md"}


async def run_chat_scenario(scenario: str) -> dict:
    from app.api.v1 import chat as chat_mod

    fn, tools = {
        "text": (_text_only_fn(), ()),
        "tool_call": (_tool_call_fn(), (web_search, save_report)),
        "error": (_error_fn(), ()),
    }[scenario]
    agent = _PydanticFakeAgent(fn, tools)
    persisted: list[dict] = []

    async def fake_persist_session(tenant_id, user_id, session_id, message, response, **kw):
        persisted.append({
            "message": message, "response": response,
            "reasoning": kw.get("reasoning"),
            "message_parts": kw.get("message_parts"),
            "model": kw.get("model"),
        })
        return SESSION_ID

    with ExitStack() as st:
        st.enter_context(patch.object(chat_mod, "AsyncSessionLocal", fake_session_factory()))
        st.enter_context(patch.object(chat_mod, "set_tenant_context", AsyncMock()))
        st.enter_context(patch.object(chat_mod, "emit_run_event", AsyncMock()))
        st.enter_context(patch.object(chat_mod, "store_memory_async", MagicMock()))
        st.enter_context(patch.object(chat_mod, "persist_session", fake_persist_session))
        events = []
        async for ev in chat_mod.generate_enhanced_sse_stream(
            request=MagicMock(), message_content="What is the contract?", agent=agent,
            user_id=USER_ID, tenant_id=TENANT_ID, session_id=SESSION_ID,
            agent_id="general",
        ):
            events.append(ev)
    return {"events": sse_events(events), "persisted_messages": persisted}


# ─────────────────────────────────────────────────────────────────────────────
# Deep Work engine v2  (services/deep_work/engine.py)
# ─────────────────────────────────────────────────────────────────────────────

_DW_SESSION = "dwsession0001"


class _FakeDeepWorkGateway:
    """Fake LLMGateway. Dispatches on request.model (tier) and system prompt."""

    reflections = 0
    fail_orchestrator = False

    def __init__(self, db=None):
        self._worker_iter: dict[str, int] = {}

    @staticmethod
    def _resp(content, tool_calls=None):
        from app.services.llm_gateway import LLMResponse, ToolCallResponse

        return LLMResponse(
            content=content, model="fake", provider_name="fake",
            input_tokens=10, output_tokens=5, cost_usd=0.0, latency_ms=1,
            tool_calls=[ToolCallResponse(**tc) for tc in tool_calls] if tool_calls else None,
        )

    async def complete(self, request, tenant_id=None, user_id=None):
        from app.services.deep_work.engine import _load_prompt

        system = request.messages[0]["content"]
        model = request.model
        if model == "fake-worker":
            first_user = request.messages[1]["content"]
            n = self._worker_iter.get(first_user, 0)
            self._worker_iter[first_user] = n + 1
            slug = first_user.split(":", 1)[1].strip().replace(" ", "-")[:20]
            if n == 0:
                return self._resp("", [{
                    "id": f"call_{slug}", "name": "web_search",
                    "arguments": {"query": first_user.split(":", 1)[1].strip()},
                }])
            return self._resp("Research complete.")
        if model == "fake-low":  # extractor
            sub_query = request.messages[1]["content"].split("Sub-query: ", 1)[1].split("\n", 1)[0]
            slug = sub_query.replace(" ", "-")[:20]
            return self._resp(json.dumps([{
                "fact": f"Finding about {sub_query}. More detail follows.",
                "source_url": f"https://example.com/{slug}",
                "source_type": "web", "source_title": f"Source for {sub_query}",
                "confidence": 0.9, "quote": "a quote",
            }]))
        if model == "fake-mid":  # reflection
            type(self).reflections += 1
            if type(self).reflections == 1:
                return self._resp(json.dumps({
                    "score": 55, "gaps": ["pricing"], "should_continue": True,
                    "gap_filling_tasks": [{
                        "worker_id": "gap-1", "sub_query": "pricing details",
                        "tools": ["web_search"],
                    }],
                }))
            return self._resp(json.dumps({"score": 90, "gaps": [], "should_continue": False}))
        if system == _load_prompt("orchestrator.md"):
            if self.fail_orchestrator:
                return self._resp("this is not json")
            return self._resp(json.dumps({
                "title": "Contract Research",
                "sub_queries": [
                    # One worker per round: parallel worker interleaving is
                    # thread-timing dependent (aiofiles) and so not recorded.
                    {"worker_id": "w1", "sub_query": "market size", "tools": ["web_search"]},
                ],
            }))
        if system == _load_prompt("outliner.md"):
            return self._resp(json.dumps({
                "title": "Contract Report",
                "sections": [
                    {"heading": "Market", "description": "size", "learning_ids": [1]},
                    {"heading": "Players", "description": "who", "learning_ids": [2, 3]},
                ],
            }))
        raise AssertionError(f"unexpected complete() call: model={model!r} system={system[:60]!r}")

    async def stream(self, request, tenant_id=None, user_id=None):
        from app.services.llm_gateway import LLMStreamChunk

        system = request.messages[0]["content"]
        if system.startswith("Given a research report body"):
            parts = ["## Executive Summary\n\nA summary. ", "\n\n## Key Takeaways\n\n1. A takeaway [1]."]
        else:
            parts = ["Section body ", "text [1]."]
        for p in parts:
            yield LLMStreamChunk(content=p)


async def _fake_web_search(self, tool_name, arguments):
    q = arguments.get("query", "")
    return {"results": [{"title": q, "url": f"https://example.com/{q.replace(' ', '-')[:20]}"}]}


async def run_deep_work_scenario(scenario: str = "success") -> dict:
    import asyncio

    from app.config import get_settings
    from app.services.deep_work import engine as eng_mod
    from app.services.deep_work.event_sink import SSEQueueSink
    from app.services.deep_work.worker import DeepWorkWorker
    from app.services.session_storage import SessionStorage

    real = get_settings()
    settings = real.model_copy(update={
        "ENABLE_SANDBOX": False, "DEEP_WORK_MAX_REFLECTION_ROUNDS": 2,
        "DEEP_WORK_MAX_DURATION_SEC": 60,
    })
    _FakeDeepWorkGateway.reflections = 0
    _FakeDeepWorkGateway.fail_orchestrator = scenario == "failure"

    async def fake_models_and_keys(self):
        return ({"high": "fake-high", "mid": "fake-mid", "low": "fake-low"},
                {"tavily": "k"})

    async def fake_policy(db, tenant_uuid, task_type, output_type, agent_config):
        return "fake-worker"

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    error: str | None = None
    report = None
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as st:
        storage = SessionStorage(base_path=Path(tmp))
        st.enter_context(patch.object(eng_mod, "get_settings", lambda: settings))
        st.enter_context(patch.object(eng_mod, "LLMGateway", _FakeDeepWorkGateway))
        st.enter_context(patch.object(eng_mod, "emit_run_event", AsyncMock()))
        st.enter_context(patch.object(eng_mod, "resolve_model_for_policy", fake_policy))
        st.enter_context(patch.object(eng_mod.DeepWorkEngine, "_resolve_models_and_keys", fake_models_and_keys))
        st.enter_context(patch.object(DeepWorkWorker, "_execute_tool", _fake_web_search))
        engine = eng_mod.DeepWorkEngine(
            MagicMock(), storage, str(TENANT_ID), str(USER_ID), _DW_SESSION,
            event_sink=SSEQueueSink(queue),
        )
        try:
            report = await engine.run("compare contract vendors")
        except Exception as exc:  # engine re-raises; API layer turns it into deep_work_engine_error
            error = f"{type(exc).__name__}"

        # What GET /deep-work/engine/{id}/stream puts on the wire (deep_work.py:1218-1222)
        wire = []
        while not queue.empty():
            ev = queue.get_nowait()
            wire.append({"event": ev["type"], "data": ev["data"]})

        out: dict[str, Any] = {"events": wire, "engine_raised": error}
        if scenario == "success":
            out["report"] = report
            out["full_state"] = await _deep_work_full_state(storage)
            state_path = storage._get_session_path(str(TENANT_ID), _DW_SESSION) / "engine_state.json"
            out["persisted_engine_state"] = json.loads(state_path.read_text())
            out["files"] = sorted(
                str(p.relative_to(Path(tmp))) for p in Path(tmp).rglob("*") if p.is_file()
            )
    return out


async def _deep_work_full_state(storage) -> dict:
    """GET /deep-work/by-conversation/{cid}/full-state against the engine's persisted files."""
    from app.api.v1 import deep_work as dw_mod
    from app.models.background_job import BackgroundJob

    conv = uuid.UUID("44444444-4444-4444-4444-444444444444")
    job = BackgroundJob(
        id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
        tenant_id=TENANT_ID, user_id=USER_ID, job_type="deep_work", status="completed",
        trigger="manual", session_id=_DW_SESSION, conversation_id=conv,
        input_data={"query": "compare contract vendors", "engine": "orchestrator-worker"},
    )
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db.execute = AsyncMock(return_value=res)
    with patch("app.services.session_storage.get_session_storage", lambda: storage):
        state = await dw_mod._build_full_state(db, conv, USER_ID, TENANT_ID)
    return state.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# Fleet / background agents
#   services/background_agent_executor.py  ->  job.execution_trace
#   api/v1/background_jobs.py :: stream_job_progress  ->  progress/step/completed|failed|cancelled
# ─────────────────────────────────────────────────────────────────────────────

_JOB_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
_AGENT_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")


class _FleetGateway:
    def __init__(self, db=None):
        self.calls = 0

    async def complete(self, request, tenant_id=None, user_id=None):
        from app.services.llm_gateway import LLMResponse, ToolCallResponse

        self.calls += 1
        if _FleetGateway.fail:
            raise RuntimeError("provider exploded")
        base = dict(model="fake", provider_name="fake", input_tokens=100,
                    output_tokens=20, cost_usd=0.0, latency_ms=1)
        if self.calls == 1:
            return LLMResponse(content="", tool_calls=[ToolCallResponse(
                id="call_fleet_1", name="web_search", arguments={"query": "fleet contract"},
            )], **base)
        return LLMResponse(content="All done: found one result.", **base)

    fail = False


async def run_fleet_scenario(scenario: str) -> dict:
    from app.api.v1 import background_jobs as bj_mod
    from app.models.background_job import BackgroundJob
    from app.services import background_agent_executor as ex

    job = BackgroundJob(
        id=_JOB_ID, tenant_id=TENANT_ID, user_id=USER_ID, job_type="background_agent",
        status="pending", trigger="manual", progress=0, execution_trace=[],
        input_data={"task": "Look something up"},
    )
    agent = SimpleNamespace(
        id=_AGENT_ID, name="Fleet Agent", system_prompt="You are a test agent.",
        model="gpt-4o", temperature=0.7, skills=["web_search"],
        resource_constraints={"max_tool_calls": 5, "timeout_seconds": 60},
        background_config={},
    )

    @asynccontextmanager
    async def nested():
        yield

    def make_db():
        db = AsyncMock()

        async def execute(stmt, *a, **k):
            r = MagicMock()
            r.scalar_one_or_none.return_value = agent if "custom_agents" in str(stmt) else job
            return r

        db.execute = execute
        db.begin_nested = lambda: nested()
        db.commit = AsyncMock()
        return db

    @asynccontextmanager
    async def session_cm():
        yield make_db()

    async def handler(ctx, query: str):
        return {"results": [{"title": "Fleet", "url": "https://example.com/fleet"}], "query": query}

    tools = {"web_search": SimpleNamespace(
        name="web_search", description="Search", handler=handler, parameters={},
    )}
    _FleetGateway.fail = scenario == "failed"

    with ExitStack() as st:
        for mod in (ex,):
            st.enter_context(patch.object(mod, "AsyncSessionLocal", lambda: session_cm()))
            st.enter_context(patch.object(mod, "set_tenant_context", AsyncMock()))
            st.enter_context(patch.object(mod, "emit_run_event", AsyncMock()))
            st.enter_context(patch.object(mod, "LLMGateway", _FleetGateway))
            st.enter_context(patch.object(mod, "_load_tools", lambda names: tools))
            st.enter_context(patch.object(mod, "_safe_check_interventions", AsyncMock(return_value=[])))
            st.enter_context(patch.object(mod, "_safe_deliver", AsyncMock()))
            st.enter_context(patch.object(mod, "clear_interventions", AsyncMock()))
        await ex.run_background_agent(
            job_id=_JOB_ID, agent_id=_AGENT_ID, tenant_id=TENANT_ID, user_id=USER_ID,
            input_data={"task": "Look something up"},
        )
        if scenario == "cancelled":
            # Real cancel endpoint only calls job.cancel() (background_jobs.py:268-311)
            job.cancel()

    # Now stream it the way GET /background-jobs/{id}/stream does.
    request_db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    request_db.execute = AsyncMock(return_value=res)
    with patch("app.core.database.AsyncSessionLocal", lambda: session_cm()), \
            patch("app.core.database.set_tenant_context", AsyncMock()):
        resp = await bj_mod.stream_job_progress(
            _JOB_ID, db=request_db, user=SimpleNamespace(id=str(USER_ID)), tenant_id=TENANT_ID,
        )
        text = ""
        async for chunk in resp.body_iterator:
            text += chunk if isinstance(chunk, str) else chunk.decode()
    return {
        "events": parse_sse_text(text),
        "final_job": {"status": job.status, "progress": job.progress,
                      "result": job.result, "error": job.error},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Workflow execution
#   services/workflow_orchestrator.py :: execute_workflow_stream (raw events)
#   api/v1/workflow_execution.py      :: workflow_progress / workflow_complete SSE
# NOTE: workflow-correctness work was in flight on the orchestrator and
# node_executors when this was recorded; re-record if that lands.
# ─────────────────────────────────────────────────────────────────────────────

_WF_ID = uuid.UUID("88888888-8888-8888-8888-888888888888")
_WF_SESSION = uuid.UUID("99999999-9999-9999-9999-999999999999")

_WF_SPECS: dict[str, dict] = {
    "success": {
        "nodes": [
            {"id": "start-1", "type": "start", "data": {"label": "Start"}},
            {"id": "tmpl-1", "type": "template",
             "data": {"label": "Greeting", "template": "Hello {{name}}"}},
            {"id": "out-1", "type": "output", "data": {"label": "Result", "outputName": "greeting"}},
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "tmpl-1"},
            {"id": "e2", "source": "tmpl-1", "target": "out-1"},
        ],
    },
    # Unknown node type -> orchestrator passes through as status "skipped"
    "unknown_node": {
        "nodes": [
            {"id": "start-1", "type": "start", "data": {"label": "Start"}},
            {"id": "mystery-1", "type": "does_not_exist", "data": {}},
        ],
        "edges": [{"id": "e1", "source": "start-1", "target": "mystery-1"}],
    },
}


async def run_workflow_scenario(scenario: str) -> dict:
    from app.api.v1 import workflow_execution as wf_mod
    from app.services.workflow_orchestrator import WorkflowOrchestrator

    from app.services import workflow_orchestrator as orch_mod

    spec = _WF_SPECS[scenario]
    inputs = {"name": "Oniva"}
    # Pin the flags that change the event payloads (env-dependent defaults).
    pins = ExitStack()
    for flag, value in (("USE_STATE_MACHINE", True), ("USE_DATA_CLASSIFICATION", True)):
        pins.enter_context(patch.object(orch_mod.settings, flag, value))

    def make_db():
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.add = MagicMock()
        return db

    # 1. Raw orchestrator events (what the SSE layer translates)
    raw = []
    orch = WorkflowOrchestrator(
        db=make_db(), user_id=str(USER_ID), tenant_id=TENANT_ID, trigger="agent_chat",
    )
    async for ev in orch.execute_workflow_stream(
        workflow_spec=spec, input_variables=inputs, workflow_id=_WF_ID, save_logs=False,
    ):
        raw.append(ev)

    # 2. The SSE endpoint the frontend consumes
    request_db = AsyncMock()
    session_res, wf_res = MagicMock(), MagicMock()
    session_res.scalar_one_or_none.return_value = SimpleNamespace(id=_WF_SESSION)
    wf_res.scalar_one_or_none.return_value = SimpleNamespace(
        spec=spec, name="Contract Workflow",
    )
    request_db.execute = AsyncMock(side_effect=[session_res, wf_res])

    @asynccontextmanager
    async def session_cm():
        yield make_db()

    with patch.object(wf_mod, "AsyncSessionLocal", lambda: session_cm()), \
            patch.object(wf_mod, "set_tenant_context", AsyncMock()):
        resp = await wf_mod.execute_workflow_stream(
            agent_id=str(_AGENT_ID), session_id=_WF_SESSION,
            request=wf_mod.WorkflowExecuteRequest(workflow_id=str(_WF_ID), inputs=inputs),
            db=request_db, user=SimpleNamespace(id=str(USER_ID)), tenant_id=TENANT_ID,
        )
        sse = []
        async for ev in resp.body_iterator:
            sse.append(ev)
    pins.close()
    return {"orchestrator_events": raw, "sse_events": sse_events(sse)}
