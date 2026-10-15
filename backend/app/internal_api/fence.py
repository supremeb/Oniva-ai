"""Fence admission for the tool gateway (U5 / R7, KTD4).

A session has one high-water mark in ``engine_fences`` (system role only). Any fenced request
(manifest, invoke, reconcile) with a token raises it; a lower token is refused with
``409 fence_rejected``. A write effect holds ``FOR SHARE`` on the session's fence row for its whole
duration, so a successor's raise (a row UPDATE) waits for in-flight effects: once the raise
returns, no old effect is running and none can start. A raise waits at most
``RAISE_WAIT_MS`` for that lock, then answers ``503`` + ``Retry-After``.
"""
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError

from app.models.engine import EngineFence

logger = logging.getLogger(__name__)

RAISE_WAIT_MS = 5000
_TOKEN = re.compile(r"\d{1,15}")  # a safe integer in the engine (JS Number)

SessionFactory = Callable[[], Any]  # () -> async context manager yielding an AsyncSession


class GatewayError(Exception):
    """An error answered as ``status`` with a bare JSON body (the engine reads ``error``)."""

    def __init__(self, status: int, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
        super().__init__(body.get("error"))
        self.status = status
        self.body = body
        self.headers = headers or {}


async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body, headers=exc.headers)


class FenceRejected(GatewayError):
    def __init__(self, high_water: int):
        super().__init__(409, {"error": "fence_rejected", "highWater": int(high_water)})
        self.high_water = int(high_water)


class FenceBusy(GatewayError):
    """A write effect of an older owner still holds the session's fence row."""

    def __init__(self) -> None:
        super().__init__(503, {"error": "fence_busy"}, {"Retry-After": "1"})


def fence_headers(request: Request) -> Tuple[Optional[int], Optional[str]]:
    """``(token, session)`` from X-Engine-Fence / X-Engine-Session; a malformed token is a 400."""
    raw = request.headers.get("x-engine-fence")
    session = request.headers.get("x-engine-session") or None
    if raw is None:
        return None, session
    if not _TOKEN.fullmatch(raw.strip()):
        raise GatewayError(400, {"error": "invalid_fence"})
    return int(raw), session


def _is_postgres(db: Any) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def is_lock_timeout(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code == "55P03":
            return True
    return "lock timeout" in str(exc).lower() or "LockNotAvailable" in type(getattr(orig, "__cause__", orig)).__name__


async def _set_lock_timeout(db: Any, wait_ms: int) -> None:
    if _is_postgres(db):
        await db.execute(text(f"SET LOCAL lock_timeout = '{int(wait_ms)}ms'"))


async def _current(db: Any, session_id: str) -> Optional[int]:
    return (
        await db.execute(select(EngineFence.high_water).where(EngineFence.session_id == session_id))
    ).scalar_one_or_none()


async def admit_fence(
    factory: SessionFactory, session_id: str, token: int, *, wait_ms: Optional[int] = None
) -> int:
    """Admit ``token`` for ``session_id`` and raise the mark to it. Returns the new mark.

    Raises FenceRejected (409) for a lower token and FenceBusy (503) when an older owner's write
    effect still holds the row past ``wait_ms``.
    """
    async with factory() as db:
        # Fast path without a row lock: the owner's own concurrent calls (same token) must not
        # queue behind each other's write effects, which hold FOR SHARE on this row.
        current = await _current(db, session_id)
        if current is not None:
            if current > token:
                raise FenceRejected(current)
            if current == token:
                return current
        await _set_lock_timeout(db, wait_ms or RAISE_WAIT_MS)
        insert = pg_insert if _is_postgres(db) else sqlite_insert
        stmt = insert(EngineFence).values(session_id=session_id, high_water=token, updated_at=func.now())
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id"],
            set_={"high_water": stmt.excluded.high_water, "updated_at": func.now()},
            where=EngineFence.high_water <= stmt.excluded.high_water,
        ).returning(EngineFence.high_water)
        try:
            raised = (await db.execute(stmt)).scalar_one_or_none()
        except DBAPIError as exc:
            if is_lock_timeout(exc):
                logger.info("fence raise for %s timed out waiting for an in-flight write", session_id)
                raise FenceBusy() from exc
            raise
        if raised is None:  # a higher mark landed between the read and the upsert
            raise FenceRejected(await _current(db, session_id) or 0)
        return int(raised)


async def recheck_fence(factory: SessionFactory, session_id: str, token: int) -> None:
    """Refuse when a successor has raised the mark above ``token`` (reads: no lock)."""
    async with factory() as db:
        current = await _current(db, session_id)
        if current is not None and current > token:
            raise FenceRejected(current)


@asynccontextmanager
async def hold_write_fence(
    factory: SessionFactory, session_id: str, token: int, *, wait_ms: Optional[int] = None
) -> AsyncIterator[None]:
    """Hold ``FOR SHARE`` on the session's fence row and re-check ``token`` under it.

    The check and the lock are one statement: once it returns, a raise above ``token`` cannot commit
    until this block exits, and none committed before it. The caller runs the effect and stores the
    outcome inside the block.
    """
    async with factory() as db:
        await _set_lock_timeout(db, wait_ms or RAISE_WAIT_MS)
        try:
            current = (
                await db.execute(
                    select(EngineFence.high_water)
                    .where(EngineFence.session_id == session_id)
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
        except DBAPIError as exc:
            if is_lock_timeout(exc):
                raise FenceBusy() from exc
            raise
        if current is not None and current > token:
            raise FenceRejected(current)
        yield
