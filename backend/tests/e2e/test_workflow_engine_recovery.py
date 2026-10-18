"""U22 / AE3, AE5, AE6, AE9, R3-R7, R10: workflow runs on the engine survive restarts, each node's effect once.

Same stack as the Fleet and Deep Work e2e (harness.py): Postgres for the engine, a real engine worker with the
workflow task kinds registered against the backend node gateway, a scripted model for ``llm`` nodes, and in the
backend process the real workflow, schedule and webhook routes, the node gateway, run sync and run reconciler on a
file SQLite database. The outside world is the backend's counting HTTP target (``/e2e/http/<post|hold|flaky>/<tag>``,
logged to ``http.log``): an HTTP node is the side effect, and ``hold`` keeps a request in flight while a process dies.

  * join: the worker is SIGKILLed while one parallel branch's POST is held; the run completes, each POST happened
    once, the ledger has one row for the llm node, and finalize wrote the read model once.
  * branch: a condition node runs only the taken branch; the other is ``skipped`` and its POST never happens.
  * iteration: a fan-out of four items with ``parallel = 2`` keeps its bound across a worker restart; every item ran once.
  * schedule retry (AE5): a scheduled run that fails once waits ``retry_delay_seconds`` (engine clock), reruns, completes.
  * wait (AE6): a 2 h wait survives a worker restart and ends when the test clock passes its deadline.
  * human in the loop: the decision comes through the inbox after worker and backend restarts.
  * backend restart (AE9): the backend is replaced while the run is parked; nothing runs twice. With a POST in
    flight the same restart is an expected failure (xfail, see its reason): the engine gives up on the backend too soon.
  * cancel: cancelling a fan-out run aborts its children; nothing starts afterwards.

Skipped unless ONIVA_E2E=1.
"""
import json
import signal
import time

import httpx
import pytest

from tests.e2e import workflow_script as script
from tests.e2e.harness import AUTH, GATEWAY_TOKEN, as_user, restart_backend, sql
from tests.e2e.test_chat_engine_recovery import snapshot_of
from tests.e2e.workflow_helpers import (
    advance_clock, api, close_gates, dones, edge, ensure_worker, exec_row, http_lines, http_node, in_flight, node,
    open_gate, run_workflow, save_workflow, starts, wait_for, wait_status)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_gates(world):
    close_gates(world)
    yield


def settle(seconds: float = 3.0) -> None:
    """Give a stray duplicate (a replayed node, a second finalize, a reconciler pass) time to show up."""
    time.sleep(seconds)


def trace_status(row: dict) -> dict:
    return {t["node_id"]: t["status"] for t in row["trace"]}


def ledger(w, conv: str) -> list[tuple]:
    return sql(w, "SELECT input_tokens, output_tokens FROM llm_usage_records WHERE conversation_id = ?",
               conv.replace("-", ""))


def item_rows(w, parent: str) -> list[dict]:
    rows = sql(w, "SELECT status, input_data FROM workflow_executions WHERE input_data LIKE ?", f'%{parent}%')
    return [{"status": s, "input": json.loads(i) if isinstance(i, str) else i} for s, i in rows
            if (json.loads(i) if isinstance(i, str) else i).get("__parent_run_id") == parent]


def finalize_again(w, execution_id: str, status: str = "failed") -> httpx.Response:
    """Post a second ``finalize`` for a settled run, as a replayed worker would (it must change nothing)."""
    row = sql(w, "SELECT e.tenant_id, c.project_id FROM workflow_executions e JOIN engine_conversations c "
                 "ON c.conversation_id = e.engine_conversation_id WHERE e.id = ?", execution_id.replace("-", ""))[0]
    tenant, project = (str(__import__("uuid").UUID(hex=v)) for v in row)
    return httpx.post(
        f"{w.backend_url}/internal/workflows/runs/{execution_id}/finalize",
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}", "Idempotency-Key": f"{execution_id}:r0:finalize"},
        json={"tenantId": tenant, "projectId": project, "contractVersion": 1, "status": status,
              "nodeSummaries": {"s": "failed"}, "attempts": 1, "error": "late duplicate"}, timeout=30)


# --- a worker kill mid-join ----------------------------------------------------------------------------------


def test_run_survives_worker_kill_mid_join_and_finalizes_once(world):
    w = world
    worker = ensure_worker(w, 1)
    wf = save_workflow(w, [
        node("s", "start"), node("l", "llm", userPrompt=script.LLM_MARKER, systemPrompt="Summarise."),
        http_node(w, "a", "hold", "join-a"), http_node(w, "b", "post", "join-b"),
        node("j", "merge"), node("o", "output")],
        [edge("s", "l"), edge("s", "a"), edge("s", "b"), edge("l", "j"), edge("a", "j"), edge("b", "j"), edge("j", "o")])
    ex = run_workflow(w, wf)
    wait_for(lambda: in_flight(w, "join-a") and dones(w, "join-b") == 1, "branch a held while b is done", 90)
    assert exec_row(w, ex)["status"] == "running"

    worker.kill(signal.SIGKILL)
    w.start_worker(2)
    open_gate(w)  # the held request was served by the backend all along; it returns, the restarted node reads it

    row = wait_status(w, ex)
    settle()
    assert starts(w, "join-a") == 1 and starts(w, "join-b") == 1, "an HTTP node's POST ran twice"
    assert dones(w, "join-a") == 1 and dones(w, "join-b") == 1
    assert trace_status(row) == {n: "success" for n in "slabjo"}, row["trace"]
    llm_step = next(t for t in row["trace"] if t["node_id"] == "l")
    assert llm_step["output"]["text"] == script.LLM_ANSWER, llm_step
    assert [tuple(r) for r in ledger(w, row["conversation_id"])] == [script.LLM_TOKENS], "one ledger row per model request"
    assert row["nodes_executed"] == 6 and row["error"] is None and row["completed_at"]
    assert row["total_tokens"] == sum(script.LLM_TOKENS), row

    # finalize wrote the read model once: a duplicate (even one claiming failure) moves nothing.
    before = exec_row(w, ex)
    again = finalize_again(w, ex)
    assert again.status_code == 200, again.text
    after = exec_row(w, ex)
    assert (after["status"], after["completed_at"], after["error"], after["results"], after["nodes_executed"]) == \
        (before["status"], before["completed_at"], before["error"], before["results"], before["nodes_executed"])


# --- branch, join, skip -------------------------------------------------------------------------------------


def test_branch_runs_only_the_taken_branch(world):
    w = world
    ensure_worker(w, 11)
    wf = save_workflow(w, [
        node("s", "start"), node("c", "ifelse", conditions=[{"variable": "yes", "operator": "equals", "value": "yes"}]),
        http_node(w, "a", "post", "br-a"), http_node(w, "b", "post", "br-b"), node("j", "merge"), node("o", "output")],
        [edge("s", "c"), edge("c", "a", "true"), edge("c", "b", "false"), edge("a", "j"), edge("b", "j"), edge("j", "o")])
    ex = run_workflow(w, wf)
    row = wait_status(w, ex)
    settle(2)
    assert starts(w, "br-a") == 1 and starts(w, "br-b") == 0, "the untaken branch ran"
    assert row["results"]["b"]["status"] == "skipped", row["results"]
    assert row["results"]["a"]["status"] == "success" and row["results"]["o"]["status"] == "success"
    assert "b" not in trace_status(row), "a skipped node must not run through the gateway"
    assert row["nodes_executed"] == 5, "skipped nodes are not counted as executed"


# --- iteration fan-out with restart -------------------------------------------------------------------------


def run_tasks(w, execution_id: str, kind: str) -> list[dict]:
    view = snapshot_of(w, str(__import__("uuid").UUID(hex=sql(
        w, "SELECT engine_conversation_id FROM workflow_executions WHERE id = ?", execution_id.replace("-", ""))[0][0])))
    return [t for t in view.get("tasks", {}).values() if t["kind"] == kind]


def waiting_nodes(w, execution_id: str) -> int:
    """Node tasks asleep on a wake time (the fan-out node itself waits for its lanes, which is another phase)."""
    return len([t for t in run_tasks(w, execution_id, "workflow_node")
                if t["status"] == "waiting" and t["phase"] == "awaitWake"])


def test_iteration_fanout_keeps_its_bound_across_a_worker_restart(world):
    """Four items, ``parallel = 2``, each parks on a 10 minute wait before its POST. Two items are parked at a time
    before the worker is killed, after the restart, and again for the second pair; every item's POST happens once."""
    w = world
    worker = ensure_worker(w, 21)
    tags = ["it-a", "it-b", "it-c", "it-d"]
    wf = save_workflow(w, [
        node("s", "start"),
        node("it", "iteration", inputArray=json.dumps(["a", "b", "c", "d"]), targetNodeId="w", parallel=True,
             maxParallel=2),
        node("w", "wait", waitType="duration", duration=10, durationUnit="minutes"),
        http_node(w, "t1", "post", "it-{{item}}"), node("after", "output")],
        [edge("s", "it"), edge("it", "w"), edge("w", "t1"), edge("t1", "it", "iteration_output"), edge("it", "after")])
    ex = run_workflow(w, wf)
    wait_for(lambda: waiting_nodes(w, ex) == 2, "two items parked", 90)
    settle(3)
    assert waiting_nodes(w, ex) == 2, "more items running than the parallel bound allows"
    assert sorted(i["status"] for i in item_rows(w, ex)).count("pending") >= 2

    worker.kill(signal.SIGKILL)
    w.start_worker(22)
    settle(5)  # the restarted lanes must not start a third item while two are parked
    row = exec_row(w, ex)
    assert row["status"] == "running", (row["status"], row["error"], [(i["status"], i["input"].get("item")) for i in item_rows(w, ex)])
    assert waiting_nodes(w, ex) == 2 and sum(starts(w, t) for t in tags) == 0

    advance_clock(w, 10 * 60 * 1000 + 1000)  # the first pair posts and ends; the second pair parks
    wait_for(lambda: sum(starts(w, t) for t in tags) == 2 and waiting_nodes(w, ex) == 2, "the second pair parked", 60)
    settle(3)
    assert waiting_nodes(w, ex) == 2 and sum(starts(w, t) for t in tags) == 2

    advance_clock(w, 10 * 60 * 1000 + 1000)
    row = wait_status(w, ex)
    settle()
    assert [starts(w, t) for t in tags] == [1, 1, 1, 1], "an item's POST ran twice"
    output = row["results"]["it"]["output"]
    assert output["count"] == 4 and output["success_count"] == 4 and output["error_count"] == 0, output
    items = item_rows(w, ex)
    assert len(items) == 4 and {i["status"] for i in items} == {"completed"}, items


# --- AE5: a scheduled run retries after the engine-clock delay ---------------------------------------------


def test_scheduled_run_fails_once_waits_the_delay_and_completes(world):
    w = world
    ensure_worker(w, 31)
    wf = save_workflow(w, [node("s", "start"), http_node(w, "h", "flaky", "sched"), node("o", "output")],
                       [edge("s", "h"), edge("h", "o")])
    created = api(w, "POST", "/schedules", json={
        "workflow_id": wf, "name": "retry", "schedule_type": "interval", "interval_seconds": 3600,
        "retry_on_failure": True, "max_retries": 2, "retry_delay_seconds": 60})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    triggered = api(w, "POST", f"/schedules/{sid}/trigger")
    assert triggered.status_code == 200, f"{triggered.text}\nbackend:\n{w.backend.tail()}"

    def scheduled_row():
        rows = sql(w, "SELECT status, retry_count, workflow_execution_id, error FROM scheduled_workflow_executions "
                      "WHERE schedule_id = ?", sid.replace("-", ""))
        return rows[0] if rows else None

    wait_for(lambda: starts(w, "sched") == 1 and "done sched 500" in http_lines(w), "the first (failing) attempt", 90)
    settle(5)  # the delay has not passed on the engine clock: no second attempt
    assert starts(w, "sched") == 1
    status, retries, execution_id, _ = scheduled_row()
    assert status == "running" and execution_id, (status, retries)
    execution_id = str(__import__("uuid").UUID(hex=execution_id))
    assert exec_row(w, execution_id)["status"] == "running"

    advance_clock(w, 61_000)
    wait_for(lambda: scheduled_row()[0] == "completed", "the scheduled execution to complete", 90)
    settle()
    assert starts(w, "sched") == 2, "exactly one retry"
    status, retries, _, error = scheduled_row()
    assert (status, retries, error) == ("completed", 1, None), scheduled_row()
    row = wait_status(w, execution_id)
    assert row["trigger"] == "scheduled" and trace_status(row)["h"] == "success", row["trace"]
    last = sql(w, "SELECT last_run_status, last_error FROM scheduled_workflows WHERE id = ?", sid.replace("-", ""))[0]
    assert last == ("completed", None), last


# --- AE6: a long wait survives a worker restart --------------------------------------------------------------


def test_wait_node_survives_a_worker_restart_and_ends_at_its_deadline(world):
    w = world
    worker = ensure_worker(w, 41)
    wf = save_workflow(w, [
        node("s", "start"), node("w", "wait", waitType="duration", duration=2, durationUnit="hours"),
        http_node(w, "h", "post", "wait-after"), node("o", "output")],
        [edge("s", "w"), edge("w", "h"), edge("h", "o")])
    ex = run_workflow(w, wf)
    waiting = wait_for(lambda: [t for t in run_tasks(w, ex, "workflow_node") if t["status"] == "waiting"],
                       "the wait node to park", 90)
    assert waiting[0].get("waitUntil"), waiting
    assert starts(w, "wait-after") == 0

    worker.kill(signal.SIGKILL)
    w.start_worker(42)
    settle(4)
    parked = wait_for(lambda: [t for t in run_tasks(w, ex, "workflow_node") if t["status"] == "waiting"],
                      "the wait node to still be parked", 30)
    assert parked[0]["waitUntil"] == waiting[0]["waitUntil"], "a restart moved the wake time"
    assert exec_row(w, ex)["status"] == "running" and starts(w, "wait-after") == 0

    advance_clock(w, 2 * 3600 * 1000 + 1000)
    row = wait_status(w, ex)
    settle(2)
    assert starts(w, "wait-after") == 1
    assert trace_status(row) == {"s": "success", "w": "success", "h": "success", "o": "success"}, row["trace"]


# --- human in the loop, decided through the inbox after restarts --------------------------------------------


def inbox_review(w, who: str = "admin") -> dict | None:
    r = httpx.get(f"{w.backend_url}/api/v1/inbox", headers={**AUTH, **as_user(who)}, timeout=30)
    assert r.status_code == 200, r.text
    items = [i for i in r.json()["items"] if i["kind"] == "review"]
    return items[0] if items else None


def test_human_wait_is_decided_through_the_inbox_after_restarts(world):
    w = world
    worker = ensure_worker(w, 51)
    wf = save_workflow(w, [
        node("s", "start"), node("h", "human_in_loop", title="Release the batch", description="please review"),
        http_node(w, "after", "post", "hil-after"), node("o", "output")],
        [edge("s", "h"), edge("h", "after"), edge("after", "o")])
    ex = run_workflow(w, wf)
    item = wait_for(lambda: inbox_review(w), "the review in the inbox", 90)
    assert item["title"] == "Release the batch" and item["actions"], item
    waiting = api(w, "GET", f"/workflows/executions/{ex}/waiting-nodes")
    assert waiting.status_code == 200 and waiting.json()["nodes"][0]["nodeId"] == "h", waiting.text

    worker.kill(signal.SIGKILL)
    restart_backend(w)
    w.start_worker(52)
    settle(3)
    assert exec_row(w, ex)["status"] == "running" and starts(w, "hil-after") == 0
    item = inbox_review(w)
    assert item is not None, "the review must survive both restarts"

    decide = item["actions"][0]
    assert decide["type"] == "decide" and decide["allowed"], decide
    r = httpx.post(f"{w.backend_url}{decide['endpoint']}", headers={**AUTH, **as_user("admin")},
                   json={"decision": "approve"}, timeout=30)
    assert r.status_code == 200, f"{r.text}\nbackend:\n{w.backend.tail()}"
    row = wait_status(w, ex)
    settle(2)
    assert starts(w, "hil-after") == 1
    assert row["trace"] and next(t for t in row["trace"] if t["node_id"] == "h")["status"] == "success"
    assert inbox_review(w) is None, "a decided review leaves the inbox"
    again = httpx.post(f"{w.backend_url}{decide['endpoint']}", headers={**AUTH, **as_user("admin")},
                       json={"decision": "approve"}, timeout=30)
    assert again.status_code >= 400 or starts(w, "hil-after") == 1, "a second decision must not run the node again"
    settle(2)
    assert starts(w, "hil-after") == 1


# --- AE9: the backend restarts mid-run -----------------------------------------------------------------------


def test_backend_restart_mid_run_changes_nothing(world):
    w = world
    ensure_worker(w, 61)
    wf = save_workflow(w, [
        node("s", "start"), http_node(w, "a", "post", "be-a"), node("w", "wait", waitType="duration", duration=30,
                                                                  durationUnit="minutes"),
        http_node(w, "c", "post", "be-c"), node("o", "output")],
        [edge("s", "a"), edge("a", "w"), edge("w", "c"), edge("c", "o")])
    ex = run_workflow(w, wf)
    wait_for(lambda: [t for t in run_tasks(w, ex, "workflow_node") if t["status"] == "waiting"], "the wait to park", 90)
    assert starts(w, "be-a") == 1 and starts(w, "be-c") == 0

    restart_backend(w)
    settle(4)  # the reconciler of the new backend passes over the run: it is not stuck, so it leaves it alone
    assert exec_row(w, ex)["status"] == "running"

    advance_clock(w, 31 * 60 * 1000)
    row = wait_status(w, ex)
    settle(3)
    assert starts(w, "be-a") == 1 and starts(w, "be-c") == 1, "a node's POST ran again after the backend restart"
    assert trace_status(row) == {"s": "success", "a": "success", "w": "success", "c": "success", "o": "success"}


@pytest.mark.xfail(strict=True, reason=(
    "Known gap (reported by U22): the engine's node gateway gives up after 3 attempts (about 2 s), shorter than a "
    "backend restart, so the node is failed with 'the backend could not be reached' and the run task faults at "
    "finalize (its finalize post cannot reach the backend either). The run's submission then settles as done, "
    "and workflow_run_sync.on_finished judges a run without a finalize by its stored trace, which has no step "
    "for the node that never recorded one: the execution is marked completed with that node missing."))
def test_backend_restart_with_a_post_in_flight_completes_the_run_once(world):
    """AE9: the backend is replaced while an HTTP node's POST is in flight. The POST happens once and the run
    completes with that node's outcome recorded."""
    w = world
    ensure_worker(w, 62)
    wf = save_workflow(w, [node("s", "start"), http_node(w, "a", "hold", "be-hold"), node("o", "output")],
                       [edge("s", "a"), edge("a", "o")])
    ex = run_workflow(w, wf)
    wait_for(lambda: in_flight(w, "be-hold"), "the POST in flight", 90)
    restart_backend(w)
    open_gate(w)
    wait_for(lambda: exec_row(w, ex)["status"] in ("completed", "failed"), "the run to end", 90)
    row = exec_row(w, ex)
    settle(3)
    assert starts(w, "be-hold") == 1, "the interrupted POST was repeated"
    assert row["status"] == "completed" and trace_status(row) == {"s": "success", "a": "success", "o": "success"}, row


# --- cancel aborts the children -------------------------------------------------------------------------------


def test_cancel_aborts_the_fanout_children_and_starts_nothing_afterwards(world):
    w = world
    ensure_worker(w, 71)
    tags = ["cx-a", "cx-b", "cx-c", "cx-d"]
    wf = save_workflow(w, [
        node("s", "start"),
        node("it", "iteration", inputArray=json.dumps(["a", "b", "c", "d"]), targetNodeId="t1", parallel=True,
             maxParallel=2),
        http_node(w, "t1", "hold", "cx-{{item}}"), node("after", "output")],
        [edge("s", "it"), edge("it", "t1"), edge("t1", "it", "iteration_output"), edge("it", "after")])
    ex = run_workflow(w, wf)
    wait_for(lambda: sum(starts(w, t) for t in tags) == 2, "two items in flight", 90)

    cancelled = api(w, "POST", f"/workflows/{ex}/cancel")
    assert cancelled.status_code == 200, f"{cancelled.text}\nbackend:\n{w.backend.tail()}"
    assert exec_row(w, ex)["status"] == "cancelled"
    conv = str(__import__("uuid").UUID(hex=exec_row(w, ex)["conversation_id"].replace("-", "")))
    wait_for(lambda: all(t["status"] in ("aborted", "done", "faulted")
                         for t in snapshot_of(w, conv).get("tasks", {}).values()), "every task of the run to stop", 60)
    open_gate(w)  # the two held requests return; the run is over, so no lane may pick up item c or d
    settle(6)
    assert sum(starts(w, t) for t in tags) == 2, [starts(w, t) for t in tags]
    row = exec_row(w, ex)
    assert row["status"] == "cancelled" and not row["input_data"].get("abort_pending"), row
    assert api(w, "POST", f"/workflows/{ex}/cancel").status_code == 422
