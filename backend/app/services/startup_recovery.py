"""Startup/shutdown recovery sweeps (U9).

Run on the BYPASSRLS system session factory (``SystemSessionLocal``): one statement covers
every tenant, no per-tenant GUC loop. Only rows whose last activity is OLDER than the stale
threshold are touched, so jobs that another replica started moments ago are never failed.

Last activity is ``COALESCE(started_at, created_at)``: neither table has a heartbeat column
yet, so a legitimately running job older than the threshold (STALE_JOB_THRESHOLD_SECONDS)
can still be failed by a restarting replica - keep the threshold above the longest job.
"""
from __future__ import annotations

from sqlalchemy import text


async def fail_stale_deep_work_jobs(session_factory, stale_seconds: int, reason: str) -> int:
    async with session_factory() as db:
        result = await db.execute(
            text(
                "UPDATE background_jobs SET status = 'failed', error = :reason, "
                "completed_at = NOW() "
                "WHERE status IN ('running', 'pending') AND job_type = 'deep_work' "
                "AND COALESCE(started_at, created_at) < NOW() - make_interval(secs => :stale)"
            ),
            {"reason": reason, "stale": stale_seconds},
        )
        await db.commit()
        return result.rowcount or 0


async def fail_stale_knowledge_documents(session_factory, stale_seconds: int) -> int:
    async with session_factory() as db:
        result = await db.execute(
            text(
                "UPDATE knowledge_documents SET status = 'error', "
                "error_message = 'Server restarted during processing. Please re-upload.' "
                "WHERE status IN ('uploading', 'processing') "
                "AND created_at < NOW() - make_interval(secs => :stale)"
            ),
            {"stale": stale_seconds},
        )
        await db.commit()
        return result.rowcount or 0
