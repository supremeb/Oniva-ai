"""U36 load gate driver: do automation runs and chat share one engine session without hurting chat?

Profiles
  full   200 workflow runs of 10 nodes + 20 Fleet runs against one worker on one session, with a steady chat load.
  smoke  5 workflow runs + 1 Fleet run, so the driver itself does not rot (run by ``test_automation_smoke.py``).

Phases: (1) chat first-token baseline alone; (2) the same chat load while the automation runs are started and
drained, the engine test clock advanced past the 30 s waits instead of sleeping; (3) drain, then the gate.

First-token latency: milliseconds from the chat submission request being sent to the scripted answer text arriving
on the backend's SSE relay (a text-only turn, one model request).

What the stack lets us observe without product code, and what it does not:
  * engine commit latency: not observable per commit. Reported as the inter-commit gap p50/p95/p99 from
    ``engine_commits.committed_at`` (a cadence proxy, labelled as such) plus commits per second.
  * outbox depth: rows in ``engine_usage_outbox`` sampled every second (max and at the end).
  * reconciler lag: not directly observable. Reported as ``drain_lag_s``, seconds from the last clock advance to
    every read-model row being terminal.
  * worker CPU/memory: ``psutil`` over the worker's process tree if importable, else ``ps`` for its process group.
  * model requests: the model script runs in the worker process, so requests are the scripts' arithmetic
    (``fakes.expected_model_requests``), compared with the ledger rows (``llm_usage_records``).

Run for real: ``cd backend && ONIVA_E2E=1 PATH=/opt/homebrew/bin:$PATH uv run python -m tests.load.automation_load
--profile full`` (a throwaway Postgres, the backend and one engine worker are booted, as in the e2e suite).
"""
import argparse
import dataclasses
import datetime
import json
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from tests.e2e import fleet_script
from tests.e2e.harness import AUTH, new_conversation, sql, stack, wait_for
from tests.e2e.harness import api as chat_api
from tests.e2e.test_fleet_engine_recovery import start_job
from tests.e2e.workflow_helpers import advance_clock, run_workflow
from tests.load import fakes
from tests.load.gate import evaluate_gate, percentile

TERMINAL = ("completed", "failed", "cancelled", "canceled", "timed_out", "error")


@dataclass(frozen=True)
class Profile:
    name: str
    workflow_runs: int
    fleet_runs: int
    baseline_chat_samples: int
    min_loaded_chat_samples: int
    chat_interval_s: float
    start_threads: int
    drain_timeout_s: float


PROFILES = {
    "full": Profile("full", 200, 20, 60, 60, 0.5, 16, 900.0),
    "smoke": Profile("smoke", 5, 1, 5, 3, 0.2, 4, 240.0),
}


# --- measurements ------------------------------------------------------------------------------------


def first_token_ms(w, timeout: float = 60.0) -> float:
    """One chat turn; milliseconds until the scripted answer text arrives on the SSE relay."""
    conv = new_conversation(w)
    t0 = time.perf_counter()
    r = chat_api(w, "POST", f"/{conv}/submissions", json={"text": fakes.CHAT_TEXT, "requestId": f"load-{time.time_ns()}"})
    assert r.status_code == 202, r.text
    text = ""
    with httpx.stream("GET", f"{w.backend_url}/api/v1/engine/conversations/{conv}/watch", headers=AUTH,
                      timeout=httpx.Timeout(timeout, read=timeout)) as s:
        assert s.status_code == 200, s.read()
        for chunk in s.iter_text():
            text += chunk
            if fakes.CHAT_ANSWER in text:
                return (time.perf_counter() - t0) * 1000.0
            assert time.perf_counter() - t0 < timeout, "chat answer never arrived"
    raise AssertionError("chat stream ended without the answer")


class ChatLoad:
    """A steady chat load: one turn at a time, one every ``interval`` seconds, until stopped."""

    def __init__(self, w, interval: float):
        self.w, self.interval = w, interval
        self.samples: list[tuple[float, float]] = []  # (monotonic start, first-token ms)
        self.errors: list[str] = []
        self.submitted = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.submitted += 1
                self.samples.append((started, first_token_ms(self.w)))
            except Exception as e:  # noqa: BLE001 - recorded; the gate fails on any chat error (a dropped turn would hide a stall)
                self.errors.append(repr(e)[:200])
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=90)


class Sampler:
    """Worker CPU/memory and outbox depth, once a second, for the length of the run."""

    def __init__(self, w, worker):
        self.w, self.pid = w, worker.p.pid
        self.extra_pids = [s.p.pid for s in getattr(worker, "shard_procs", [])]  # the background shard
        self.cpu: list[float] = []
        self.rss_mb: list[float] = []
        self.outbox: list[int] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._procs: dict = {}

    def worker_usage(self) -> tuple[float, float]:
        """CPU % and RSS MB summed over every engine worker (one, or the interactive and background shards)."""
        pids = [self.pid, *self.extra_pids]
        try:
            import psutil
            procs = []
            for pid in pids:
                root = self._procs.setdefault(pid, psutil.Process(pid))
                procs.append(root)
                for child in root.children(recursive=True):
                    procs.append(self._procs.setdefault(child.pid, child))  # cached: cpu_percent needs the prior call
            return (sum(p.cpu_percent(interval=None) for p in procs),
                    sum(p.memory_info().rss for p in procs) / 1e6)
        except ImportError:
            out = subprocess.run(["ps", "-axo", "pgid=,pcpu=,rss="], capture_output=True, text=True).stdout
            rows = [line.split() for line in out.splitlines() if line.split()[:1] and line.split()[0] in map(str, pids)]
            return sum(float(r[1]) for r in rows), sum(float(r[2]) for r in rows) / 1024.0
        except Exception:  # noqa: BLE001 - the worker may have just exited
            return 0.0, 0.0

    def _run(self):
        while not self._stop.is_set():
            cpu, rss = self.worker_usage()
            self.cpu.append(cpu)
            self.rss_mb.append(rss)
            depth = pg_scalar(self.w, "SELECT count(*) FROM engine_usage_outbox")
            if depth is not None:
                self.outbox.append(int(depth))
            self._stop.wait(1.0)

    def start(self):
        self.worker_usage()  # prime psutil's cpu counter
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)


def pg_scalar(w, query: str):
    import psycopg2
    try:
        con = psycopg2.connect(w.pg_url)
        try:
            with con.cursor() as cur:
                cur.execute(query)
                return cur.fetchone()[0]
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - observability must not break the run
        return None


def commit_cadence_ms(w) -> dict:
    import psycopg2
    try:
        con = psycopg2.connect(w.pg_url)
        try:
            with con.cursor() as cur:
                cur.execute("SELECT session_id, EXTRACT(EPOCH FROM committed_at) FROM engine_commits "
                            "ORDER BY session_id, seq")
                rows = [(r[0], float(r[1])) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return {"observable": False}
    # Each session has its own commit line (seq): the gap is measured within a session, never across shards.
    per_session: dict = {}
    for sid, t in rows:
        per_session.setdefault(sid, []).append(t)
    gaps = [(b - a) * 1000.0 for ts_ in per_session.values() for a, b in zip(ts_, ts_[1:])]
    ts = sorted(t for _, t in rows)
    span = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
    return {"observable": True, "sessions": {sid: len(v) for sid, v in sorted(per_session.items())}, "kind": "inter-commit gap (cadence proxy, not per-commit latency)",
            "commits": len(ts), "commits_per_s": round(len(ts) / span, 2) if span > 0 else None,
            "p50_ms": round(percentile(gaps, 50), 2), "p95_ms": round(percentile(gaps, 95), 2),
            "p99_ms": round(percentile(gaps, 99), 2)}


def stats(values: list[float]) -> dict:
    return {"n": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(percentile(values, 95), 1)}


# --- the run ---------------------------------------------------------------------------------------------


def _non_terminal(w, execution_ids: list[str], job_ids: list[str]) -> tuple[int, int]:
    """(non-terminal, failed) over the runs this profile started, read from the backend's read model."""
    ex = {r[0]: r[1] for r in sql(w, "SELECT id, status FROM workflow_executions")}
    jobs = {r[0]: r[1] for r in sql(w, "SELECT id, status FROM background_jobs")}
    statuses = [ex.get(i.replace("-", "")) for i in execution_ids] + [jobs.get(i.replace("-", "")) for i in job_ids]
    return (sum(1 for s in statuses if s not in TERMINAL), sum(1 for s in statuses if s in TERMINAL and s != "completed"))


def failure_reasons(w, execution_ids: list[str], job_ids: list[str]) -> list[str]:
    """Why runs ended other than completed: ``kind id status: error`` (first 10), so a red gate says what broke."""
    out: list[str] = []
    for kind, table, ids in (("workflow", "workflow_executions", execution_ids), ("fleet", "background_jobs", job_ids)):
        rows = {r[0]: r[1:] for r in sql(w, f"SELECT id, status, error FROM {table}")}
        for i in ids:
            status, error = rows.get(i.replace("-", ""), (None, None))
            if status != "completed":
                out.append(f"{kind} {i} {status}: {str(error)[:160]}")
    return out[:10]


def run_profile(w, profile: Profile, worker) -> dict:
    fakes.open_all_gates(w)
    fakes.unbound_fleet_budget(w)
    workflow_id = fakes.load_workflow(w)
    sampler = Sampler(w, worker)
    sampler.start()
    t_begin = time.monotonic()

    # 1. chat alone
    baseline = [first_token_ms(w) for _ in range(profile.baseline_chat_samples)]

    # 2. chat under automation load
    chat = ChatLoad(w, profile.chat_interval_s)
    chat.start()
    execution_ids: list[str] = []
    job_ids: list[str] = []
    start_errors: list[str] = []

    def start_workflow(n: int):
        return run_workflow(w, workflow_id, {"n": str(n)})

    def start_fleet(_):
        return start_job(w, fakes.FLEET_SCENARIO)[0]

    t_load = time.monotonic()
    with ThreadPoolExecutor(profile.start_threads) as pool:
        futures = [("wf", pool.submit(start_workflow, n)) for n in range(profile.workflow_runs)]
        futures += [("fleet", pool.submit(start_fleet, n)) for n in range(profile.fleet_runs)]
        for kind, f in futures:
            try:
                (execution_ids if kind == "wf" else job_ids).append(f.result(timeout=300))
            except Exception as e:  # noqa: BLE001
                start_errors.append(f"{kind}: {repr(e)[:200]}")

    # 3. drain: advance the engine clock past the 30 s waits (repeatedly, so late arrivals are released too)
    last_advance = time.monotonic()
    deadline = time.monotonic() + profile.drain_timeout_s
    while time.monotonic() < deadline:
        advance_clock(w, (fakes.WAIT_SECONDS + 1) * 1000)
        last_advance = time.monotonic()
        non_terminal, _ = _non_terminal(w, execution_ids, job_ids)
        if non_terminal == 0:
            break
        time.sleep(2.0)
    drain_done = time.monotonic()
    drain_lag_s = drain_done - last_advance
    loaded_window_s = drain_done - t_load
    chat.stop()
    # only turns that started while automation was still running count as loaded; later ones ran on an idle engine
    loaded = [ms for t0, ms in chat.samples if t0 < drain_done]
    sampler.stop()

    # the ledger is flushed through the outbox: wait for it to settle before comparing
    expected = fakes.expected_model_requests(len(execution_ids), len(job_ids), baseline.__len__() + chat.submitted)
    try:
        wait_for(lambda: sql(w, "SELECT count(*) FROM llm_usage_records")[0][0] >= expected, "the ledger to settle", 60)
    except AssertionError:
        pass
    time.sleep(2.0)  # let a stray duplicate show
    ledger_rows = sql(w, "SELECT count(*) FROM llm_usage_records")[0][0]
    non_terminal, failed = _non_terminal(w, execution_ids, job_ids)
    started, done = fakes.http_calls(w)

    base_p95 = percentile(baseline, 95)
    loaded_p95 = percentile(loaded, 95)
    gate = evaluate_gate(base_p95, loaded_p95, non_terminal + len(start_errors), ledger_rows, expected,
                         failed_runs=failed, chat_errors=len(chat.errors), loaded_samples=len(loaded),
                         min_loaded_samples=profile.min_loaded_chat_samples, http_started=started,
                         http_done=done, expected_http_calls=len(execution_ids))
    return {
        "profile": profile.name,
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
        "hardware": hardware(),
        "workload": {"workflow_runs": len(execution_ids), "nodes_per_run": fakes.WORKFLOW_NODES,
                     "fleet_runs": len(job_ids), "start_errors": start_errors,
                     "chat_baseline_samples": len(baseline), "chat_loaded_samples": len(loaded),
                     "engine_shards": getattr(w, "shards", 1), "chat_errors": len(chat.errors), "chat_error_samples": chat.errors[:5], "http_calls_started": started, "http_calls_done": done,
                     "wall_s": round(time.monotonic() - t_begin, 1), "loaded_window_s": round(loaded_window_s, 1)},
        "chat_first_token": {"baseline": stats(baseline), "loaded": stats(loaded),
                             "p95_regression": None if gate.chat_p95_regression is None
                             else round(gate.chat_p95_regression, 4)},
        "engine_commits": commit_cadence_ms(w),
        "worker": {"cpu_percent_mean": round(sum(sampler.cpu) / max(1, len(sampler.cpu)), 1),
                   "cpu_percent_max": round(max(sampler.cpu, default=0.0), 1),
                   "rss_mb_max": round(max(sampler.rss_mb, default=0.0), 1)},
        "outbox_depth": {"max": max(sampler.outbox, default=None), "end": pg_scalar(w, "SELECT count(*) FROM engine_usage_outbox")},
        "reconciler_lag": {"observable": False, "drain_lag_s_proxy": round(drain_lag_s, 1)},
        "after_drain": {"non_terminal_runs": non_terminal, "failed_runs": failed, "start_errors": len(start_errors),
                        "failure_reasons": failure_reasons(w, execution_ids, job_ids) if failed or non_terminal else []},
        "ledger": {"rows": ledger_rows, "model_requests": expected},
        "gate": {"passed": gate.passed, "reasons": gate.reasons, "max_regression": 0.20},
    }


def hardware() -> dict:
    info = {"machine": platform.machine(), "system": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(), "cpus": __import__("os").cpu_count()}
    try:
        import psutil
        info["memory_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except ImportError:
        pass
    return info


def format_report(result: dict) -> str:
    c, ch = result["chat_first_token"], result["engine_commits"]
    verdict = "PASS" if result["gate"]["passed"] else "FAIL"
    reg = c["p95_regression"]
    lines = [
        f"### U36 load gate: {verdict} ({result['profile']} profile, {result['date']})",
        "",
        f"- hardware: {json.dumps(result['hardware'])}",
        f"- workload: {result['workload']['workflow_runs']} workflow runs x {result['workload']['nodes_per_run']} nodes, "
        f"{result['workload']['fleet_runs']} Fleet runs, {result['workload']['wall_s']} s wall, "
        f"{result['workload'].get('engine_shards', 1)} engine shard(s)",
        f"- chat first token, baseline: p50 {c['baseline']['p50_ms']} ms, p95 {c['baseline']['p95_ms']} ms (n={c['baseline']['n']})",
        f"- chat first token, loaded: p50 {c['loaded']['p50_ms']} ms, p95 {c['loaded']['p95_ms']} ms (n={c['loaded']['n']}), "
        f"p95 regression {'n/a' if reg is None else f'{reg:+.1%}'} (limit +20%)",
        ("- engine commits (inter-commit gap proxy): " + f"p50 {ch['p50_ms']} / p95 {ch['p95_ms']} / p99 {ch['p99_ms']} ms, "
         f"{ch['commits']} commits") if ch.get("observable") else "- engine commits: not observable",
        f"- worker: cpu mean {result['worker']['cpu_percent_mean']}% max {result['worker']['cpu_percent_max']}%, "
        f"rss max {result['worker']['rss_mb_max']} MB",
        f"- outbox depth: max {result['outbox_depth']['max']}, end {result['outbox_depth']['end']}",
        f"- reconciler lag: not directly observable; drain lag proxy {result['reconciler_lag']['drain_lag_s_proxy']} s",
        f"- non-terminal runs after drain: {result['after_drain']['non_terminal_runs']} "
        f"(failed: {result['after_drain']['failed_runs']}, start errors: {result['after_drain']['start_errors']})",
        *[f"  - {r}" for r in result["after_drain"].get("failure_reasons", [])],
        f"- ledger rows {result['ledger']['rows']} vs model requests {result['ledger']['model_requests']}",
        f"- gate: {verdict}" + ("" if result["gate"]["passed"] else " - " + "; ".join(result["gate"]["reasons"])),
    ]
    return "\n".join(lines)


def print_report(result: dict) -> None:
    print("\n=== U36 RESULT (json) ===")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("\n=== U36 RESULT (markdown) ===")
    print(format_report(result))


def run_on_stack(profile_name: str) -> dict:
    """Boot the e2e stack (Postgres, backend, one engine worker) and run a profile on it."""
    with stack() as w:
        return run_profile(w, PROFILES[profile_name], w.start_worker(1))


def profile_with_overrides(base: Profile, **overrides) -> Profile:
    """``base`` with the non-None overrides applied (the CI workflow's knobs); the pass criteria are not an override."""
    changes = {k: v for k, v in overrides.items() if v is not None}
    name = f"{base.name}+custom" if changes else base.name
    return dataclasses.replace(base, name=name, **changes)


def write_outputs(result: dict, json_path: str | None = None, markdown_path: str | None = None) -> None:
    """Machine-readable result and the markdown summary (for ``$GITHUB_STEP_SUMMARY``), written only when asked."""
    if json_path:
        Path(json_path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if markdown_path:
        Path(markdown_path).write_text(format_report(result) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    ap.add_argument("--workflow-runs", type=int, help="override the profile's workflow run count")
    ap.add_argument("--fleet-runs", type=int, help="override the profile's Fleet run count")
    ap.add_argument("--baseline-samples", type=int, dest="baseline_chat_samples", help="chat samples with no load")
    ap.add_argument("--min-loaded-samples", type=int, dest="min_loaded_chat_samples",
                    help="loaded chat samples the gate requires")
    ap.add_argument("--chat-interval", type=float, dest="chat_interval_s", help="seconds between chat turns")
    ap.add_argument("--start-threads", type=int, help="concurrent run starters")
    ap.add_argument("--drain-timeout", type=float, dest="drain_timeout_s", help="seconds allowed to drain")
    ap.add_argument("--json-out", help="write the result as JSON to this path")
    ap.add_argument("--markdown-out", help="write the markdown summary to this path")
    args = ap.parse_args(argv)
    profile = profile_with_overrides(
        PROFILES[args.profile], workflow_runs=args.workflow_runs, fleet_runs=args.fleet_runs,
        baseline_chat_samples=args.baseline_chat_samples, min_loaded_chat_samples=args.min_loaded_chat_samples,
        chat_interval_s=args.chat_interval_s, start_threads=args.start_threads, drain_timeout_s=args.drain_timeout_s)
    with stack() as w:
        result = run_profile(w, profile, w.start_worker(1))
    print_report(result)
    write_outputs(result, args.json_out, args.markdown_out)
    return 0 if result["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
