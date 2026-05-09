"""Thin async service for persisting RunEvents.

Fire-and-forget — exceptions are logged, never raised.
All callers must pass the request's existing DB session so that
the RunEvent is written within the same RLS-scoped transaction.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run_event import RunEvent

logger = logging.getLogger(__name__)


def _to_uuid(value: str | UUID | None) -> UUID | None:
    """Convert a string or UUID to UUID, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


async def emit_run_event(
    db: AsyncSession,
    *,
    run_id: str | UUID,
    tenant_id: str | UUID,
    event_type: str,
    session_id: str | UUID | None = None,
    agent_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    payload: dict | None = None,
) -> None:
    """Persist a RunEvent row.

    Fire-and-forget: exceptions are caught, logged as warnings, and never
    propagated — so this call can never break the surrounding execution path.
    """
    try:
        run_uuid = _to_uuid(run_id)
        tenant_uuid = _to_uuid(tenant_id)
        if run_uuid is None or tenant_uuid is None:
            logger.warning(
                "emit_run_event: invalid run_id=%r or tenant_id=%r — skipping",
                run_id,
                tenant_id,
            )
            return

        event = RunEvent(
            id=uuid4(),
            run_id=run_uuid,
            tenant_id=tenant_uuid,
            event_type=event_type,
            session_id=_to_uuid(session_id),
            agent_name=agent_name,
            timestamp=datetime.now(timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            payload=payload,
        )
        db.add(event)
        await db.flush()
    except Exception:
        logger.warning("Failed to persist RunEvent (event_type=%s)", event_type, exc_info=True)
