"""check_and_create_background_job on real Postgres.

Before the fix the count query used ``SELECT count(*) ... FOR UPDATE``, which Postgres rejects
("FOR UPDATE is not allowed with aggregate functions"); SQLite ignores FOR UPDATE.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.background_dispatch import (
    ConcurrentLimitExceeded,
    check_and_create_background_job,
)


@pytest.fixture
def seeded(pg_subset_sync):
    from sqlalchemy.orm import Session

    from app.models.custom_agent import CustomAgent
    from app.models.tenant import Tenant
    from app.models.user import User

    with Session(pg_subset_sync) as s:
        tenant = Tenant(name="t", slug=f"t-{uuid.uuid4().hex[:8]}")
        s.add(tenant)
        s.flush()
        user = User(tenant_id=tenant.id, email=f"{uuid.uuid4().hex[:8]}@example.com", role="member")
        s.add(user)
        s.flush()
        agent = CustomAgent(user_id=user.id, tenant_id=tenant.id, name="a")
        s.add(agent)
        s.commit()
        return tenant.id, user.id, agent.id


async def _call(url, tenant, user, agent, max_concurrent):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            try:
                job = await check_and_create_background_job(
                    db, tenant, agent, user, max_concurrent, "manual", {"type": "test"})
                await db.commit()
                return job.id
            except ConcurrentLimitExceeded:
                await db.rollback()
                return None
    finally:
        await engine.dispose()


async def test_creates_job_then_enforces_limit(pg_subset_url, seeded):
    tenant, user, agent = seeded
    first = await _call(pg_subset_url, tenant, user, agent, max_concurrent=1)
    assert first is not None
    assert await _call(pg_subset_url, tenant, user, agent, max_concurrent=1) is None


async def test_concurrent_dispatch_respects_limit(pg_subset_url, seeded):
    tenant, user, agent = seeded
    results = await asyncio.gather(
        *[_call(pg_subset_url, tenant, user, agent, max_concurrent=2) for _ in range(8)])
    assert sum(r is not None for r in results) == 2
