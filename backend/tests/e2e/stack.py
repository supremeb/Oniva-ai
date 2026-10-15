"""Boot the e2e stack for the browser test and serve a tiny control API (test-only).

    E2E_CORS_ORIGIN=http://localhost:3100 python -m tests.e2e.stack

Starts Postgres (engine), the backend (backend_app.py) and one worker, writes the connection details to
``E2E_STACK_INFO`` (json), then serves ``http://127.0.0.1:<control_port>``:
``POST /kill-worker`` (SIGKILL), ``POST /start-worker``, ``POST /open-gate`` / ``/close-gate`` (the blocked
write), ``GET /effects`` (lines the write has appended), ``GET /state``. Exits on SIGTERM/SIGINT or stdin EOF.
"""
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.e2e.harness import free_port, stack


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

        server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        info_path.write_text(json.dumps({
            "backendUrl": w.backend_url, "controlUrl": f"http://127.0.0.1:{server.server_port}",
            "projectId": str(w.project)}))
        done.wait()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
