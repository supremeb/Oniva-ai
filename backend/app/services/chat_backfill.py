"""Projection of legacy ``chat_sessions.messages`` into ``chat_session_messages`` (U10, KTD7).

The JSONB column is never modified. Every insert is ``ON CONFLICT (session_id, seq) DO NOTHING`` so
the projection is idempotent and resumable at any point (an interrupted run leaves a prefix or a
gap-free subset; the next run fills what is missing).

Three entry points:
  * :func:`ensure_backfilled` - lazy path, called before a session is read or appended to.
  * :func:`append_message_row` - dual-write path for one newly appended message.
  * :func:`run` / ``python -m app.services.chat_backfill`` - admin command over all tenants
    (system role, resumable by session id with ``--after``).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession, ChatSessionMessage

logger = logging.getLogger(__name__)

_INSERT_CHUNK = 500


def validate_parts(parts: Any) -> bool:
    """Schema check for stored parts: a list of objects that each carry a string ``type``."""
    return isinstance(parts, list) and all(
        isinstance(p, dict) and isinstance(p.get("type"), str) for p in parts
    )


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)  # legacy writes are naive UTC


def _content_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    return "" if content is None else str(content)


def parts_for_message(msg: dict) -> list:
    """Parts copied as-is from stored data; a single text part from ``content`` when there are none."""
    metadata = msg.get("metadata")
    stored = msg.get("parts")
    if not stored and isinstance(metadata, dict):
        stored = metadata.get("parts")
    if stored:
        return stored if isinstance(stored, list) else [stored]
    return [{"type": "text", "text": _content_text(msg)}]


def message_row(tenant_id: uuid.UUID, session_id: uuid.UUID, seq: int, msg: dict) -> dict:
    """The ``chat_session_messages`` values for the message stored at index ``seq``."""
    parts = parts_for_message(msg)
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "session_id": session_id,
        "seq": seq,
        "role": str(msg.get("role") or "assistant")[:20],
        "content": _content_text(msg),
        "parts": parts,
        "parts_valid": validate_parts(parts),
        "created_at": _parse_ts(msg.get("timestamp")) or datetime.now(timezone.utc),
    }


async def _insert_rows(db: AsyncSession, rows: list[dict]) -> int:
    inserted = 0
    for i in range(0, len(rows), _INSERT_CHUNK):
        chunk = rows[i:i + _INSERT_CHUNK]
        stmt = (
            pg_insert(ChatSessionMessage)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["session_id", "seq"])
            .returning(ChatSessionMessage.seq)
        )
        inserted += len((await db.execute(stmt)).all())
    return inserted


async def backfill_messages(
    db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID, messages: list
) -> int:
    """Project ``messages`` into the read model. Returns the number of rows newly inserted."""
    rows = [
        message_row(tenant_id, session_id, i, m)
        for i, m in enumerate(messages or [])
        if isinstance(m, dict)
    ]
    return await _insert_rows(db, rows) if rows else 0


async def ensure_backfilled(db: AsyncSession, session: ChatSession) -> int:
    """Lazy path: fill any missing read-model rows for ``session`` (no-op when complete)."""
    messages = session.messages or []
    if not messages:
        return 0
    have = (await db.execute(
        select(func.count()).select_from(ChatSessionMessage)
        .where(ChatSessionMessage.session_id == session.id)
    )).scalar() or 0
    if have >= len(messages):
        return 0
    return await backfill_messages(db, session.tenant_id, session.id, messages)


async def append_message_row(
    db: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID, message: dict
) -> None:
    """Dual-write: project the message just appended to ``chat_sessions.messages`` (its last element).

    Runs in the caller's transaction, so a failure rolls the JSONB append back with it.
    """
    length = (await db.execute(
        select(func.jsonb_array_length(ChatSession.messages)).where(ChatSession.id == session_id)
    )).scalar() or 0
    await _insert_rows(db, [message_row(tenant_id, session_id, length - 1, message)])


@dataclass
class BackfillResult:
    sessions: int = 0
    rows: int = 0
    last_session_id: Optional[uuid.UUID] = None


async def run(
    db: AsyncSession,
    batch: int = 200,
    after: Optional[uuid.UUID] = None,
    max_batches: Optional[int] = None,
) -> BackfillResult:
    """Backfill every session with id > ``after`` in id order, committing after each batch.

    Resumable: feed ``last_session_id`` back as ``after``. Meant for a BYPASSRLS session (rows
    carry each session's own ``tenant_id``).
    """
    result = BackfillResult(last_session_id=after)
    batches = 0
    while max_batches is None or batches < max_batches:
        q = select(ChatSession.id, ChatSession.tenant_id, ChatSession.messages).order_by(ChatSession.id).limit(batch)
        if result.last_session_id is not None:
            q = q.where(ChatSession.id > result.last_session_id)
        page = (await db.execute(q)).all()
        if not page:
            break
        for sid, tid, messages in page:
            result.rows += await backfill_messages(db, tid, sid, messages or [])
            result.sessions += 1
            result.last_session_id = sid
        await db.commit()
        batches += 1
    return result


async def _main(batch: int, after: Optional[uuid.UUID]) -> BackfillResult:
    from app.core.database import get_system_session

    async with get_system_session() as db:
        return await run(db, batch=batch, after=after)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill chat_session_messages from chat_sessions.")
    parser.add_argument("--batch", type=int, default=200, help="sessions per transaction")
    parser.add_argument("--after", type=uuid.UUID, default=None, help="resume after this session id")
    args = parser.parse_args(argv)
    res = asyncio.run(_main(args.batch, args.after))
    print(f"sessions={res.sessions} rows_inserted={res.rows} last_session_id={res.last_session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
