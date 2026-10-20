"""Helpers for the workflow e2e scenarios (test-only): specs, runs, the counting HTTP target, the engine clock.

A workflow run is one ``workflow_run`` engine task started by the real routes (``/api/v1/workflows``,
``/api/v1/schedules``, ``/api/v1/webhooks``). The tests read the backend's SQLite read model
(``workflow_executions``), the counting target's log (``http.log``, one ``start``/``done`` line per call) and
the engine through its public view.
"""
import json
import uuid

import httpx

from tests.e2e.harness import AUTH, SERVICE_TOKEN, sql, wait_for


def node(nid: str, ntype: str, **data) -> dict:
    return {"id": nid, "type": ntype, "data": data, "position": {"x": 0.0, "y": 0.0}}


def edge(src: str, dst: str, handle: str | None = None) -> dict:
    return {"id": f"{src}-{dst}", "source": src, "target": dst, **({"sourceHandle": handle} if handle else {})}


def http_node(w, nid: str, mode: str, tag: str, **extra) -> dict:
    """An HTTP node that POSTs to the counting target (``post`` answers, ``hold`` waits for its gate, ``flaky``
    fails its first call)."""
    return node(nid, "http", method="POST", url=f"{w.backend_url}/e2e/http/{mode}/{tag}", body="{}", **extra)


def api(w, method: str, path: str, **kw) -> httpx.Response:
    headers = {**AUTH, **kw.pop("headers", {})}
    return httpx.request(method, f"{w.backend_url}/api/v1{path}", headers=headers, timeout=60, **kw)


def save_workflow(w, nodes: list, edges: list, name: str = "e2e") -> str:
    r = httpx.post(f"{w.backend_url}/e2e/workflows", json={"name": name, "spec": {"nodes": nodes, "edges": edges}},
                   timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def run_workflow(w, workflow_id: str, inputs: dict | None = None, key: str | None = None) -> str:
    """Start a saved workflow through the browser route; returns the execution id (200 and 202 both carry it)."""
    headers = {"Idempotency-Key": key} if key else {}
    r = api(w, "POST", f"/workflows/{workflow_id}/run", json={"inputs": inputs or {}}, headers=headers)
    assert r.status_code in (200, 202), f"{r.status_code} {r.text}\nbackend:\n{w.backend.tail()}"
    return r.json()["execution_id"]


def _load(v):
    return json.loads(v) if isinstance(v, str) else v


def exec_row(w, execution_id: str) -> dict:
    rows = sql(w, "SELECT status, results, error, nodes_executed, total_nodes, total_tokens, execution_trace, "
                  "input_data, completed_at, engine_conversation_id, trigger FROM workflow_executions "
                  "WHERE id = ?", execution_id.replace("-", ""))
    assert rows, f"no workflow_executions row for {execution_id}"
    status, results, error, done, total, tokens, trace, input_data, completed, conv, trigger = rows[0]
    return {"status": status, "results": _load(results) or {}, "error": error, "nodes_executed": done,
            "total_nodes": total, "total_tokens": tokens, "trace": _load(trace) or [],
            "input_data": _load(input_data) or {}, "completed_at": completed, "conversation_id": conv,
            "trigger": trigger}


def exec_status(w, execution_id: str) -> str:
    return exec_row(w, execution_id)["status"]


def worker_tails(w) -> str:
    return "\n".join(p.tail(15) for p in w.procs if "worker" in p.log.name)


def wait_status(w, execution_id: str, want: str = "completed", timeout: float = 120.0) -> dict:
    try:
        wait_for(lambda: exec_status(w, execution_id) == want, f"the run to be {want}", timeout)
    except AssertionError as e:
        row = exec_row(w, execution_id)
        raise AssertionError(f"{e}\nrun: {row['status']} error={row['error']} "
                             f"trace={[(t['node_id'], t['status']) for t in row['trace']]}\nworkers:\n{worker_tails(w)}"
                             f"\nbackend:\n{w.backend.tail(25)}") from None
    return exec_row(w, execution_id)


def ensure_worker(w, n: int):
    live = [p for p in w.procs if "worker" in p.log.name and p.p.poll() is None]
    return live[-1] if live else w.start_worker(n)


# --- the counting HTTP target -------------------------------------------------------------------------


def http_lines(w) -> list[str]:
    return w.dw.lines(w.dw_dir / "http.log")


def starts(w, tag: str) -> int:
    return http_lines(w).count(f"start {tag}")


def dones(w, tag: str) -> int:
    return http_lines(w).count(f"done {tag}")


def in_flight(w, tag: str) -> bool:
    return starts(w, tag) > dones(w, tag) and starts(w, tag) > 0


def open_gate(w, tag: str = "all") -> None:
    (w.dw_dir / f"gate-{tag}").write_text("open")


def close_gates(w) -> None:
    for p in w.dw_dir.glob("gate-*"):
        p.unlink(missing_ok=True)


# --- the engine's shared test clock ----------------------------------------------------------------------


def advance_clock(w, ms: int) -> dict:
    r = httpx.post(f"{w.engine_url}/v1/test/clock", json={"advanceMs": ms},
                   headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def new_id() -> str:
    return str(uuid.uuid4())
