"""Concurrent scheduler claims under FOR UPDATE SKIP LOCKED never double-claim.

The claim SELECT mirrors ``WorkflowScheduler._poll_once`` (app/services/workflow_scheduler.py);
keep the two in step.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

CLAIM_SQL = text("""
    SELECT id FROM scheduled_workflows
    WHERE is_enabled = true AND status = 'ACTIVE' AND next_run_at <= NOW()
    ORDER BY next_run_at ASC
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
""")
N_SCHEDULES = 24


@pytest.fixture
def due_schedules(pg_subset_sync):
    from sqlalchemy.orm import Session

    from app.models.scheduled_workflow import ScheduledWorkflow, ScheduleStatus, ScheduleType
    from app.models.tenant import Tenant
    from app.models.user import User

    with Session(pg_subset_sync) as s:
        tenant = Tenant(name="t", slug=f"t-{uuid.uuid4().hex[:8]}")
        s.add(tenant)
        s.flush()
        user = User(tenant_id=tenant.id, email=f"{uuid.uuid4().hex[:8]}@example.com", role="member")
        s.add(user)
        s.flush()
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        ids = []
        for i in range(N_SCHEDULES):
            sw = ScheduledWorkflow(
                tenant_id=tenant.id, user_id=user.id, name=f"s{i}",
                schedule_type=ScheduleType.INTERVAL, interval_seconds=60,
                status=ScheduleStatus.ACTIVE, is_enabled=True,
                next_run_at=past + timedelta(seconds=i),
            )
            s.add(sw)
            s.flush()
            ids.append(sw.id)
        s.commit()
    return set(ids)


async def _claimer(url: str, batch: int, hold: float) -> list[uuid.UUID]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            rows = (await db.execute(CLAIM_SQL, {"batch_size": batch})).fetchall()
            claimed = [r[0] for r in rows]
            await asyncio.sleep(hold)  # keep row locks while the others poll
            if claimed:
                await db.execute(
                    text("UPDATE scheduled_workflows SET next_run_at = NOW() + interval '1 hour' "
                         "WHERE id = ANY(:ids)"),
                    {"ids": claimed},
                )
            await db.commit()
            return claimed
    finally:
        await engine.dispose()


async def test_concurrent_claims_never_overlap(pg_subset_url, due_schedules):
    results = await asyncio.gather(*[_claimer(pg_subset_url, batch=8, hold=0.4) for _ in range(4)])
    flat = [i for r in results for i in r]
    assert len(flat) == len(set(flat)), "a schedule was claimed by two pollers"
    assert set(flat) <= due_schedules
    # Pollers overlapped (each held its locks), so the work really was split between them.
    assert sum(1 for r in results if r) >= 2
    # Nothing left due in the next poll except what nobody got to (batch limit 4*8 > 24 => none).
    assert len(flat) == N_SCHEDULES


async def test_skip_locked_skips_rather_than_waits(pg_subset_url, due_schedules):
    engine = create_async_engine(pg_subset_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as holder:
            held = (await holder.execute(CLAIM_SQL, {"batch_size": N_SCHEDULES})).fetchall()
            assert len(held) == N_SCHEDULES
            # Another session sees nothing (and does not block) while the first holds the locks.
            second = await asyncio.wait_for(_claimer(pg_subset_url, batch=N_SCHEDULES, hold=0), 5)
            assert second == []
            await holder.rollback()
    finally:
        await engine.dispose()
