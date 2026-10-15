"""U13 / AE3, AE4, AE5, AE8, AE10, R9, R10, R20: Deep Work on the engine survives a kill; cancel stops work.

Same stack as the chat recovery e2e (harness.py: real engine worker with a scripted model, real
gateway/policy/ledger, the real Deep Work routes and ``dw_*`` tools on a file SQLite database). The
network pieces are fakes (dw_fakes.py): ``web_search`` writes a line per call and a query containing
``GATED`` blocks on a gate file, and the E2B executor / proxy tokens / Redis are shared-file fakes so a
second backend process sees what the first created. The model script is dw_script.py.

  * recovery: two workers in one round; worker 2's search is held in flight while the engine worker is
    SIGKILLed; it restarts, the gate opens, the report is produced. Effects once, one finalize,
    sources block, root-bound ledger rows, cost equals the rows, a fresh load rebuilds the same state.
  * steer: a steer sent during the research round is consumed after the tool round.
  * cancel: cancel from a SECOND backend process during research; children aborted, sandboxes torn down
    once per handle, no new invocations afterwards, no worker output afterwards.

Skipped unless ONIVA_E2E=1.
"""
import json
import signal
import time

import httpx
import pytest

from tests.e2e import dw_script as script
from tests.e2e.harness import AUTH, api, sql, wait_for
from tests.e2e.test_chat_engine_recovery import Watcher, snapshot_of  # the chat e2e's reconnecting reader

pytestmark = pytest.mark.e2e


# --- helpers -----------------------------------------------------------------------------------------


def dw(w, method: str, path: str, base: str | None = None, **kw) -> httpx.Response:
    headers = {**AUTH, **kw.pop("headers", {})}
    return httpx.request(method, f"{base or w.backend_url}/api/v1/deep-work{path}", headers=headers, timeout=120, **kw)


def start_run(w, name: str) -> tuple[str, str]:
    """Clarify -> plan -> start through the browser routes; returns (session_id, conversation_id)."""
    q = script.query(name)
    body = {"query": q, "project_id": str(w.project)}
    clarified = dw(w, "POST", "/engine/clarify", json=body)
    assert clarified.status_code == 200, f"{clarified.text}\nbackend:\n{w.backend.tail()}"
    assert clarified.json()["action"] == "proceed", clarified.json()
    session_id = clarified.json()["session_id"]
    planned = dw(w, "POST", "/engine/plan", json={**body, "session_id": session_id, "enriched_query": q})
    assert planned.status_code == 200, planned.text
    assert planned.json()["steps"] == script.sub_queries(name), planned.json()
    started = dw(w, "POST", "/engine", json={**body, "session_id": session_id, "plan": planned.json()["raw_plan"],
                                              "enriched_query": q})
    assert started.status_code == 200, f"{started.text}\nbackend:\n{w.backend.tail()}"
    assert started.json()["status"] == "started" and started.json()["session_id"] == session_id
    return session_id, started.json()["conversation_id"]


def ensure_worker(w, n: int):
    """The running engine worker (tests share one stack and each may leave a different one running)."""
    live = [p for p in w.procs if "worker" in p.log.name and p.p.poll() is None]
    return live[-1] if live else w.start_worker(n)


def searches(w) -> list[str]:
    return w.dw.lines(w.dw.search_log)


def sandbox_events(w) -> list[str]:
    return w.dw.lines(w.dw.sandbox_log)


def run_row(w, session_id: str) -> dict:
    row = sql(w, "SELECT status, phase, round, rounds FROM deep_work_runs WHERE id = ?", session_id.replace("-", ""))
    assert row, f"no deep_work_runs row for {session_id}"
    status, phase, rnd, rounds = row[0]
    return {"status": status, "phase": phase, "round": rnd, "rounds": json.loads(rounds) if isinstance(rounds, str) else rounds}


def invocations(w, tool: str, status: str | None = None) -> list[tuple]:
    q = "SELECT idempotency_key, status FROM tool_invocations WHERE tool = ?"
    return sql(w, q + (" AND status = ?" if status else ""), *([tool, status] if status else [tool]))


def run_completes(w, conv: str, timeout: float = 180.0) -> None:
    """Wait for the run to be ``completed``; on a timeout say where the root conversation got to."""
    try:
        wait_for(lambda: full_state(w, conv)["status"] == "completed", "the run to complete", timeout)
    except AssertionError as e:
        view = snapshot_of(w, conv)
        tail = [(x["kind"], json.dumps(x["payload"])[:200]) for x in view["entries"]][-8:]
        raise AssertionError(f"{e}\nroot entries (tail): {tail}\nrun: {view['run']} inbox: {view['inbox']}\n"
                             f"workers: {[(c['status'], c['recent'][-1:]) for c in view['subagents'].values()]}\n"
                             f"workers:\n" + "\n".join(p.tail(12) for p in w.procs if "worker" in p.log.name)
                             + f"\nbackend:\n{w.backend.tail(12)}") from None


class Counts:
    """Tool invocation counts since construction (the stack is shared by the tests of this module)."""

    TOOLS = ("web_search", "dw_start_round", "dw_save_learnings", "dw_finalize", "e2e_hold", "dw_write_section")

    def __init__(self, w):
        self.w, self.base = w, {t: len(invocations(w, t)) for t in self.TOOLS}

    def __call__(self, tool: str, status: str | None = None) -> int:
        n = len(invocations(self.w, tool, status))
        return n - (self.base[tool] if status is None else 0)


def full_state(w, conv: str, base: str | None = None) -> dict:
    r = dw(w, "GET", f"/by-conversation/{conv}/full-state", base=base)
    assert r.status_code == 200, r.text
    return r.json()


def report_of(w, session_id: str) -> str:
    files = list(w.dw_dir.glob(f"sessions/**/{session_id}/workspace/report.md"))
    assert len(files) == 1, f"report.md of {session_id}: {files}"
    return files[0].read_text()


def entries_of(w, conv: str) -> list[dict]:
    return snapshot_of(w, conv)["entries"]


def in_flight_search(w, query: str) -> bool:
    return f"start {query}" in searches(w) and f"done {query}" not in searches(w)


@pytest.fixture(autouse=True)
def _reset_gate(world):
    world.dw.gate.unlink(missing_ok=True)
    yield


# --- AE3 / AE10 / AE4: kill mid-research -----------------------------------------------------------------


def test_deep_work_survives_engine_kill_mid_research(world):
    w = world
    worker = ensure_worker(w, 1)
    w1, w2 = script.worker_ids("recovery")
    q1, q2 = script.sub_queries("recovery")

    base_log, n = len(searches(w)), Counts(w)
    session_id, conv = start_run(w, "recovery")
    watcher = Watcher(w, conv)
    watcher.start()
    try:
        # Worker 1 finished (search + save), worker 2's search is in flight on the closed gate.
        wait_for(lambda: f"done {q1}" in searches(w) and in_flight_search(w, q2), "worker 2 mid-search", 90)
        wait_for(lambda: any(w1 in str(r) for r in run_row(w, session_id)["rounds"]), "the round to be registered", 30)
        wait_for(lambda: n("dw_save_learnings") == 1, "worker 1 to save its learnings", 60)
        assert full_state(w, conv)["status"] == "running"
        before_kill = len(watcher.connects)

        worker.kill(signal.SIGKILL)
        # Restart while the search is still held: the engine re-runs the interrupted worker; the backend
        # holds the first call's claim, so the replay is answered from it rather than searched again.
        w.start_worker(2)
        w.dw.gate.write_text("open")

        run_completes(w, conv)

        # The browser's reader reconnected with a cursor and saw the run through.
        assert len(watcher.connects) > before_kill and any(c is not None for c in watcher.connects[before_kill:]), watcher.connects
        wait_for(lambda: watcher.seen(script.FINAL_TEXT), "the reader to get the final answer", 30)
    finally:
        watcher.stop.set()

    # --- effects once (AE3) ---------------------------------------------------------------------------------
    log = searches(w)[base_log:]
    assert log.count(f"start {q1}") == 1 and log.count(f"done {q1}") == 1, f"worker 1's search reran: {log}"
    assert log.count(f"start {q2}") == 1 and log.count(f"done {q2}") == 1, f"worker 2's search ran twice: {log}"
    assert (n("web_search"), n("dw_start_round"), n("dw_save_learnings"), n("dw_finalize")) == (2, 1, 2, 1)
    assert n("dw_finalize", "completed") >= 1

    # --- the run and its report ---------------------------------------------------------------------------
    row = run_row(w, session_id)
    assert row["status"] == "completed"
    assert [r["round"] for r in row["rounds"]] == [1]
    report = report_of(w, session_id)
    assert q1 in report and q2 in report, report
    assert report.count("## Findings") == 1 and report.count("## Method") == 1, report
    sources = report.split("## Sources", 1)[1]
    urls = {f"https://example.com/{w1}/{n}" for n in (1, 2)} | {f"https://example.com/{w2}/{n}" for n in (1, 2)}
    assert {u for u in urls if u in sources} == urls and report.count("## Sources") == 1, report

    # --- one worker each, no duplicates ------------------------------------------------------------------------
    view = snapshot_of(w, conv)
    children = view["subagents"]
    workers = [c for c in children.values() if "web_search" in json.dumps(c.get("recent"))]
    assert len(children) == 3 and len(workers) == 2, children  # two workers and one reflector
    assert view["run"]["active"] is False
    stream = json.dumps(view["entries"])
    assert stream.count(f"WORKER {w1}:") == 1 and stream.count(f"WORKER {w2}:") == 1, "a worker was started twice"

    # --- ledger: rows bound to the root; cost equals the rows ----------------------------------------------------
    ledger = wait_for(lambda: (lambda rows: rows if len(rows) >= 8 else None)(sql(
        w, "SELECT conversation_id, input_tokens, output_tokens, cost_usd, purpose FROM llm_usage_records")),
        "ledger rows", 30)
    assert {c for c, *_ in ledger} == {conv.replace("-", "")}, "child usage must be stored under the root conversation"
    # Every tier resolves to a priced model, so every row carries dollars.
    statuses = sql(w, "SELECT status, cost_usd FROM llm_usage_records")
    assert {s for s, _ in statuses} == {"success"}, statuses
    assert all(float(c) > 0 for _, c in statuses), statuses
    purposes = {p for *_, p in ledger}
    cost = api(w, "GET", f"/{conv}/cost")
    assert cost.status_code == 200, cost.text
    body = cost.json()
    assert body["inputTokens"] == sum(r[1] for r in ledger) and body["outputTokens"] == sum(r[2] for r in ledger)
    assert body["totalUsd"] == pytest.approx(sum(float(r[3]) for r in ledger)) and body["unpriced"] is False
    assert [r["runId"] for r in body["runs"]] and sum(r["inputTokens"] for r in body["runs"]) == body["inputTokens"]
    assert {p["purpose"] for p in body["byPurpose"]} == purposes
    stages = {st["stage"] for st in body["byStage"]}
    assert {"orchestrator", "worker", "reflection"} <= stages, body["byStage"]
    assert sum(st["inputTokens"] for st in body["byStage"]) == body["inputTokens"]

    # --- AE4: a fresh load (refresh, second tab) rebuilds the same view ----------------------------------------------
    again = snapshot_of(w, conv)
    assert again["entries"] == view["entries"] and len(again["subagents"]) == 3
    state = full_state(w, conv)
    assert state["runtime"] == "engine" and state["status"] == "completed" and state["session_id"] == session_id


# --- AE10: a kill with a non-replayable write in flight leaves the "resumed after restart" marker ------------------


def test_deep_work_restart_with_write_in_flight_marks_the_resume(world):
    w = world
    worker = ensure_worker(w, 11)
    r1, r2 = script.worker_ids("restart")
    q1, q2 = script.sub_queries("restart")
    effects_before = len(w.effects.read_text().splitlines()) if w.effects.exists() else 0
    base, n = len(searches(w)), Counts(w)
    w.gate.unlink(missing_ok=True)

    session_id, conv = start_run(w, "restart")
    watcher = Watcher(w, conv)
    watcher.start()
    try:
        # Both workers are done; the orchestrator's own write is in flight on the closed gate.
        wait_for(lambda: n("e2e_hold") == 1 and invocations(w, "e2e_hold", "in_flight"), "the held write to be in flight", 90)
        assert n("dw_save_learnings") == 2
        worker.kill(signal.SIGKILL)
        before_kill = len(watcher.connects)
        w.start_worker(30)

        # The engine cannot know whether the write happened: the model gets an "interrupted" result and the
        # run goes on without the write being repeated.
        run_completes(w, conv)
        assert len(watcher.connects) > before_kill and any(c is not None for c in watcher.connects[before_kill:])
        wait_for(lambda: watcher.seen(script.FINAL_TEXT), "the reader to get the final answer", 30)
    finally:
        watcher.stop.set()

    # The write finishes once, in the backend, after the caller that started it was gone.
    w.gate.write_text("open")
    wait_for(lambda: len(w.effects.read_text().splitlines()) - effects_before == 1, "the held write to finish", 30)
    time.sleep(2)
    assert w.effects.read_text().splitlines()[effects_before:] == ["effect res-root"], "the write must happen exactly once"
    assert n("e2e_hold") == 1

    # The root conversation carries the interrupted result (what the card turns into the "resumed" chip).
    entries = entries_of(w, conv)
    interrupted = [e for e in entries if e["kind"] == "tool_result" and e["payload"].get("status") == "interrupted"]
    assert len(interrupted) == 1 and interrupted[0]["payload"]["toolName"] == "e2e_hold", [e["kind"] for e in entries]
    log = searches(w)[base:]
    assert log.count(f"start {q1}") == 1 and log.count(f"start {q2}") == 1, f"a search reran: {log}"
    stream = json.dumps(entries)
    assert stream.count(f"WORKER {r1}:") == 1 and stream.count(f"WORKER {r2}:") == 1, "a worker was started twice"
    report = report_of(w, session_id)
    assert report.count("## Findings") == 1 and report.count("## Method") == 1 and report.count("## Sources") == 1
    assert n("dw_finalize") == 1 and n("dw_save_learnings") == 2 and n("dw_start_round") == 1


# --- AE4: refresh and a second tab mid-research, then AE8: steer --------------------------------------------------


def test_deep_work_second_reader_and_steer_during_research(world):
    w = world
    ensure_worker(w, 12)
    q1, q2 = script.sub_queries("steer")
    base = len(searches(w))

    session_id, conv = start_run(w, "steer")
    wait_for(lambda: f"done {q1}" in searches(w)[base:] and in_flight_search(w, q2), "worker 2 mid-search", 90)

    # AE4: a reader that joins now (refresh / second tab) sees the plan, one finished worker, one running.
    def mid_view():
        v = snapshot_of(w, conv)
        done = [s for s in v["subagents"].values() if s.get("status") not in ("running", "active", None)]
        return v if len(v["subagents"]) == 2 and done else None

    view = wait_for(mid_view, "two workers with one finished", 60)
    second = snapshot_of(w, conv)
    assert second["subagents"].keys() == view["subagents"].keys()
    assert full_state(w, conv)["plan"]["title"] == script.title("steer")
    assert full_state(w, conv)["status"] == "running"

    # AE8: the steer is queued while the round runs and consumed only after the tool round.
    key = "idem-dw-steer-1"
    steer = dw(w, "POST", f"/{session_id}/steer", headers={"Idempotency-Key": key},
               json={"text": "DW_REDIRECT focus on pricing", "requestId": "req-steer-1"})
    assert steer.status_code == 202, steer.text
    again = dw(w, "POST", f"/{session_id}/steer", headers={"Idempotency-Key": key},
               json={"text": "DW_REDIRECT focus on pricing", "requestId": "req-steer-1"})
    assert again.status_code == 202, again.text
    queued = wait_for(lambda: snapshot_of(w, conv)["inbox"]["queue"], "the steer to be queued", 30)
    assert len(queued) == 1 and queued[0].get("mode", "steer") == "steer", queued
    assert not [e for e in entries_of(w, conv) if "DW_REDIRECT" in json.dumps(e)], "the steer was consumed before the round ended"

    w.dw.gate.write_text("open")
    run_completes(w, conv, 60)

    entries = entries_of(w, conv)
    kinds = [e["kind"] for e in entries]
    steer_at = [i for i, e in enumerate(entries) if e["kind"] == "user" and "DW_REDIRECT" in json.dumps(e)]
    results_at = [i for i, e in enumerate(entries)
                  if e["kind"] == "tool_result" and "research_worker" in json.dumps(e["payload"].get("toolName", ""))]
    assert len(steer_at) == 1, kinds
    assert len(results_at) == 2 and steer_at[0] > max(results_at), (kinds, steer_at, results_at)
    assert snapshot_of(w, conv)["inbox"]["queue"] == []
    log = searches(w)[base:]
    assert log.count(f"start {q1}") == 1 and log.count(f"start {q2}") == 1


# --- AE5: cancel during research, from a second backend process ------------------------------------------------------


def test_cancel_during_research_aborts_children_and_tears_down_sandboxes(world):
    w = world
    ensure_worker(w, 13)
    c1, c2 = script.worker_ids("cancel")
    q1, q2 = script.sub_queries("cancel")
    base_sandbox, n = len(sandbox_events(w)), Counts(w)

    session_id, conv = start_run(w, "cancel")
    wait_for(lambda: in_flight_search(w, q1) and in_flight_search(w, q2), "both searches in flight", 90)
    run_prefix = f"{session_id}:"
    created = [e for e in sandbox_events(w)[base_sandbox:] if e.startswith("created ")]
    assert sorted(e.split(" ", 1)[1] for e in created) == sorted([f"{run_prefix}{c1}", f"{run_prefix}{c2}"]), created

    second = w.start_second_backend()  # no local state: it never saw this run start
    cancelled = dw(w, "POST", f"/engine/{session_id}/cancel", base=second.url)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled", cancelled.text

    # The session shows cancelled on both backends; the root run ended aborted; children are terminal.
    assert full_state(w, conv)["status"] == "cancelled"
    assert full_state(w, conv, base=second.url)["status"] == "cancelled"
    view = wait_for(lambda: (lambda v: v if v["run"]["active"] is False else None)(snapshot_of(w, conv)), "the root run to stop", 60)
    assert len(view["subagents"]) == 2
    assert {s["status"] for s in view["subagents"].values()} == {"aborted"}, view["subagents"]

    # Sandboxes: torn down exactly once per handle, even after a repeated cancel and the lazy reaper.
    wait_for(lambda: len([e for e in sandbox_events(w)[base_sandbox:] if e.startswith("torn ")]) >= 2, "sandbox teardown", 30)
    again = dw(w, "POST", f"/engine/{session_id}/cancel", base=second.url)
    assert again.status_code == 200 and again.json()["status"] == "cancelled"
    full_state(w, conv)  # reaps a terminal run
    torn = sorted(e.split(" ", 1)[1] for e in sandbox_events(w)[base_sandbox:] if e.startswith("torn "))
    assert torn == sorted([f"{run_prefix}{c1}", f"{run_prefix}{c2}"]), torn

    # No worker produces output afterwards: open the gate, let anything still running finish.
    invocations_at_abort = len(sql(w, "SELECT 1 FROM tool_invocations"))
    entries_at_abort = len(entries_of(w, conv))
    w.dw.gate.write_text("open")
    wait_for(lambda: f"done {q1}" in searches(w) and f"done {q2}" in searches(w), "the held searches to finish", 30)
    time.sleep(5)
    assert len(sql(w, "SELECT 1 FROM tool_invocations")) == invocations_at_abort, "a tool was invoked after the abort"
    assert n("dw_save_learnings") == 0 and n("dw_finalize") == 0 and n("dw_write_section") == 0
    assert len(entries_of(w, conv)) == entries_at_abort, "the root conversation moved after the abort"
    after = snapshot_of(w, conv)
    assert after["run"]["active"] is False
    assert {s["status"] for s in after["subagents"].values()} == {"aborted"}
    row = run_row(w, session_id)
    assert row["status"] == "cancelled" and all(not (r.get("workers") or {}) for r in row["rounds"]), row
    assert len([e for e in sandbox_events(w)[base_sandbox:] if e.startswith("torn ")]) == 2
