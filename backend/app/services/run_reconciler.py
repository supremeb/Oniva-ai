"""Leader-safe run reconciler (plan 003, U5).

Closes the two crash windows around an engine run: a settle whose finished post was lost, and a
domain row whose engine run was never started. It replaces the boot "mark failed" sweep for runs:
the engine owns recovery of in-flight work, and this loop only reads engine state and applies it
through the hooks in ``run_handlers``.

Every ``RUN_RECONCILE_INTERVAL_S`` it takes a Postgres advisory lock (a second reconciler gets
``False`` and does nothing), asks each registered kind's ``find_stuck`` for rows quiet for at
least ``RUN_RECONCILE_AFTER_S``, and per row:

* with an engine conversation: read its snapshot (and root task when the row recorded one) and
  call ``on_finished`` once nothing is active; an engine that cannot be reached leaves the row;
* without one (``404``): call ``start_missing`` with bounded retries (``RUN_START_RETRY_MAX``,
  exponential backoff from ``RUN_START_RETRY_BASE_S``); at the bound call ``fail(ref,
  "engine_start_failed")`` once; a kind without a start hook is failed with ``engine_run_missing``.

This is non-run work: it is on the U30 ``create_task`` guard allow-list.
"""
import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select, text

from app.models.engine import EngineConversation
from app.services import run_handlers
from app.services.deep_work.engine_view import read_snapshot
from app.services.engine_client import EngineError, get_engine_client
from app.services.run_status import from_engine_task

logger = logging.getLogger(__name__)

LOCK_KEY = 7_640_221_001  # arbitrary, stable across replicas
_ACTIVE_TASK = ("pending", "running", "waiting")


def view_is_active(view: dict) -> bool:
    """True while the engine still has work for the conversation (a run, queued input or a live task)."""
    if (view.get("run") or {}).get("active") or (view.get("inbox") or {}).get("queue"):
        return True
    return any((t or {}).get("status") in _ACTIVE_TASK for t in (view.get("tasks") or {}).values())


class RunReconciler:
    def __init__(self, factory: Callable, settings: Any, *,
                 engine_factory: Callable = get_engine_client,
                 clock: Callable[[], float] = time.monotonic):
        self._factory = factory
        self._settings = settings
        self._engine_factory = engine_factory
        self._clock = clock
        # (kind, ref_id) -> (failed start attempts, earliest next attempt on self._clock)
        self._retries: dict[tuple, tuple[int, float]] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ---- loop ------------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        interval = float(self._settings.RUN_RECONCILE_INTERVAL_S)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run reconciler pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), interval)
            except asyncio.TimeoutError:
                pass

    # ---- one pass --------------------------------------------------------------------------

    async def run_once(self) -> Optional[dict]:
        """One pass under the advisory lock. ``None`` when another reconciler holds it."""
        async with self._factory() as probe:
            bind = probe.bind
        if bind is not None and bind.dialect.name == "postgresql":
            async with bind.connect() as conn:
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar()
                if not got:
                    return None
                try:
                    return await self._pass()
                finally:
                    await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        return await self._pass()

    async def _pass(self) -> dict:
        counts = {"checked": 0, "finished": 0, "started": 0, "failed": 0}
        older_than = timedelta(seconds=int(self._settings.RUN_RECONCILE_AFTER_S))
        for kind in run_handlers.kinds():
            find = run_handlers.hook(kind, "find_stuck")
            if find is None:
                continue
            try:
                async with self._factory() as db:
                    refs = list(await find(db, older_than))
            except Exception:
                logger.exception("find_stuck failed for run kind %r", kind)
                continue
            for ref in refs:
                counts["checked"] += 1
                try:
                    await self._reconcile(ref, counts)
                except Exception:
                    logger.exception("reconcile failed for %s %s", ref.kind, ref.ref_id)
        return counts

    # ---- one row ---------------------------------------------------------------------------

    async def _bind(self, ref: run_handlers.RunRef) -> run_handlers.RunRef:
        """Fill tenant, project, session and conversation from the run's engine binding row."""
        async with self._factory() as db:
            row = (await db.execute(
                select(EngineConversation).where(
                    EngineConversation.run_kind == ref.kind,
                    EngineConversation.run_ref_id == ref.ref_id,
                )
            )).scalars().first()
        if row is None:
            return ref
        return ref.with_binding(
            tenant_id=ref.tenant_id or row.tenant_id, project_id=ref.project_id or row.project_id,
            session_id=ref.session_id or row.session_id,
            conversation_id=ref.conversation_id or row.conversation_id,
        )

    async def _snapshot(self, ref: run_handlers.RunRef) -> Optional[dict]:
        """Engine state of the run: ``{source, view, task, runStatus}``; ``None`` when the engine has no
        conversation for it; raises ``EngineError`` when the engine cannot be asked."""
        if ref.conversation_id is None or ref.tenant_id is None or ref.project_id is None:
            return None
        engine = self._engine_factory()
        session_id = ref.session_id or engine.directory.default_session_id
        try:
            view = await read_snapshot(engine, session_id, ref.tenant_id, ref.project_id, ref.conversation_id)
        except EngineError as e:
            if e.status == 404:
                return None
            raise
        task = None
        status = None
        if ref.task_id:
            try:
                task = await engine.get_task(session_id, ref.tenant_id, ref.project_id,
                                             ref.conversation_id, ref.task_id)
                status = from_engine_task(task.get("status"), outcome=task.get("outcome"))
            except EngineError as e:
                if e.status != 404:
                    raise
        return {"source": "reconcile", "view": view, "task": task, "runStatus": status}

    async def _reconcile(self, ref: run_handlers.RunRef, counts: dict) -> None:
        ref = await self._bind(ref)
        try:
            snap = await self._snapshot(ref)
        except (EngineError, asyncio.TimeoutError) as e:
            logger.warning("run reconciler cannot reach the engine for %s %s: %s", ref.kind, ref.ref_id, e)
            return
        if snap is not None:
            await self._settle(ref, snap, counts)
            return
        await self._missing(ref, counts)

    async def _settle(self, ref, snap: dict, counts: dict) -> None:
        if view_is_active(snap["view"]) and not ref.extra.get("abort_pending"):
            return  # (a cancelled row with an unsent abort still goes to its hook to re-issue it)
        on_finished = run_handlers.hook(ref.kind, "on_finished")
        if on_finished is None:
            logger.warning("run kind %r has no on_finished hook", ref.kind)
            return
        await on_finished(ref, snap)
        self._retries.pop((ref.kind, ref.ref_id), None)
        counts["finished"] += 1

    async def _missing(self, ref, counts: dict) -> None:
        key = (ref.kind, ref.ref_id)
        start = run_handlers.hook(ref.kind, "start_missing")
        if start is None:
            await self._fail(ref, "engine_run_missing", counts)
            return
        attempts, next_at = self._retries.get(key, (0, 0.0))
        if self._clock() < next_at:
            return
        result = await start(ref)
        if result.status in ("started", "already_started"):
            self._retries.pop(key, None)
            counts["started"] += 1
            return
        attempts = max(attempts + 1, result.attempts or 0)
        if attempts >= int(self._settings.RUN_START_RETRY_MAX):
            self._retries.pop(key, None)
            await self._fail(ref, "engine_start_failed", counts)
            return
        delay = float(self._settings.RUN_START_RETRY_BASE_S) * (2 ** (attempts - 1))
        self._retries[key] = (attempts, self._clock() + delay)
        logger.warning("start_missing failed for %s %s (attempt %d): %s",
                       ref.kind, ref.ref_id, attempts, result.error)

    async def _fail(self, ref, code: str, counts: dict) -> None:
        fail = run_handlers.hook(ref.kind, "fail")
        if fail is None:
            logger.error("run kind %r has no fail hook; %s %s stays as is (%s)",
                         ref.kind, ref.kind, ref.ref_id, code)
            return
        await fail(ref, code)
        counts["failed"] += 1
