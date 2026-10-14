"""Alembic against an empty Postgres as the owner role.

KNOWN BROKEN (see DRIFT.md): ``alembic upgrade head`` does not complete on an empty database -
the chain was never run from scratch and only works against databases built by
``Base.metadata.create_all``. The upgrade/downgrade tests are strict xfails so that fixing the
chain forces removing the marker (KTD6).
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.postgres.conftest import BACKEND_DIR, run_alembic

DRIFT = "migration chain does not run from an empty DB (DRIFT.md)"


def test_single_alembic_head():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, heads


@pytest.mark.xfail(strict=True, reason=DRIFT)
def test_upgrade_head_on_empty_db(pg_migrated_url):
    res = run_alembic(pg_migrated_url, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-2000:]
    engine = create_engine(pg_migrated_url)
    try:
        with engine.connect() as c:
            assert c.execute(text("SELECT to_regclass('public.sandbox_executions')")).scalar()
            assert c.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar()
    finally:
        engine.dispose()


@pytest.mark.xfail(strict=True, reason=DRIFT)
def test_downgrade_then_upgrade_last_revision(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    down = run_alembic(pg_migrated_url, "downgrade", "-1")
    assert down.returncode == 0, down.stderr[-2000:]
    up = run_alembic(pg_migrated_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
