"""Postgres test tier fixtures (KTD2).

Sources of Postgres, in order:
  1. ``TEST_DATABASE_URL`` - an owner/superuser URL to an existing server (CI uses a
     ``pgvector/pgvector:pg16`` service). Test databases are created next to it.
  2. A throwaway local cluster booted with ``initdb``/``pg_ctl`` (Homebrew postgresql@14/15
     or anything on PATH).

Two layers, so a missing pgvector does not hide everything:
  * ``pg_subset_url``  - plain Postgres; only the tables a test needs (FK closure) are built
    from ``Base.metadata``. Used by the SKIP LOCKED / FOR UPDATE tests.
  * ``pg_migrated_url`` - ``alembic upgrade head`` on an empty DB as the owner role. Needs the
    ``vector`` extension; skipped locally with a clear reason when it is unavailable (CI always
    has it).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[2]

_BIN_CANDIDATES = [
    "/opt/homebrew/opt/postgresql@14/bin",
    "/opt/homebrew/opt/postgresql@15/bin",
    "/opt/homebrew/opt/postgresql@16/bin",
    "/opt/homebrew/opt/postgresql@17/bin",
    "/usr/local/opt/postgresql@14/bin",
    "/usr/lib/postgresql/16/bin",
    "/usr/lib/postgresql/15/bin",
    "/usr/lib/postgresql/14/bin",
]


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/postgres" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.postgres)


def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in _BIN_CANDIDATES:
        p = Path(d) / name
        if p.exists():
            return str(p)
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PgServer:
    """Connection info for an owner-capable Postgres server."""

    def __init__(self, host: str, port: int, user: str, password: str | None):
        self.host, self.port, self.user, self.password = host, port, user, password

    def _connect(self, dbname: str):
        conn = psycopg2.connect(
            host=self.host, port=self.port, user=self.user,
            password=self.password, dbname=dbname,
        )
        conn.autocommit = True
        return conn

    def url(self, dbname: str, driver: str = "postgresql") -> str:
        auth = self.user + (f":{self.password}" if self.password else "")
        return f"{driver}://{auth}@{self.host}:{self.port}/{dbname}"

    def recreate_db(self, dbname: str) -> None:
        conn = self._connect("postgres")
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (dbname,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        finally:
            conn.close()

    def vector_available(self) -> bool:
        conn = self._connect("postgres")
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            return cur.fetchone() is not None
        finally:
            conn.close()


@pytest.fixture(scope="session")
def pg_server():
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        parts = urlsplit(override)
        yield PgServer(parts.hostname or "127.0.0.1", parts.port or 5432,
                       parts.username or "postgres", parts.password)
        return

    initdb, pg_ctl = _find_bin("initdb"), _find_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("Postgres tier: no initdb/pg_ctl found and TEST_DATABASE_URL is not set")

    data_dir = tempfile.mkdtemp(prefix="oniva-pg-")
    port = _free_port()
    log = os.path.join(data_dir, "server.log")
    subprocess.run([initdb, "-D", os.path.join(data_dir, "data"), "-U", "postgres", "-A", "trust"],
                   check=True, capture_output=True)
    # unix_socket_directories= (empty): TCP only, avoids the 103-byte socket path limit.
    subprocess.run(
        [pg_ctl, "-D", os.path.join(data_dir, "data"), "-l", log, "-w", "start",
         "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories= "
               f"-c fsync=off -c max_connections=60"],
        check=True, capture_output=True,
    )
    try:
        yield PgServer("127.0.0.1", port, "postgres", None)
    finally:
        subprocess.run([pg_ctl, "-D", os.path.join(data_dir, "data"), "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def pg_has_vector(pg_server) -> bool:
    return pg_server.vector_available()


@pytest.fixture(scope="session")
def pg_subset_url(pg_server) -> str:
    """Async URL of a DB holding only the tables the claim/dispatch tests need.

    Built from ``Base.metadata`` (FK closure of the target tables), not Alembic, so it runs on
    plain Postgres without pgvector.
    """
    from app.models import Base

    wanted = {"background_jobs", "scheduled_workflows", "scheduled_workflow_executions"}
    by_name = Base.metadata.tables
    closure: set[str] = set()
    stack = list(wanted)
    while stack:
        name = stack.pop()
        if name in closure:
            continue
        closure.add(name)
        for fk in by_name[name].foreign_keys:
            stack.append(fk.column.table.name)
    tables = [t for t in Base.metadata.sorted_tables if t.name in closure]

    pg_server.recreate_db("oniva_subset")
    engine = create_engine(pg_server.url("oniva_subset"))
    try:
        Base.metadata.create_all(engine, tables=tables)
    finally:
        engine.dispose()
    return pg_server.url("oniva_subset", driver="postgresql+asyncpg")


@pytest.fixture
def pg_subset_sync(pg_subset_url):
    """Sync engine on the subset DB with every table emptied before the test."""
    engine = create_engine(pg_subset_url.replace("+asyncpg", ""))
    with engine.begin() as conn:
        names = conn.execute(text(
            "SELECT string_agg(quote_ident(tablename), ',') FROM pg_tables WHERE schemaname='public'"
        )).scalar()
        if names:
            conn.execute(text(f"TRUNCATE {names} CASCADE"))
    yield engine
    engine.dispose()


def run_alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url.replace("postgresql://", "postgresql+asyncpg://", 1)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )


@pytest.fixture
def pg_migrated_url(pg_server, pg_has_vector):
    """Sync owner URL of an empty DB; ``alembic upgrade head`` is run by the test itself."""
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres "
                    "(brew pgvector ships only for postgresql@17/18); CI runs this tier fully")
    pg_server.recreate_db("oniva")
    return pg_server.url("oniva")
