"""U11: workflow correctness (R12, AE6).

Covers both engines (WorkflowOrchestrator and DurableWorkflowEngine):
failure status, branch skipping, token totals, checkpoint isolation, recovery,
the agent node's tool loop, and the wait node.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.workflow import WorkflowExecution
from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.node_executors import NodeExecutionContext, NodeExecutionResult
from app.node_executors.agent_executor import AgentExecutor
from app.node_executors.wait_executor import MAX_IN_PROCESS_WAIT_SECONDS, WaitExecutor
from app.services.durable_workflow_engine import DurableWorkflowEngine
from app.services.workflow_orchestrator import WorkflowOrchestrator

TENANT_ID = uuid.uuid4()
USER_ID = str(uuid.uuid4())


def _node(node_id, node_type, **data):
    return {"id": node_id, "type": node_type, "data": data, "position": {"x": 0, "y": 0}}


def _edge(source, target, handle=None):
    edge = {"id": f"{source}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    return edge


def _ok(node_id, output=None, **kw):
    return NodeExecutionResult(node_id=node_id, status="success", output=output or {"v": node_id}, **kw)


class Runner:
    """Runs a spec through either engine and exposes a uniform result."""

    def __init__(self, kind, db):
        self.kind = kind
        self.db = db
        if kind == "orchestrator":
            self.engine = WorkflowOrchestrator(db, USER_ID)
            self.base = self.engine
        else:
            self.engine = DurableWorkflowEngine(db, USER_ID, TENANT_ID)
            self.engine._ensure_rls_context = AsyncMock()
            self.base = self.engine._base_orchestrator

    def fake_nodes(self, fn):
        """Replace node execution with ``fn(node_id, node_type, inputs, variables)``."""
        calls = []

        async def _execute_node(node_id, node_type, node_data, inputs, variables, workflow_spec=None):
            calls.append(node_id)
            out = fn(node_id, node_type, inputs, variables)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        self.base._execute_node = _execute_node
        return calls

    async def run(self, spec, workflow_id=None, inputs=None):
        if self.kind == "orchestrator":
            return await self.engine.execute_workflow(spec, inputs)
        return await self.engine.execute_workflow(spec, inputs, workflow_id=workflow_id)


@pytest.fixture(autouse=True)
def durable_on():
    with patch("app.services.durable_workflow_engine.settings") as s:
        s.USE_DURABLE_WORKFLOWS = True
        yield s


@pytest.fixture(params=["orchestrator", "durable"])
def runner(request, test_db):
    return Runner(request.param, test_db)


# --------------------------------------------------------------------------
# Status / kwarg normalisation
# --------------------------------------------------------------------------

def test_legacy_error_status_is_normalised_to_failed():
    assert NodeExecutionResult(node_id="n", status="error").status == "failed"
    assert NodeExecutionResult(node_id="n", status="success").status == "success"


@pytest.mark.asyncio
async def test_executor_failure_uses_failed_status_and_elapsed_ms():
    context = NodeExecutionContext(
        node_id="a1", node_type="agent", node_data={}, inputs={}, variables={}, user_id=USER_ID
    )
    result = await AgentExecutor().execute(context, MagicMock())
    assert result.status == "failed"
    assert result.error == "Agent ID not specified"
    assert isinstance(result.elapsed_ms, int)


# --------------------------------------------------------------------------
# AE6: a failing agent node fails the run under both engines
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ae6_failing_agent_node_fails_the_run(runner):
    spec = {
        "nodes": [_node("s", "start"), _node("a", "agent"), _node("o", "output")],
        "edges": [_edge("s", "a"), _edge("a", "o")],
    }
    # Real executors: the agent node has no agentId, so it fails.
    result = await runner.run(spec, workflow_id=uuid.uuid4())

    assert result["status"] == "failed"
    assert result["error"] == "Agent ID not specified"
    assert result["results"]["a"]["status"] == "failed"
    assert "o" not in result["results"]  # downstream node never ran


@pytest.mark.asyncio
async def test_failed_run_is_recorded_on_the_execution_log(test_db):
    runner = Runner("durable", test_db)
    spec = {"nodes": [_node("s", "start"), _node("a", "agent")], "edges": [_edge("s", "a")]}
    result = await runner.run(spec, workflow_id=uuid.uuid4())

    log = (await test_db.execute(select(WorkflowExecution))).scalar_one()
    assert log.status == "failed"
    assert log.error == "Agent ID not specified"
    assert str(log.dbos_workflow_uuid) == result["dbos_workflow_uuid"]


@pytest.mark.asyncio
async def test_failing_node_ends_stream_as_failed_in_both_engines(test_db):
    spec = {
        "nodes": [_node("s", "start"), _node("a", "agent"), _node("o", "output")],
        "edges": [_edge("s", "a"), _edge("a", "o")],
    }
    for engine in (WorkflowOrchestrator(test_db, USER_ID), DurableWorkflowEngine(test_db, USER_ID, TENANT_ID)):
        engine._ensure_rls_context = AsyncMock()
        events = [e async for e in engine.execute_workflow_stream(spec)]
        final = events[-1]
        assert final["type"] == "complete"
        assert final["status"] == "failed"
        assert "o" not in final["results"]


# --------------------------------------------------------------------------
# Branching
# --------------------------------------------------------------------------

def _branch_spec():
    return {
        "nodes": [
            _node("s", "start"), _node("c", "ifelse"), _node("A", "llm"),
            _node("B", "llm"), _node("J", "merge"),
        ],
        "edges": [
            _edge("s", "c"), _edge("c", "A", "true"), _edge("c", "B", "false"),
            _edge("A", "J"), _edge("B", "J"),
        ],
    }


@pytest.mark.parametrize("branch,taken,untaken", [("true", "A", "B"), ("false", "B", "A")])
@pytest.mark.asyncio
async def test_untaken_branch_is_skipped_and_join_runs_once(runner, branch, taken, untaken):
    def fn(node_id, node_type, inputs, variables):
        if node_id == "c":
            return _ok("c", next_node_id=branch)
        return _ok(node_id)

    calls = runner.fake_nodes(fn)
    result = await runner.run(_branch_spec(), workflow_id=uuid.uuid4())

    assert result["status"] == "completed"
    assert calls == ["s", "c", taken, "J"]
    assert calls.count("J") == 1
    assert result["results"][untaken]["status"] == "skipped"
    assert result["executed_nodes"] == 4  # skipped node does not count as executed


@pytest.mark.asyncio
async def test_node_downstream_of_skipped_branch_is_skipped_too(runner):
    spec = {
        "nodes": [_node("s", "start"), _node("c", "ifelse"), _node("A", "llm"),
                  _node("B", "llm"), _node("B2", "llm")],
        "edges": [_edge("s", "c"), _edge("c", "A", "true"), _edge("c", "B", "false"), _edge("B", "B2")],
    }
    calls = runner.fake_nodes(
        lambda n, t, i, v: _ok(n, next_node_id="true") if n == "c" else _ok(n)
    )
    result = await runner.run(spec, workflow_id=uuid.uuid4())
    assert calls == ["s", "c", "A"]
    assert result["results"]["B2"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_real_condition_executor_picks_the_branch(runner):
    spec = {
        "nodes": [
            _node("s", "start"),
            _node("c", "ifelse", conditions=[{"variable": "x", "operator": "equals", "value": "1"}]),
            _node("A", "llm"), _node("B", "llm"),
        ],
        "edges": [_edge("s", "c"), _edge("c", "A", "true"), _edge("c", "B", "false")],
    }
    executed = []
    real = runner.base._execute_node

    async def spy(node_id, node_type, node_data, inputs, variables, workflow_spec=None):
        executed.append(node_id)
        if node_type == "llm":
            return _ok(node_id)
        return await real(node_id, node_type, node_data, inputs, variables, workflow_spec)

    runner.base._execute_node = spy
    result = await runner.run(spec, workflow_id=uuid.uuid4(), inputs={"x": "1"})
    assert executed == ["s", "c", "A"]
    assert result["results"]["B"]["status"] == "skipped"


# --------------------------------------------------------------------------
# Token totals
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_totals_are_summed(runner):
    spec = {
        "nodes": [_node("s", "start"), _node("l1", "llm"), _node("l2", "llm")],
        "edges": [_edge("s", "l1"), _edge("l1", "l2")],
    }
    runner.fake_nodes(
        lambda n, t, i, v: _ok(n, tokens_used={"l1": 120, "l2": 30}.get(n))
    )
    result = await runner.run(spec, workflow_id=uuid.uuid4())
    assert result["total_tokens"] == 150


@pytest.mark.asyncio
async def test_token_usage_is_persisted_on_the_orchestrator_log(test_db):
    engine = WorkflowOrchestrator(test_db, USER_ID, tenant_id=TENANT_ID)
    spec = {"nodes": [_node("s", "start"), _node("l1", "llm")], "edges": [_edge("s", "l1")]}

    async def fake(node_id, node_type, node_data, inputs, variables, workflow_spec=None):
        return _ok(node_id, tokens_used=42 if node_id == "l1" else None)

    engine._execute_node = fake
    with patch.object(test_db, "execute", wraps=test_db.execute) as ex:
        # set_config does not exist on SQLite
        real_execute = ex.side_effect

        async def execute(stmt, *a, **kw):
            if "set_config" in str(stmt):
                return None
            return await real_execute(stmt, *a, **kw)

        ex.side_effect = execute
        result = await engine.execute_workflow(spec, workflow_id=uuid.uuid4())

    assert result["total_tokens"] == 42
    log = (await test_db.execute(select(WorkflowExecution))).scalar_one()
    assert log.total_tokens == 42
    assert log.execution_trace[1]["token_usage"] == {"total": 42}


# --------------------------------------------------------------------------
# Durable engine: checkpoints and recovery
# --------------------------------------------------------------------------

def _linear_spec(n=3):
    ids = [f"n{i}" for i in range(n)]
    return {
        "nodes": [_node(i, "llm") for i in ids],
        "edges": [_edge(a, b) for a, b in zip(ids, ids[1:])],
    }


async def _checkpoints(db):
    rows = (await db.execute(select(WorkflowCheckpoint))).scalars().all()
    by_run = {}
    for row in rows:
        by_run.setdefault(row.workflow_uuid, []).append(row)
    return by_run


@pytest.mark.asyncio
async def test_concurrent_runs_of_one_workflow_keep_their_own_checkpoints(test_db):
    workflow_id = uuid.uuid4()
    gate = asyncio.Event()
    entered = asyncio.Event()

    run_a = Runner("durable", test_db)
    run_b = Runner("durable", test_db)

    async def slow(node_id, node_type, inputs, variables):
        if node_id == "n1":
            entered.set()
            await gate.wait()  # run A parks mid-workflow
        return _ok(node_id)

    run_a.fake_nodes(slow)
    run_b.fake_nodes(lambda n, t, i, v: _ok(n))

    task_a = asyncio.create_task(run_a.run(_linear_spec(), workflow_id=workflow_id))
    await entered.wait()
    result_b = await run_b.run(_linear_spec(), workflow_id=workflow_id)  # starts + finishes meanwhile

    in_flight = await _checkpoints(test_db)
    assert len(in_flight) == 2  # B's start did not wipe A's rows
    gate.set()
    result_a = await task_a

    assert result_a["status"] == result_b["status"] == "completed"
    assert result_a["dbos_workflow_uuid"] != result_b["dbos_workflow_uuid"]
    runs = await _checkpoints(test_db)
    assert {str(k) for k in runs} == {result_a["dbos_workflow_uuid"], result_b["dbos_workflow_uuid"]}
    for rows in runs.values():
        assert sorted(r.step_number for r in rows) == [0, 1, 2]
        assert all(r.status == "completed" for r in rows)


@pytest.mark.asyncio
async def test_recovery_resumes_from_last_completed_node_and_restores_state(test_db):
    workflow_id = uuid.uuid4()
    spec = _linear_spec(4)
    engine_1 = Runner("durable", test_db)
    seen = []

    def first_attempt(node_id, node_type, inputs, variables):
        seen.append(node_id)
        if node_id == "n0":
            variables["counter"] = 7  # state that must survive recovery
        if node_id == "n2":
            return NodeExecutionResult(node_id=node_id, status="failed", error="boom")
        return _ok(node_id, {"out": node_id}, tokens_used=10)

    engine_1.fake_nodes(first_attempt)
    first = await engine_1.run(spec, workflow_id=workflow_id)
    assert first["status"] == "failed"
    assert seen == ["n0", "n1", "n2"]

    run_uuid = uuid.UUID(first["dbos_workflow_uuid"])
    states = {r.node_id: r.status for r in (await _checkpoints(test_db))[run_uuid]}
    assert states == {"n0": "completed", "n1": "completed", "n2": "failed"}

    # Recover with a fresh engine (new process): only n2 and n3 execute.
    engine_2 = Runner("durable", test_db)
    resumed = []
    received = {}

    def second_attempt(node_id, node_type, inputs, variables):
        resumed.append(node_id)
        received[node_id] = (dict(inputs), dict(variables))
        return _ok(node_id, {"out": node_id}, tokens_used=10)

    engine_2.fake_nodes(second_attempt)
    recovered = await engine_2.engine.recover_workflow(run_uuid, spec, {})

    assert resumed == ["n2", "n3"]
    assert recovered["status"] == "completed"
    assert received["n2"][0] == {"n1": {"out": "n1"}}  # predecessor output restored
    assert received["n2"][1]["counter"] == 7  # variables restored
    assert recovered["total_tokens"] == 40  # restored tokens + re-run nodes
    assert set(recovered["results"]) == {"n0", "n1", "n2", "n3"}

    rows = (await _checkpoints(test_db))[run_uuid]
    assert sorted(r.step_number for r in rows) == [0, 1, 2, 3]
    assert all(r.status == "completed" for r in rows)

    logs = (await test_db.execute(select(WorkflowExecution))).scalars().all()
    assert len(logs) == 1 and logs[0].status == "completed"  # same record reused


@pytest.mark.asyncio
async def test_recovery_restores_branch_decisions(test_db):
    workflow_id = uuid.uuid4()
    spec = _branch_spec()
    engine_1 = Runner("durable", test_db)

    def first(node_id, node_type, inputs, variables):
        if node_id == "c":
            return _ok("c", next_node_id="true")
        if node_id == "A":
            return NodeExecutionResult(node_id="A", status="failed", error="boom")
        return _ok(node_id)

    engine_1.fake_nodes(first)
    failed = await engine_1.run(spec, workflow_id=workflow_id)
    assert failed["status"] == "failed"

    engine_2 = Runner("durable", test_db)
    calls = engine_2.fake_nodes(lambda n, t, i, v: _ok(n))
    recovered = await engine_2.engine.recover_workflow(uuid.UUID(failed["dbos_workflow_uuid"]), spec, {})

    assert calls == ["A", "J"]  # B stays skipped after recovery; join runs once
    assert recovered["results"]["B"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_infrastructure_exception_fails_checkpoint_and_log(test_db):
    runner = Runner("durable", test_db)

    async def explode(node_id, node_type, node_data, inputs, variables, workflow_spec=None):
        raise RuntimeError("db went away")

    runner.base._execute_node = explode
    with pytest.raises(RuntimeError):
        await runner.run(_linear_spec(2), workflow_id=uuid.uuid4())

    (rows,) = (await _checkpoints(test_db)).values()
    assert [r.status for r in rows] == ["failed"]
    log = (await test_db.execute(select(WorkflowExecution))).scalar_one()
    assert log.status == "failed" and log.error == "db went away"


# --------------------------------------------------------------------------
# Agent node: the tool loop exits when an iteration makes no new tool calls
# --------------------------------------------------------------------------

def _call_part(call_id):
    return SimpleNamespace(part_kind="tool-call", tool_name="lookup", args={"q": "x"}, tool_call_id=call_id)


def _return_part(call_id):
    return SimpleNamespace(part_kind="tool-return", tool_name="lookup", content="found", tool_call_id=call_id)


def _text_part():
    return SimpleNamespace(part_kind="text", content="done")


class FakeRun:
    def __init__(self, output, all_messages, total_tokens):
        self.output = output
        self._all_messages = all_messages
        self._tokens = total_tokens

    def usage(self):
        return SimpleNamespace(total_tokens=self._tokens)


class FakePydanticAgent:
    """Iteration 1 calls a tool; iteration 2 answers in plain text."""

    runs = 0

    def __init__(self, **kwargs):
        pass

    def tool(self, fn):
        return fn

    async def run(self, prompt, deps=None, message_history=None):
        type(self).runs += 1
        history = list(message_history or [])
        if type(self).runs == 1:
            new = [SimpleNamespace(parts=[_call_part("c1")]), SimpleNamespace(parts=[_return_part("c1")])]
            return FakeRun("calling", history + new, 11)
        new = [SimpleNamespace(parts=[_text_part()])]
        return FakeRun("final answer", history + new, 5)


@pytest.mark.asyncio
async def test_agent_node_exits_loop_when_no_new_tool_calls():
    FakePydanticAgent.runs = 0
    custom_agent = SimpleNamespace(
        id=uuid.uuid4(), name="Researcher", tenant_id=None, user_id=uuid.uuid4(), is_active=True,
        model="openai/gpt-4o-mini", system_prompt="be helpful", skills=["lookup"],
        connector_ids=None, usage_count=0, last_used_at=None,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=custom_agent)))
    db.commit = AsyncMock()
    skill = SimpleNamespace(enabled=True, handler=lambda: None)

    module = "app.node_executors.agent_executor"
    with patch(f"{module}.get_redis_service", AsyncMock()), \
         patch(f"{module}.AgentCircuitBreaker", return_value=SimpleNamespace(check_health=AsyncMock())), \
         patch(f"{module}.resolve_key_with_fallback", AsyncMock(return_value=("key", "openai/gpt-4o-mini"))), \
         patch(f"{module}.create_litellm_model", return_value=object()), \
         patch(f"{module}.PydanticAgent", FakePydanticAgent), \
         patch(f"{module}.patch_dangling_tool_calls", side_effect=lambda m: m), \
         patch(f"{module}.SkillRegistry.get_skill", return_value=skill), \
         patch.object(AgentExecutor, "_load_tool_api_keys", AsyncMock(return_value={})):
        context = NodeExecutionContext(
            node_id="a1", node_type="agent", user_id=USER_ID, inputs={}, variables={},
            node_data={"agentId": str(custom_agent.id), "userPrompt": "find x", "maxIterations": 6},
        )
        result = await AgentExecutor().execute(context, db)

    assert result.status == "success", result.error
    assert FakePydanticAgent.runs == 2  # ran twice, not up to maxIterations
    assert result.output["stopped_reason"] == "complete"
    assert result.output["response"] == "final answer"
    assert result.output["total_tool_calls"] == 1  # only iteration 1's call counted
    assert [len(i["tool_calls"]) for i in result.output["iterations"]] == [1, 0]
    assert result.output["iterations"][0]["tool_calls"][0]["result"] == "found"
    assert result.tokens_used == 16


# --------------------------------------------------------------------------
# Wait node: honour the real duration or reject, never silently truncate
# --------------------------------------------------------------------------

async def _wait(**node_data):
    context = NodeExecutionContext(
        node_id="w", node_type="wait", node_data=node_data, inputs={}, variables={}, user_id=USER_ID
    )
    return await WaitExecutor().execute(context, MagicMock())


@pytest.mark.asyncio
async def test_wait_honours_real_duration_above_the_old_60s_cap():
    with patch("app.node_executors.wait_executor.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await _wait(waitType="duration", duration=2, durationUnit="minutes")
    assert result.status == "success"
    sleep.assert_awaited_once_with(120)
    assert result.output["wait_seconds"] == 120


@pytest.mark.asyncio
async def test_wait_rejects_durations_it_cannot_honour():
    with patch("app.node_executors.wait_executor.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await _wait(waitType="duration", duration=2, durationUnit="hours")
    assert 7200 > MAX_IN_PROCESS_WAIT_SECONDS
    assert result.status == "failed"
    assert "not supported" in result.error
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,unit", [(-5, "seconds"), ("ten", "seconds"), (5, "fortnights")])
async def test_wait_rejects_invalid_durations(duration, unit):
    result = await _wait(waitType="duration", duration=duration, durationUnit=unit)
    assert result.status == "failed"
    assert "Invalid wait duration" in result.error
