"""U11 / R18 / AE10: a real engine worker, a scripted model and the real backend routers, end to end.

Processes: a throwaway Postgres 14+ cluster (engine storage), the backend (``backend_app.py``: the
real /internal router + browser routes on file SQLite, uvicorn), and ``npm run worker:test`` in
``engine/`` (scripted model, backend policy and gateway). The test drives only the backend's
browser routes. Skipped unless ONIVA_E2E=1.

Scenario 1: create, submit, SSE to completion; the read tool ran once, usage was recorded and priced
from the scripted tokens, the answer is in the stream; replaying the invoke key does not run the tool
again. Scenario 2: the worker is SIGKILLed while a non-idempotent write is held in flight in the
backend; the write completes and its outcome is stored; the restarted worker adopts that outcome, the
run completes, and the effect happened exactly once.
"""
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[2]
ENGINE = BACKEND.parent / "engine"
GATEWAY_TOKEN = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
SERVICE_TOKEN = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET_KEY = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
SESSION = "e2e-session"
AUTH = {"Authorization": "Bearer e2e-user-jwt"}

READ_TOKENS = (777, 33)    # round 1 (tool call)
ANSWER_TOKENS = (1234, 56)  # round 2 (answer)
ANSWER = "KYC verified: low risk (e2e-answer-1)"
WRITE_ANSWER = "write finished (e2e-answer-2)"

SCRIPT = {
    "rules": [
        {"when": {"role": "user", "includes": "RUN_KYC"},
         "respond": {"steps": [{"type": "tool_call", "name": "kyc_verification",
                                "arguments": {"full_name": "Ada Lovelace", "dob": "1815-12-10", "country": "UK"}}],
                     "usage": {"inputTokens": READ_TOKENS[0], "outputTokens": READ_TOKENS[1]}}},
        {"when": {"role": "user", "includes": "RUN_WRITE"},
         "respond": {"steps": [{"type": "tool_call", "name": "e2e_write", "arguments": {"note": "once"}}],
                     "usage": {"inputTokens": 11, "outputTokens": 5}}},
        {"when": {"role": "tool_result", "tool": "kyc_verification"},
         "respond": {"steps": [{"type": "text", "text": ANSWER}],
                     "usage": {"inputTokens": ANSWER_TOKENS[0], "outputTokens": ANSWER_TOKENS[1]}}},
        {"when": {"role": "tool_result", "tool": "e2e_write"},
         "respond": {"steps": [{"type": "text", "text": WRITE_ANSWER}],
                     "usage": {"inputTokens": 13, "outputTokens": 7}}},
    ]
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, what: str, timeout: float = 60.0, every: float = 0.2):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            got = fn()
            if got:
                return got
        except Exception as e:  # noqa: BLE001 - polling
            last = e
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last error: {last!r})")


def find_bin(name: str) -> str:
    found = shutil.which(name)
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/15/bin",
              "/usr/lib/postgresql/14/bin", "/opt/homebrew/opt/postgresql@14/bin"):
        if found:
            break
        if (Path(d) / name).exists():
            found = str(Path(d) / name)
    if not found:
        pytest.skip(f"e2e needs {name} on PATH")
    return found


class Proc:
    """A child in its own process group, logging to a file, killed with its group."""

    def __init__(self, argv, env, cwd, log: Path):
        self.log = log
        self._fh = log.open("wb")
        self.p = subprocess.Popen(argv, env=env, cwd=cwd, stdout=self._fh, stderr=subprocess.STDOUT,
                                  start_new_session=True)

    def kill(self, sig=signal.SIGKILL):
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, sig)
            except ProcessLookupError:
                pass
            try:
                self.p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.p.pid, signal.SIGKILL)
                self.p.wait(timeout=5)
        self._fh.close()

    def tail(self, n=40) -> str:
        return "\n".join(self.log.read_text(errors="replace").splitlines()[-n:])


class World:
    pass


@pytest.fixture(scope="module")
def world():
    w = World()
    tmp = Path(tempfile.mkdtemp(prefix="oniva-e2e-"))
    procs: list[Proc] = []
    pg_data = tmp / "pg"
    pg_ctl = None
    pg_started = False
    import psycopg2
    from urllib.parse import urlsplit
    external = os.environ.get("ONIVA_E2E_PG_URL")  # CI service container; else a throwaway cluster
    pg, engine_db = {}, None
    try:
        if external:
            u = urlsplit(external)
            pg = dict(host=u.hostname, port=u.port or 5432, user=u.username, password=u.password)
            auth = u.username + (f":{u.password}" if u.password else "")
        else:
            pg_ctl, initdb = find_bin("pg_ctl"), find_bin("initdb")
            pg = dict(host="127.0.0.1", port=free_port(), user="postgres", password=None)
            auth = "postgres"
            subprocess.run([initdb, "-D", str(pg_data), "-U", "postgres", "-A", "trust"], check=True,
                           capture_output=True)
            subprocess.run([pg_ctl, "-D", str(pg_data), "-l", str(tmp / "pg.log"), "-w", "start", "-o",
                            f"-p {pg['port']} -c listen_addresses=127.0.0.1 -c unix_socket_directories= "
                            "-c fsync=off"], check=True, capture_output=True)
            pg_started = True
        engine_db = f"engine_e2e_{uuid.uuid4().hex[:8]}"
        conn = psycopg2.connect(dbname="postgres", **pg)
        conn.autocommit = True
        conn.cursor().execute(f"CREATE DATABASE {engine_db}")
        conn.close()
        pg_url = f"postgres://{auth}@{pg['host']}:{pg['port']}/{engine_db}"

        w.tmp = tmp
        w.backend_port, w.engine_port = free_port(), free_port()
        w.tenant, w.user, w.project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        w.db = tmp / "backend.db"
        w.effects, w.gate, w.script = tmp / "effects.txt", tmp / "gate", tmp / "script.json"
        w.script.write_text(json.dumps(SCRIPT))
        w.pg_url = pg_url
        w.engine_url = f"http://127.0.0.1:{w.engine_port}"
        w.backend_url = f"http://127.0.0.1:{w.backend_port}"

        base = {k: v for k, v in os.environ.items() if not k.startswith(("ENGINE_", "DATABASE_URL"))}
        info = tmp / "info.json"
        env = {**base, "PYTHONPATH": str(BACKEND), "E2E_PORT": str(w.backend_port), "E2E_DB": str(w.db),
               "E2E_INFO": str(info), "E2E_EFFECTS": str(w.effects), "E2E_GATE": str(w.gate),
               "E2E_TENANT_ID": str(w.tenant), "E2E_USER_ID": str(w.user), "E2E_PROJECT_ID": str(w.project),
               "E2E_SECRET_KEY": SECRET_KEY, "ENGINE_GATEWAY_TOKEN": GATEWAY_TOKEN,
               "ENGINE_SERVICE_TOKEN": SERVICE_TOKEN, "ENGINE_URL": w.engine_url, "ENGINE_SESSION_ID": SESSION}
        backend = Proc([sys.executable, "-m", "tests.e2e.backend_app"], env, BACKEND, tmp / "backend.log")
        procs.append(backend)
        w.backend = backend

        def backend_ready():
            assert backend.p.poll() is None, backend.tail()
            return info.exists() and httpx.get(f"{w.backend_url}/internal/ping", headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}"}).status_code == 200
        wait_for(backend_ready, "the backend", 90)

        w.worker_env = {**base, "ENGINE_PORT": str(w.engine_port), "ENGINE_HOST": "127.0.0.1",
                        "DATABASE_URL": w.pg_url, "ENGINE_SESSION_ID": SESSION,
                        "ENGINE_SERVICE_TOKEN": SERVICE_TOKEN, "ENGINE_GATEWAY_URL": w.backend_url,
                        "ENGINE_GATEWAY_TOKEN": GATEWAY_TOKEN, "ENGINE_POLICY": "backend",
                        "ENGINE_POLICY_URL": w.backend_url, "ENGINE_TEST_MODEL_SCRIPT": str(w.script),
                        "ENGINE_TEST_MODEL_PROVIDER": "anthropic", "ENGINE_LEASE_TTL_MS": "3000",
                        "ENGINE_PROGRESS_INTERVAL_MS": "50"}
        w.procs = procs
        w.start_worker = lambda n: _start_worker(w, n)
        yield w
    finally:
        for p in procs:
            p.kill()
        if external and engine_db:
            try:
                c = psycopg2.connect(dbname="postgres", **pg)
                c.autocommit = True
                c.cursor().execute(f"DROP DATABASE IF EXISTS {engine_db} WITH (FORCE)")
                c.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        if pg_started:
            subprocess.run([pg_ctl, "-D", str(pg_data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _start_worker(w: World, n: int) -> Proc:
    worker = Proc(["npm", "run", "--silent", "worker:test"], w.worker_env, ENGINE, w.tmp / f"worker{n}.log")
    w.procs.append(worker)

    def owner():
        assert worker.p.poll() is None, worker.tail()
        r = httpx.get(f"{w.engine_url}/v1/health", timeout=2)
        return r.status_code == 200 and r.json().get("owner") is True
    wait_for(owner, "the worker to own the session lease", 90)
    return worker


def sql(w: World, query: str, *args):
    con = sqlite3.connect(w.db, timeout=10)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


def api(w: World, method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{w.backend_url}/api/v1/engine/conversations{path}", headers=AUTH,
                         timeout=30, **kw)


def watch_until(w: World, conversation_id: str, needle: str, timeout: float = 60.0) -> str:
    """Read the backend's SSE relay until `needle` appears in the stream; return the text read."""
    text = ""
    deadline = time.monotonic() + timeout
    with httpx.stream("GET", f"{w.backend_url}/api/v1/engine/conversations/{conversation_id}/watch",
                      headers=AUTH, timeout=httpx.Timeout(timeout, read=timeout)) as r:
        assert r.status_code == 200, r.read()
        for chunk in r.iter_text():
            text += chunk
            if needle in text:
                return text
            assert time.monotonic() < deadline, f"no {needle!r} in stream; got {text[-800:]}"
    raise AssertionError(f"stream ended without {needle!r}: {text[-800:]}")


def new_conversation(w: World) -> str:
    r = api(w, "POST", "", json={"project_id": str(w.project), "agent": {"tools": ["kyc_verification", "e2e_write"]}})
    assert r.status_code == 201, f"{r.text}\nbackend:\n{w.backend.tail()}"
    return r.json()["conversationId"]


def test_engine_smoke(world):
    w = world
    worker = w.start_worker(1)

    # ---- scenario 1: tool call, usage, answer -------------------------------------------------
    conv = new_conversation(w)
    r = api(w, "POST", f"/{conv}/submissions", json={"text": "RUN_KYC please", "requestId": "req-kyc"})
    assert r.status_code == 202, r.text
    stream = watch_until(w, conv, ANSWER)
    assert "kyc_verification" in stream

    rows = sql(w, "SELECT idempotency_key, tool, status, outcome FROM tool_invocations WHERE tool = 'kyc_verification'")
    assert len(rows) == 1 and rows[0][2] == "completed", rows
    key = rows[0][0]

    def usage():
        got = sql(w, "SELECT input_tokens, output_tokens, cost_usd, provider_name FROM llm_usage_records "
                     "ORDER BY input_tokens")
        return got if len(got) >= 2 else None
    ledger = wait_for(usage, "the usage ledger rows", 30)
    assert (READ_TOKENS[0], READ_TOKENS[1]) in [(a, b) for a, b, *_ in ledger]
    assert (ANSWER_TOKENS[0], ANSWER_TOKENS[1]) in [(a, b) for a, b, *_ in ledger]
    assert all(float(c) > 0 for _, _, c, _ in ledger), ledger  # priced from the table, not zero

    # Replaying the same invoke key returns the stored outcome without running the tool again.
    run_id, call_id = key.split(":", 1)
    body = {"args": {"full_name": "Ada Lovelace", "dob": "1815-12-10", "country": "UK"},
            "runId": run_id, "toolCallId": call_id, "tenantId": str(w.tenant), "projectId": str(w.project),
            "conversationId": conv}
    again = httpx.post(f"{w.backend_url}/internal/tools/kyc_verification/invoke", json=body, timeout=30,
                       headers={"Authorization": f"Bearer {GATEWAY_TOKEN}", "Idempotency-Key": key,
                                "X-Engine-Session": SESSION})
    assert again.status_code == 200, again.text
    assert len(sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'kyc_verification'")) == 1

    # ---- scenario 2: SIGKILL during a blocked write, restart, no duplicate ---------------------
    conv2 = new_conversation(w)
    assert api(w, "POST", f"/{conv2}/submissions", json={"text": "RUN_WRITE now", "requestId": "req-write"}
               ).status_code == 202
    wait_for(lambda: sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status = 'in_flight'"),
             "the write to be in flight in the backend", 60)
    assert not w.effects.exists()  # blocked on the gate: nothing happened yet

    worker.kill(signal.SIGKILL)
    w.gate.write_text("open")  # the backend's effect, detached from the dead caller, now completes
    wait_for(lambda: w.effects.exists() and sql(
        w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status != 'in_flight'"),
        "the in-flight write to finish and store its outcome", 30)

    w.start_worker(2)
    watch_until(w, conv2, WRITE_ANSWER, 90)

    assert w.effects.read_text().splitlines() == ["effect once"], "the write must happen exactly once"
    inv = sql(w, "SELECT idempotency_key FROM tool_invocations WHERE tool = 'e2e_write'")
    assert len(inv) == 1, inv
    # Conversations stay bound to their user/tenant through the proxy.
    assert sql(w, "SELECT count(*) FROM engine_conversations")[0][0] == 2
