"""Boot the e2e stack for the browser test and serve a tiny control API (test-only).

    E2E_CORS_ORIGIN=http://localhost:3100 python -m tests.e2e.stack

Starts Postgres (engine), the backend (backend_app.py) and one worker, writes the connection details to
``E2E_STACK_INFO`` (json), then serves ``http://127.0.0.1:<control_port>``:
``POST /kill-worker`` (SIGKILL), ``POST /start-worker``, ``POST /open-gate`` / ``/close-gate`` (the blocked
write), ``GET /effects`` (lines the write has appended), ``GET /state``. For Deep Work: ``POST /dw-open-gate`` /
``/dw-close-gate`` (a ``web_search`` of a query containing GATED), ``GET /dw-state`` (fake search log, the
tools currently in flight on the backend, sandbox events). For Fleet: ``POST /fleet-seed?script=<recovery|steer|cancel>``
creates a Fleet job through the backend's ``/api/v1/background-jobs`` and answers ``{jobId, conversationId}``; the run
holds one write on the same gate as ``/open-gate`` / ``/close-gate`` (``script=probe`` only answers 200). Exits on SIGTERM/SIGINT or stdin EOF.
"""
import json
import os
import signal
import sqlite3
import sys
import threading
import urllib.parse
import uuid

import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.e2e import fleet_script
from tests.e2e.harness import AUTH, stack


def main() -> int:
    info_path = Path(os.environ["E2E_STACK_INFO"])
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    with stack() as w:
        state = {"worker": w.start_worker(1), "n": 1}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                path = self.path.split("?")[0]
                try:
                    if path == "/kill-worker":
                        state["worker"].kill(signal.SIGKILL)
                    elif path == "/start-worker":
                        if state["worker"].p.poll() is None:
                            return self._send(409, {"error": "worker already running"})
                        state["n"] += 1
                        state["worker"] = w.start_worker(state["n"])
                    elif path == "/open-gate":
                        w.gate.write_text("open")
                    elif path == "/close-gate":
                        w.gate.unlink(missing_ok=True)
                    elif path == "/dw-open-gate":
                        w.dw.gate.write_text("open")
                    elif path == "/dw-close-gate":
                        w.dw.gate.unlink(missing_ok=True)
                    elif path == "/dw-state":
                        con = sqlite3.connect(w.db, timeout=10)
                        try:
                            in_flight = [r[0] for r in con.execute(
                                "SELECT tool FROM tool_invocations WHERE status = 'in_flight'")]
                        finally:
                            con.close()
                        return self._send(200, {"searches": w.dw.lines(w.dw.search_log),
                                                "sandbox": w.dw.lines(w.dw.sandbox_log), "inFlight": in_flight})
                    elif path == "/fleet-seed":
                        name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("script", [""])[0]
                        if name == "probe":
                            return self._send(200, {"probe": True})
                        if name not in fleet_script.UI_SCENARIOS:
                            return self._send(400, {"error": f"unknown script {name!r}"})
                        r = httpx.post(f"{w.backend_url}/api/v1/background-jobs/", headers=AUTH, timeout=60, json={
                            "job_type": "background_agent", "agent_id": str(w.agent),
                            "input_data": {"task": fleet_script.ui_task(name)}})
                        if r.status_code != 201:
                            return self._send(500, {"error": r.text})
                        job_id = r.json()["id"]
                        con = sqlite3.connect(w.db, timeout=10)
                        try:
                            row = con.execute("SELECT conversation_id FROM background_jobs WHERE id = ?",
                                              (job_id.replace("-", ""),)).fetchone()
                        finally:
                            con.close()
                        return self._send(200, {"jobId": job_id,
                                                "conversationId": str(uuid.UUID(row[0])) if row and row[0] else None})
                    elif path == "/effects":
                        lines = w.effects.read_text().splitlines() if w.effects.exists() else []
                        return self._send(200, {"effects": lines})
                    elif path != "/state":
                        return self._send(404, {"error": "unknown"})
                    self._send(200, {"workerRunning": state["worker"].p.poll() is None,
                                     "gateOpen": w.gate.exists()})
                except Exception as e:  # noqa: BLE001 - report to the test
                    self._send(500, {"error": repr(e)})

            do_GET = do_POST = _handle

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # in-process: bind 0, read the port back
        threading.Thread(target=server.serve_forever, daemon=True).start()
        info_path.write_text(json.dumps({
            "backendUrl": w.backend_url, "controlUrl": f"http://127.0.0.1:{server.server_port}",
            "projectId": str(w.project)}))
        done.wait()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
