"""Rejoin by chat id picks the active run, else the most recently created one (not the oldest)."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.deep_work_run import DeepWorkRun
from app.services import deep_work_state as dws
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _seed(factory, tenant, chat, specs):
    """specs: (status, age_hours); returns the run ids in spec order."""
    ids = []
    async with factory() as db:
        for status, hours in specs:
            rid = uuid.uuid4()
            ids.append(rid)
            db.add(DeepWorkRun(
                id=rid, tenant_id=tenant, project_id=uuid.uuid4(), user_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
                query="q", status=status, phase="clarify", round=0, rounds=[], reflections=[], artifacts=[],
                is_engine=True, chat_id=chat, created_at=T0 + timedelta(hours=hours), updated_at=T0 + timedelta(hours=hours),
            ))
    return ids


async def test_newest_terminal_run_wins_regardless_of_insert_order(factory):
    tenant, chat = uuid.uuid4(), uuid.uuid4()
    oldest, newest, middle = await _seed(factory, tenant, chat, [("failed", 1), ("failed", 3), ("failed", 2)])
    got = await dws.load_run_by_chat(factory, tenant, chat)
    assert got.id == newest and got.id not in (oldest, middle)


async def test_running_run_wins_over_newer_terminal_runs(factory):
    tenant, chat = uuid.uuid4(), uuid.uuid4()
    _, running, _ = await _seed(factory, tenant, chat, [("failed", 1), ("running", 2), ("completed", 3)])
    assert (await dws.load_run_by_chat(factory, tenant, chat)).id == running


async def test_newest_of_several_running_wins_and_other_tenants_are_ignored(factory):
    tenant, chat = uuid.uuid4(), uuid.uuid4()
    _, newer_running = await _seed(factory, tenant, chat, [("running", 1), ("running", 2)])
    await _seed(factory, uuid.uuid4(), chat, [("running", 9)])
    assert (await dws.load_run_by_chat(factory, tenant, chat)).id == newer_running
    assert await dws.load_run_by_chat(factory, uuid.uuid4(), uuid.uuid4()) is None
