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
