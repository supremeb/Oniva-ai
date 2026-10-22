"""Shared e2e harness: throwaway Postgres for the engine, the backend (backend_app.py), a scripted-model worker."""
import contextlib
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

from tests.e2e.dw_fakes import Files

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
STEER_ANSWER = "steer received (e2e-steer)"
PRE_TEXT = "Saving the note now (e2e-pre-text)"

SCRIPT = {
    "rules": [
        {"when": {"role": "user", "includes": "RUN_KYC"},
         "respond": {"steps": [{"type": "tool_call", "name": "kyc_verification",
                                "arguments": {"full_name": "Ada Lovelace", "dob": "1815-12-10", "country": "UK"}}],
                     "usage": {"inputTokens": READ_TOKENS[0], "outputTokens": READ_TOKENS[1]}}},
        {"when": {"role": "user", "includes": "RUN_WRITE"},
         "respond": {"steps": [{"type": "tool_call", "name": "e2e_write", "arguments": {"note": "once"}}],
                     "usage": {"inputTokens": 11, "outputTokens": 5}}},
        {"when": {"role": "user", "includes": "RUN_CHAT_WRITE"},
         "respond": {"steps": [{"type": "text", "text": PRE_TEXT},
                               {"type": "tool_call", "name": "e2e_write", "arguments": {"note": "chat"}}],
                     "usage": {"inputTokens": 21, "outputTokens": 9}}},
        {"when": {"role": "user", "includes": "STEER_NOW"},
         "respond": {"steps": [{"type": "text", "text": STEER_ANSWER}], "usage": {"inputTokens": 17, "outputTokens": 3}}},
        {"when": {"role": "tool_result", "tool": "kyc_verification"},
         "respond": {"steps": [{"type": "text", "text": ANSWER}],
                     "usage": {"inputTokens": ANSWER_TOKENS[0], "outputTokens": ANSWER_TOKENS[1]}}},
        {"when": {"role": "tool_result", "tool": "e2e_write"},
         "respond": {"steps": [{"type": "text", "text": WRITE_ANSWER}],
                     "usage": {"inputTokens": 13, "outputTokens": 7}}},
    ]
}


def _with_deep_work_rules(script: dict) -> dict:
    from tests.e2e import approval_script, dw_script, fleet_script, workflow_script

    # The approval rules refine tool_result rules of the generic chat ones (e2e_write), so they come first.
    return {**script, "rules": [*approval_script.build()["rules"], *script["rules"],
                                *fleet_script.build()["rules"], *dw_script.build()["rules"],
                                *workflow_script.build()["rules"]]}


_FLOOR = 20000
_handed_out: set[int] = set()


def ephemeral_range() -> tuple[int, int]:
    """The OS range outgoing connections take their local ports from (Linux, macOS; IANA default otherwise)."""
    with contextlib.suppress(OSError, ValueError):
        low, high = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        return int(low), int(high)
    with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
        out = subprocess.run(["sysctl", "-n", "net.inet.ip.portrange.first", "net.inet.ip.portrange.last"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.split()
        return int(out[0]), int(out[1])
    return 49152, 65535


def _candidate(low: int) -> int:
    return _FLOOR + int.from_bytes(os.urandom(2), "big") % max(1, low - _FLOOR)


def free_port() -> int:
    """A bindable port below the ephemeral range, never handed out twice in one run.

    Binding to 0 would return an ephemeral port that is free only until the child binds it: an outgoing
    connection opened meanwhile (the backend's, say) can take that very port, and the engine then fails
    with EADDRINUSE. Ports below the range are never used for outgoing connections.
    """
    low, _ = ephemeral_range()
    for _ in range(200):
        port = _candidate(low)
        if port in _handed_out:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        _handed_out.add(port)
        return port
    raise RuntimeError(f"no free port between {_FLOOR} and {low}")


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


def backend_mode() -> tuple[bool, int]:
    """(postgres?, workers) from the opt-in env: ``ONIVA_E2E_BACKEND_DB=postgres``, ``ONIVA_E2E_BACKEND_WORKERS=N``.

    Default (unset) is the original stack: one backend process on a SQLite file. The Postgres mode runs the backend
    the way production does: alembic-migrated schema, the app connecting as the non-bypass ``oniva_app`` role (system
    sessions as ``oniva_system``), N worker processes on one port. Workers apply to the Postgres mode only.
    """
    kind = os.environ.get("ONIVA_E2E_BACKEND_DB", "sqlite").strip().lower() or "sqlite"
    if kind not in ("sqlite", "postgres"):
        raise ValueError(f"ONIVA_E2E_BACKEND_DB must be sqlite or postgres, got {kind!r}")
    workers = int(os.environ.get("ONIVA_E2E_BACKEND_WORKERS") or 1)
    if workers < 1:
        raise ValueError("ONIVA_E2E_BACKEND_WORKERS must be >= 1")
    if workers > 1 and kind != "postgres":
        raise ValueError("ONIVA_E2E_BACKEND_WORKERS > 1 needs ONIVA_E2E_BACKEND_DB=postgres (SQLite serves one process)")
    return kind == "postgres", workers


def _migrate_backend_db(pg: dict, auth: str, backend_db: str) -> dict:
    """Create the backend database, run ``alembic upgrade head`` as the schema owner, give the app roles passwords.

    Returns the three URLs the backend needs. The roles ``oniva_app`` / ``oniva_system`` are cluster-wide and get
    a fresh random password, so point this at a throwaway or CI cluster, never at a shared development one.
    """
    import psycopg2
    conn = psycopg2.connect(dbname="postgres", **pg)
    conn.autocommit = True
    conn.cursor().execute(f"CREATE DATABASE {backend_db}")
    conn.close()
    hostport = f"{pg['host']}:{pg['port']}"
    owner = f"postgresql+asyncpg://{auth}@{hostport}/{backend_db}"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ENGINE_", "DATABASE_URL"))}
    env.update(PYTHONPATH=str(BACKEND), ONIVA_ENV_FILE="", DATABASE_URL=owner, MIGRATION_DATABASE_URL=owner,
               SECRET_KEY=SECRET_KEY)
    done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=BACKEND,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed (the Postgres needs the pgvector extension):\n"
                           f"{done.stdout[-1500:]}\n{done.stderr[-3000:]}")
    app_pw, system_pw = uuid.uuid4().hex, uuid.uuid4().hex
    conn = psycopg2.connect(dbname=backend_db, **pg)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"ALTER ROLE oniva_app PASSWORD '{app_pw}'")
    cur.execute(f"ALTER ROLE oniva_system PASSWORD '{system_pw}'")
    conn.close()
    return {"owner": owner, "app": f"postgresql+asyncpg://oniva_app:{app_pw}@{hostport}/{backend_db}",
            "system": f"postgresql+asyncpg://oniva_system:{system_pw}@{hostport}/{backend_db}",
            "owner_psycopg": f"postgresql://{auth}@{hostport}/{backend_db}"}


def _info_ready(info: Path, workers: int) -> bool:
    """The backend announced itself: ``info`` exists, and (Postgres mode) every worker wrote ``info.<pid>``."""
    return info.exists() and (workers == 0 or len(list(info.parent.glob(info.name + ".*"))) >= workers)


@contextlib.contextmanager
def stack(extra_env: dict | None = None, worker_command: list | None = None, worker_env: dict | None = None):
    """Boot the Postgres + backend + (no worker yet) stack; yield a World; tear everything down.

    Additive knobs for the eval harness (defaults leave every e2e test unchanged): ``extra_env`` is merged into the
    backend's environment, ``worker_command`` replaces ``npm run worker:test`` (the production worker, in live mode),
    ``worker_env`` is merged into the worker's environment.
    """
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
        w.backend_port, w.engine_port = int(os.environ.get("E2E_BACKEND_PORT") or free_port()), free_port()
        w.tenant, w.user, w.project, w.agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        # Approvals e2e identities (see backend_app.py): all in w.tenant except the stranger.
        w.admin, w.viewer, w.outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        w.other_tenant, w.stranger, w.other_project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        w.db = tmp / "backend.db"
        w.effects, w.gate, w.script = tmp / "effects.txt", tmp / "gate", tmp / "script.json"
        w.script.write_text(json.dumps(_with_deep_work_rules(SCRIPT)))
        w.dw_dir = tmp / "dw"
        (w.dw_dir / "sessions").mkdir(parents=True)
        w.dw = Files(w.dw_dir)
        w.pg_url = pg_url
        w.backend_pg, w.backend_workers = backend_mode()
        w.backend_db_name = f"backend_e2e_{uuid.uuid4().hex[:8]}"
        pg_env: dict = {}
        if w.backend_pg:
            urls = _migrate_backend_db(pg, auth, w.backend_db_name)
            w.backend_owner_dsn = urls["owner_psycopg"]
            pg_env = {"E2E_BACKEND_DB": "postgres", "E2E_WORKERS": str(w.backend_workers),
                      "E2E_PG_OWNER_URL": urls["owner"], "DATABASE_URL": urls["app"],
                      "SYSTEM_DATABASE_URL": urls["system"], "MIGRATION_DATABASE_URL": urls["owner"]}
        w.engine_url = f"http://127.0.0.1:{w.engine_port}"
        w.backend_url = f"http://127.0.0.1:{w.backend_port}"

        base = {k: v for k, v in os.environ.items() if not k.startswith(("ENGINE_", "DATABASE_URL"))}
        info = tmp / "info.json"
        env = {**base, "PYTHONPATH": str(BACKEND), "E2E_PORT": str(w.backend_port), "E2E_DB": str(w.db),
               "E2E_INFO": str(info), "E2E_EFFECTS": str(w.effects), "E2E_GATE": str(w.gate),
               "E2E_TENANT_ID": str(w.tenant), "E2E_USER_ID": str(w.user), "E2E_PROJECT_ID": str(w.project),
               "E2E_SECRET_KEY": SECRET_KEY, "E2E_DW_DIR": str(w.dw_dir), "E2E_AGENT_ID": str(w.agent),
               "E2E_ADMIN_ID": str(w.admin), "E2E_VIEWER_ID": str(w.viewer), "E2E_OUTSIDER_ID": str(w.outsider),
               "E2E_OTHER_TENANT_ID": str(w.other_tenant), "E2E_STRANGER_ID": str(w.stranger),
               "E2E_OTHER_PROJECT_ID": str(w.other_project), "E2E_UNATTENDED": str(tmp / "unattended.txt"),
               # Fleet on the engine: the tenant opt-in is seeded; this is the global condition.
               "ENABLE_ENGINE_FLEET": "true",
               # Workflows on the engine; the HTTP node may post to this backend's counting target.
               "ENABLE_ENGINE_WORKFLOWS": "true", "HTTP_NODE_ALLOWED_URLS": f"http://127.0.0.1:{w.backend_port}/e2e/",
               # Deep Work on the engine: tenant opt-in is seeded; these are the global conditions.
               "ENABLE_ENGINE_API": "true", "ENABLE_ENGINE_DEEP_WORK": "true", "ENABLE_DEEP_WORK": "true",
               "ENABLE_SANDBOX": "true", "E2B_API_KEY": "e2b-FAKE-e2e", "SANDBOX_PROXY_URL": "http://proxy.invalid",
               "TAVILY_API_KEY": "tvly-FAKE-e2e", "SESSION_BASE_PATH": str(w.dw_dir / "sessions"), "ENGINE_GATEWAY_TOKEN": GATEWAY_TOKEN,
               "ENGINE_SERVICE_TOKEN": SERVICE_TOKEN, "ENGINE_URL": w.engine_url, "ENGINE_SESSION_ID": SESSION,
               **pg_env, **(extra_env or {})}
        backend = Proc([sys.executable, "-m", "tests.e2e.backend_app"], env, BACKEND, tmp / "backend.log")
        procs.append(backend)
        w.backend = backend

        def backend_ready():
            assert backend.p.poll() is None, backend.tail()
            return _info_ready(info, w.backend_workers if w.backend_pg else 0) and httpx.get(
                f"{w.backend_url}/internal/ping", headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"}).status_code == 200
        wait_for(backend_ready, "the backend", 120 if w.backend_pg else 90)

        w.backend_env = env

        def start_second_backend() -> Proc:
            """A second backend process on the same database and engine: no state shared except the stores."""
            port, info2 = free_port(), tmp / "info2.json"
            second = Proc([sys.executable, "-m", "tests.e2e.backend_app"],
                          {**env, "E2E_PORT": str(port), "E2E_INFO": str(info2), "E2E_SEED": "0", "E2E_WORKERS": "1"},
                          BACKEND, tmp / "backend2.log")
            procs.append(second)
            second.url = f"http://127.0.0.1:{port}"

            def ready():
                assert second.p.poll() is None, second.tail()
                return _info_ready(info2, 1 if w.backend_pg else 0) and httpx.get(f"{second.url}/internal/ping", headers={
                    "Authorization": f"Bearer {GATEWAY_TOKEN}"}).status_code == 200
            wait_for(ready, "the second backend", 90)
            return second

        w.start_second_backend = start_second_backend
        w.worker_n = 0
        w.worker_env = {**base, "ENGINE_PORT": str(w.engine_port), "ENGINE_HOST": "127.0.0.1",
                        "DATABASE_URL": w.pg_url, "ENGINE_SESSION_ID": SESSION,
                        "ENGINE_SERVICE_TOKEN": SERVICE_TOKEN, "ENGINE_GATEWAY_URL": w.backend_url,
                        "ENGINE_GATEWAY_TOKEN": GATEWAY_TOKEN, "ENGINE_POLICY": "backend",
                        "ENGINE_POLICY_URL": w.backend_url, "ENGINE_TEST_MODEL_SCRIPT": str(w.script),
                        "ENGINE_TEST_MODEL_PROVIDER": "anthropic", "ENGINE_LEASE_TTL_MS": "3000",
                        "ENGINE_PROGRESS_INTERVAL_MS": "50",
                        "ENGINE_SUBAGENT_TOOLS": "research_worker,reflector,output_agent", **(worker_env or {})}
        w.worker_command = worker_command or ["npm", "run", "--silent", "worker:test"]
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
        if external and getattr(w, "backend_pg", False):
            try:
                c = psycopg2.connect(dbname="postgres", **pg)
                c.autocommit = True
                c.cursor().execute(f"DROP DATABASE IF EXISTS {w.backend_db_name} WITH (FORCE)")
                c.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        if pg_started:
            subprocess.run([pg_ctl, "-D", str(pg_data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def world():
    with stack() as w:
        yield w


def _start_worker(w: World, n: int) -> Proc:
    w.worker_n = n
    worker = Proc(w.worker_command, w.worker_env, ENGINE, w.tmp / f"worker{n}.log")
    w.procs.append(worker)

    def owner():
        assert worker.p.poll() is None, worker.tail()
        r = httpx.get(f"{w.engine_url}/v1/health", timeout=2)
        return r.status_code == 200 and r.json().get("owner") is True
    wait_for(owner, "the worker to own the session lease", 90)
    return worker


def restart_backend(w: World) -> Proc:
    """Kill the backend (SIGKILL) and start a new process on the same port, database and engine."""
    w.backend.kill(signal.SIGKILL)
    info = w.tmp / f"info-restart-{len(w.procs)}.json"
    env = {**w.backend_env, "E2E_SEED": "0", "E2E_INFO": str(info)}
    backend = Proc([sys.executable, "-m", "tests.e2e.backend_app"], env, BACKEND, w.tmp / f"backend-restart-{len(w.procs)}.log")
    w.procs.append(backend)
    w.backend = backend

    def ready():
        assert backend.p.poll() is None, backend.tail()
        return _info_ready(info, w.backend_workers if w.backend_pg else 0) and httpx.get(
            f"{w.backend_url}/internal/ping", headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"}).status_code == 200
    wait_for(ready, "the restarted backend", 120 if w.backend_pg else 90)
    return backend


def _pg_query(w: World, query: str, args, fetch: bool):
    import re
    import psycopg2
    hexid = re.compile(r"[0-9a-f]{32}")
    dashed = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    # The tests were written against SQLite, which stores uuids as 32 hex chars: speak that to the callers.
    args = tuple(str(uuid.UUID(a)) if isinstance(a, str) and hexid.fullmatch(a) else a for a in args)
    con = psycopg2.connect(w.backend_owner_dsn)  # the schema owner: reads past RLS, like the SQLite file did
    try:
        with con, con.cursor() as cur:
            cur.execute(query.replace("?", "%s"), args)
            if not fetch:
                return cur.rowcount
            return [tuple(v.replace("-", "") if isinstance(v, str) and dashed.fullmatch(v) else v for v in row)
                    for row in cur.fetchall()]
    finally:
        con.close()


def sql_write(w: World, query: str, *args) -> int:
    if getattr(w, "backend_pg", False):
        return _pg_query(w, query, args, False)
    con = sqlite3.connect(w.db, timeout=30)
    try:
        cur = con.execute(query, args)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def mark_unattended(w: World, conversation_id: str) -> None:
    """Treat a conversation as one no human owns (an unattended run): the backend's authorize sees no approver."""
    with (w.tmp / "unattended.txt").open("a") as f:
        f.write(conversation_id + "\n")


def as_user(name: str) -> dict:
    """Headers that make the e2e backend authenticate as one of its seeded users (default: the member)."""
    return {"X-E2E-User": name}


def sql(w: World, query: str, *args):
    if getattr(w, "backend_pg", False):
        return _pg_query(w, query, args, True)
    con = sqlite3.connect(w.db, timeout=10)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


def api(w: World, method: str, path: str, **kw) -> httpx.Response:
    headers = {**AUTH, **kw.pop("headers", {})}
    return httpx.request(method, f"{w.backend_url}/api/v1/engine/conversations{path}", headers=headers,
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
    r = api(w, "POST", "", json={"project_id": str(w.project), "selections": {"skill_ids": ["kyc_verification", "e2e_write"]}})
    assert r.status_code == 201, f"{r.text}\nbackend:\n{w.backend.tail()}"
    return r.json()["conversationId"]
