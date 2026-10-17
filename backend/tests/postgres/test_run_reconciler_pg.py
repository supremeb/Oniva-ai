"""Plan 003 U5: two reconcilers on Postgres - the second takes no advisory lock and does nothing."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services import run_handlers as rh
from app.services import run_reconciler as rr


@pytest.fixture
def pg_factory(pg_server):
    pg_server.recreate_db("oniva_reconciler")
    engine = create_async_engine(pg_server.url("oniva_reconciler", driver="postgresql+asyncpg"))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as s:
            yield s

    yield factory


async def test_second_reconciler_takes_no_lock_while_the_first_is_mid_pass(pg_factory):
    settings = SimpleNamespace(RUN_RECONCILE_AFTER_S=1, RUN_RECONCILE_INTERVAL_S=60.0,
                               RUN_START_RETRY_MAX=5, RUN_START_RETRY_BASE_S=1.0)
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def find_stuck(session, older_than):
        seen.append(1)
        entered.set()
        await release.wait()
        return []

    rh.reset()
    rh.register("fleet", find_stuck=find_stuck, on_finished=lambda *a: None)
    first = rr.RunReconciler(pg_factory, settings)
    second = rr.RunReconciler(pg_factory, settings)
    try:
        task = asyncio.create_task(first.run_once())
        await asyncio.wait_for(entered.wait(), 10)
        assert await second.run_once() is None  # lock held: it did nothing
        assert seen == [1]
        release.set()
        assert await task is not None
        # Lock released: the next pass runs.
        assert await second.run_once() is not None
    finally:
        release.set()
        rh.reset()
