"""Retention sweep for project memory (plan U32 / R25).

Rejecting a pending row deletes it at once, so what remains to expire is *stale pending* rows: proposals
(curator ``propose`` mode, tainted sources) nobody approved within ``MEMORY_RETENTION_DAYS``. Only
``pending`` rows older than the window are deleted; active memory never expires here (bullets retire by
use in ``memory_playbook.retire_expired``). The caller owns the session and the commit; run it on a
tenant-scoped session (RLS limits it to that tenant) or pass ``tenant_id`` to narrow further.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.project_memory import MemoryBullet, MemoryFile

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30


def retention_days() -> int:
    return int(getattr(get_settings(), "MEMORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


async def sweep_expired(
    db: AsyncSession, *, now: datetime | None = None, days: int | None = None, tenant_id=None
) -> dict[str, int]:
    """Delete pending files and bullets last touched more than ``days`` ago; return the counts."""
    window = days if days is not None else retention_days()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window)
    counts: dict[str, int] = {}
    for key, model in (("files", MemoryFile), ("bullets", MemoryBullet)):
        stmt = delete(model).where(model.status == "pending", model.updated_at < cutoff)
        if tenant_id is not None:
            stmt = stmt.where(model.tenant_id == tenant_id)
        counts[key] = (await db.execute(stmt)).rowcount or 0
    await db.flush()
    if counts["files"] or counts["bullets"]:
        logger.info("memory_retention_swept files=%s bullets=%s", counts["files"], counts["bullets"])
    return counts
