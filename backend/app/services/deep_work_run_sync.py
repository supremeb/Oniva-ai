"""Deep Work runs in the run reconciler.

A started engine Deep Work run settles when someone reads it (``deep_work_engine.refresh_run`` on full-state,
status and list reads). A run that ends while nobody is looking would stay ``running`` with its background job,
and an active-looking run shadows the chat's newer run. The reconciler finds such runs and settles them the
same way, from the engine view.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.deep_work_run import DeepWorkRun
from app.models.engine import EngineConversation
from app.services import deep_work_state as dws
from app.services import run_handlers

logger = logging.getLogger(__name__)

KIND = "deep_work"
STUCK_BATCH = 200


async def find_stuck(session, older_than: timedelta) -> list:
    """Started engine runs still ``running`` with no update for ``older_than``, bound to their engine conversation."""
    cutoff = datetime.now(timezone.utc) - older_than
    rows = (await session.execute(
        select(DeepWorkRun, EngineConversation.session_id)
        .join(EngineConversation, EngineConversation.conversation_id == DeepWorkRun.conversation_id, isouter=True)
        .where(
            DeepWorkRun.status == "running",
            DeepWorkRun.is_engine.is_(True),
            DeepWorkRun.job_id.is_not(None),
            DeepWorkRun.updated_at < cutoff,
        ).order_by(DeepWorkRun.updated_at).limit(STUCK_BATCH)
    )).all()
    return [
        run_handlers.RunRef(kind=KIND, ref_id=run.id, tenant_id=run.tenant_id, project_id=run.project_id,
                            session_id=session_id, conversation_id=run.conversation_id)
        for run, session_id in rows
    ]


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    """Settle the run from the engine, as a read of it would."""
    from app.api.v1 import deep_work_engine as dwe  # lazy: the API module imports this package's neighbours

    if ref.tenant_id is None:
        return
    factory = dwe.tenant_factory(ref.tenant_id)
    run = await dws.load_run_by_id(factory, ref.tenant_id, ref.ref_id)
    if run is None:
        return
    if run.status == "running":
        await dwe.refresh_run(dwe._engine(), factory, run, reap=True)
        run = await dws.load_run_by_id(factory, ref.tenant_id, ref.ref_id)
    await enqueue_curation(run)


async def enqueue_curation(run) -> None:
    """After a Deep Work run settled as completed, start the memory curator for it (U31).

    A no-op unless ``MEMORY_CURATOR_MODE`` is on; idempotent (the curator run id is deterministic per Deep Work
    run), so settling by a read first and by this hook later both land on the same run. Never raises.
    """
    if run is None or run.status != "completed":
        return
    from app.services import memory_curator

    if memory_curator.curator_mode() == "off":
        return
    from app.core.database import SystemSessionLocal

    await memory_curator.start_curation(SystemSessionLocal, conversation_id=run.conversation_id, run_id=str(run.id))


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished, find_stuck=find_stuck)


register_hooks()
