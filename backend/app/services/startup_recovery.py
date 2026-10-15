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
                # Engine sessions survive a restart; their status is reconciled lazily (KTD11).
                "AND COALESCE(input_data->>'engine', '') <> 'oniva-engine' "
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


async def interrupt_stale_tool_invocations(session_factory) -> int:
    """Give stale in-flight non-idempotent tool claims the ``interrupted/unknown`` outcome (plan 003 U5).

    A claim older than the longest tool timeout plus grace belongs to a worker that died mid-effect.
    Reconcile must not say "did not run" for it, so it is answered as unknown; idempotent tools are
    left to re-run on their next call.
    """
    from app.internal_api.invocations import interrupt_stale
    from app.internal_api.tools import idempotent_tool_names, stale_after_seconds

    return await interrupt_stale(session_factory, stale_after_seconds(), idempotent_tool_names())
