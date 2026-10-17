"""U10 / AE1, AE2, R1, R2, R10: a Fleet run on the engine survives a worker kill; cancel stops it; steer is consumed.

Same stack as the Deep Work e2e (harness.py): a real engine worker with a scripted model (fleet_script.py),
the real gateway/policy/ledger, and in the backend process the real Fleet routes (``/api/v1/background-jobs``,
``/api/v1/fleet``), start/steer/cancel service, ``/internal/runs/finished`` push, run-handler registry and run
reconciler, on a file SQLite database. ``web_search`` is the dw fake (a ``GATED`` query holds until the gate file
exists) and ``e2e_hold`` is the non-replayable write. The agent delivers its result to ``/e2e/webhook`` on the
backend, which logs one line per delivery.

  * recovery: the worker is SIGKILLed while the search is held; it restarts, the run completes once, the write
    happens once, one delivery, one ledger row per model request.
  * steer: an intervene during the held search is queued, deduplicated, and read by the next model request.
  * cancel: cancel during the held search; the later write never starts and the job stays ``cancelled``.
  * backend restart: the backend process is replaced mid-run; the run still completes once.

Skipped unless ONIVA_E2E=1.
"""
import json
import signal
import sys
import time

import httpx
import pytest

from tests.e2e import fleet_script as script
from tests.e2e.harness import AUTH, BACKEND, GATEWAY_TOKEN, Proc, sql, wait_for
from tests.e2e.test_chat_engine_recovery import snapshot_of

pytestmark = pytest.mark.e2e


# --- helpers -----------------------------------------------------------------------------------------


def fleet(w, method: str, path: str, **kw) -> httpx.Response:
    headers = {**AUTH, **kw.pop("headers", {})}
    return httpx.request(method, f"{w.backend_url}/api/v1{path}", headers=headers, timeout=60, **kw)


def start_job(w, name: str) -> tuple[str, str]:
    """Create a Fleet job through the browser route; returns (job_id, conversation_id)."""
    r = fleet(w, "POST", "/background-jobs/", json={
        "job_type": "background_agent", "agent_id": str(w.agent), "input_data": {"task": script.task(name)}})
    assert r.status_code == 201, f"{r.text}\nbackend:\n{w.backend.tail()}"
    job_id = r.json()["id"]
    row = job_row(w, job_id)
    assert row["status"] == "running" and row["conversation_id"], (row, w.backend.tail())
    return job_id, str(_uuid(row["conversation_id"]))


def _uuid(hexed: str):
    import uuid
    return uuid.UUID(hexed)


def job_row(w, job_id: str) -> dict:
    rows = sql(w, "SELECT status, conversation_id, result, error, input_data, execution_trace "
                  "FROM background_jobs WHERE id = ?", job_id.replace("-", ""))
    assert rows, f"no background_jobs row for {job_id}"
    status, conv, result, error, input_data, trace = rows[0]
    load = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731
    return {"status": status, "conversation_id": conv, "result": load(result), "error": error,
            "input_data": load(input_data) or {}, "trace": load(trace) or []}


def job_status(w, job_id: str) -> str:
    return job_row(w, job_id)["status"]


def job_completes(w, job_id: str, timeout: float = 120.0) -> dict:
    try:
        wait_for(lambda: job_status(w, job_id) == "completed", "the Fleet job to complete", timeout)
    except AssertionError as e:
        row = job_row(w, job_id)
        raise AssertionError(f"{e}\njob: {row['status']} error={row['error']}\nworkers:\n"
                             + "\n".join(p.tail(12) for p in w.procs if "worker" in p.log.name)
                             + f"\nbackend:\n{w.backend.tail(20)}") from None
    return job_row(w, job_id)


def ensure_worker(w, n: int):
    live = [p for p in w.procs if "worker" in p.log.name and p.p.poll() is None]
    return live[-1] if live else w.start_worker(n)


def searches(w) -> list[str]:
    return w.dw.lines(w.dw.search_log)


def in_flight_search(w, query: str) -> bool:
    return f"start {query}" in searches(w) and f"done {query}" not in searches(w)


def effects(w) -> list[str]:
    return w.effects.read_text().splitlines() if w.effects.exists() else []


def deliveries(w, text: str) -> list[dict]:
    return [d for d in map(json.loads, w.dw.lines(w.dw.webhook_log)) if text in str(d.get("result"))]


def invocations(w, tool: str) -> int:
    return len(sql(w, "SELECT 1 FROM tool_invocations WHERE tool = ?", tool))


def ledger(w, conv: str) -> list[tuple]:
    return sql(w, "SELECT input_tokens, output_tokens, cost_usd FROM llm_usage_records WHERE conversation_id = ? "
                  "ORDER BY input_tokens", conv.replace("-", ""))


def entries_of(w, conv: str) -> list[dict]:
    return snapshot_of(w, conv)["entries"]


@pytest.fixture(autouse=True)
def _reset_gates(world):
    world.dw.gate.unlink(missing_ok=True)
    world.gate.unlink(missing_ok=True)
    yield


def assert_completed_once(w, name: str, job_id: str, conv: str, effects_before: int,
                          tools_expected=("web_search", "e2e_hold")) -> None:
    """The run's result, one write, one delivery, one ledger row per model request, one trace of each call."""
    row = job_row(w, job_id)
    assert row["result"]["output"] == script.final_text(name), row
    time.sleep(3)  # a duplicate delivery or write would show up by now (the reconciler passes every 3 s)
    assert len(deliveries(w, script.final_text(name))) == 1, w.dw.lines(w.dw.webhook_log)
    assert effects(w)[effects_before:] == [f"effect {script.note(name)}"], "the write must happen exactly once"
    rows_ = ledger(w, conv)
    assert [(i, o) for i, o, _ in rows_] == sorted(script.TOKENS[name]), rows_
    assert all(float(c) > 0 for *_, c in rows_), rows_
    assert row["result"]["total_tokens"] == sum(i + o for i, o, _ in rows_), (row["result"], rows_)
    tools = [s["tool_name"] for s in row["trace"] if s.get("type") == "tool_call"]
    assert tools == list(tools_expected), row["trace"]
    assert job_status(w, job_id) == "completed"


# --- AE1 / AE2: kill the worker mid-run -----------------------------------------------------------------


def test_fleet_run_survives_worker_kill_and_completes_once(world):
    w = world
    worker = ensure_worker(w, 1)
    q = script.search_query("recovery")
    base_log, effects_before = len(searches(w)), len(effects(w))
    w.gate.write_text("open")  # the write is not what is held here

    job_id, conv = start_job(w, "recovery")
    wait_for(lambda: in_flight_search(w, q), "the search to be in flight", 90)
    assert job_status(w, job_id) == "running"

    worker.kill(signal.SIGKILL)
    w.start_worker(2)
    w.dw.gate.write_text("open")

    job_completes(w, job_id)
    log = searches(w)[base_log:]
    assert log.count(f"start {q}") == 1 and log.count(f"done {q}") == 1, f"the search ran twice: {log}"
    assert_completed_once(w, "recovery", job_id, conv, effects_before)

    # The job list and detail show it (no engine bookkeeping leaks), and the same job is not started twice.
    detail = fleet(w, "GET", f"/background-jobs/{job_id}")
    assert detail.status_code == 200 and detail.json()["status"] == "completed", detail.text
    assert sql(w, "SELECT COUNT(*) FROM background_jobs")[0][0] >= 1
    assert sql(w, "SELECT COUNT(*) FROM engine_conversations WHERE run_kind = 'fleet' AND run_ref_id = ?",
               job_id.replace("-", ""))[0][0] == 1


# --- steer ------------------------------------------------------------------------------------------------


def test_fleet_steer_is_queued_and_consumed_by_the_next_model_request(world):
    w = world
    ensure_worker(w, 11)
    q = script.search_query("steer")

    job_id, conv = start_job(w, "steer")
    wait_for(lambda: in_flight_search(w, q), "the search to be in flight", 90)

    sent = [fleet(w, "POST", f"/fleet/runs/{job_id}/intervene", json={"message": script.REDIRECT}) for _ in range(2)]
    assert [r.status_code for r in sent] == [200, 200], [r.text for r in sent]
    queued = wait_for(lambda: snapshot_of(w, conv)["inbox"]["queue"], "the steer to be queued", 30)
    assert len(queued) == 1, queued  # the repeat carried the same key: deduplicated by the engine
    assert not [e for e in entries_of(w, conv) if "FLEET_REDIRECT" in json.dumps(e)], "consumed before the tool round ended"

    w.dw.gate.write_text("open")
    row = job_completes(w, job_id, 90)
    assert row["result"]["output"] == script.final_text("steer"), row["result"]
    entries = entries_of(w, conv)
    kinds = [e["kind"] for e in entries]
    steer_at = [i for i, e in enumerate(entries) if e["kind"] == "user" and "FLEET_REDIRECT" in json.dumps(e)]
    result_at = [i for i, e in enumerate(entries) if e["kind"] == "tool_result"]
    assert len(steer_at) == 1 and len(result_at) == 1 and steer_at[0] > result_at[0], (kinds, steer_at, result_at)
    assert snapshot_of(w, conv)["inbox"]["queue"] == []
    assert [(i, o) for i, o, _ in ledger(w, conv)] == sorted(script.TOKENS["steer"])
    time.sleep(3)
    assert len(deliveries(w, script.final_text("steer"))) == 1


# --- cancel -----------------------------------------------------------------------------------------------


def test_fleet_cancel_during_a_tool_call_starts_nothing_afterwards(world):
    w = world
    ensure_worker(w, 12)
    name, q = "cancel", script.search_query("cancel")
    holds_before, effects_before = invocations(w, "e2e_hold"), len(effects(w))
    w.gate.write_text("open")  # if the write ever started it would finish: a leak would be visible

    job_id, conv = start_job(w, name)
    wait_for(lambda: in_flight_search(w, q), "the search to be in flight", 90)

    cancelled = fleet(w, "POST", f"/background-jobs/{job_id}/cancel", json={"reason": "no longer needed"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled", cancelled.text
    assert job_status(w, job_id) == "cancelled"
    again = fleet(w, "POST", f"/background-jobs/{job_id}/cancel")
    assert again.status_code == 400, again.text

    view = wait_for(lambda: (lambda v: v if v["run"]["active"] is False else None)(snapshot_of(w, conv)),
                    "the engine run to stop", 60)
    entries_at_abort = len(view["entries"])
    w.dw.gate.write_text("open")  # the held search returns; the run is over, so nothing may follow it
    wait_for(lambda: f"done {q}" in searches(w), "the held search to return", 30)
    time.sleep(6)  # the push of the aborted run, the reconciler and any stray tool call all get time to happen

    assert invocations(w, "e2e_hold") == holds_before, "a tool call started after the cancel"
    assert len(effects(w)) == effects_before
    after = snapshot_of(w, conv)
    assert after["run"]["active"] is False and len(after["entries"]) == entries_at_abort
    row = job_row(w, job_id)
    assert row["status"] == "cancelled" and row["result"] is None, row
    assert not row["input_data"].get("abort_pending"), row["input_data"]
    assert deliveries(w, script.final_text(name)) == []
    steer = fleet(w, "POST", f"/fleet/runs/{job_id}/intervene", json={"message": "too late"})
    assert steer.status_code == 404, steer.text


# --- the backend restarts mid-run -------------------------------------------------------------------------


def test_fleet_run_survives_a_backend_restart(world):
    w = world
    ensure_worker(w, 13)
    q = script.search_query("restart")
    effects_before = len(effects(w))
    w.gate.write_text("open")

    job_id, conv = start_job(w, "restart")
    wait_for(lambda: in_flight_search(w, q), "the search to be in flight", 90)

    # Replace the backend process on the same port and database: no state survives but the stores.
    w.backend.kill(signal.SIGKILL)
    info = w.tmp / "info-restarted.json"
    restarted = Proc([sys.executable, "-m", "tests.e2e.backend_app"],
                     {**w.backend_env, "E2E_SEED": "0", "E2E_INFO": str(info)}, BACKEND, w.tmp / "backend-restarted.log")
    w.procs.append(restarted)
    w.backend = restarted

    def ready():
        assert restarted.p.poll() is None, restarted.tail()
        return info.exists() and httpx.get(f"{w.backend_url}/internal/ping", headers={
            "Authorization": f"Bearer {GATEWAY_TOKEN}"}).status_code == 200
    wait_for(ready, "the restarted backend", 90)
    # The search that was in flight in the dead process is reported to the model as unreachable; it searches again.
    job_completes(w, job_id, 180)
    assert_completed_once(w, "restart", job_id, conv, effects_before, ("web_search", "web_search", "e2e_hold"))
