"""Deep Work run state on the engine path (deep-work-on-engine plan, U3, KTD11).

Every function opens its own tenant-scoped session from ``factory`` (R17: no shared
``AsyncSession`` across concurrent tool calls) and takes the run row with ``SELECT ... FOR UPDATE``
so replays and concurrent worker saves cannot lose or regress state:

* ``phase`` only moves forward (``PHASES``), ``round`` only grows;
* a terminal ``status`` (completed, failed, cancelled) is sticky and freezes the row;
* a worker's learnings are keyed by ``(round, worker_id)``, so a replay overwrites itself.

``reconcile`` compares a still-``running`` row with the engine run state and writes ``failed`` or
``cancelled`` once.
"""
import copy
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import case, select

from app.models.background_job import BackgroundJob
from app.models.deep_work_run import DeepWorkRun

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]  # () -> async context manager yielding an AsyncSession

PHASES = ("clarify", "plan", "research", "reflect", "synthesize", "done")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
STATUSES = TERMINAL_STATUSES | {"running"}


@dataclass(frozen=True)
class EngineRunState:
    """What the engine says about the root run: still active, or how it last ended.

    ``ended`` is ``None`` while ``active``; otherwise one of ``run_error`` (the run failed),
    ``aborted`` (cancelled) or ``finished`` (ended normally).
    """

    active: bool
    ended: Optional[str] = None
    error: Optional[str] = None


ReadEngineState = Callable[[], Awaitable[EngineRunState]]


@dataclass(frozen=True)
class RunSnapshot:
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    query: str
    enriched_query: Optional[str]
    status: str
    phase: str
    round: int
    plan: Optional[Any]
    rounds: List[Dict[str, Any]]
    reflections: List[Any]
    artifacts: List[Any]
    job_id: Optional[uuid.UUID]
    is_engine: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    chat_id: Optional[uuid.UUID] = None
    awaiting_input: Optional[Any] = None

    @classmethod
    def of(cls, r: DeepWorkRun) -> "RunSnapshot":
        return cls(
            id=r.id, tenant_id=r.tenant_id, project_id=r.project_id, user_id=r.user_id,
            conversation_id=r.conversation_id, query=r.query, enriched_query=r.enriched_query,
            status=r.status, phase=r.phase, round=r.round or 0, plan=copy.deepcopy(r.plan),
            rounds=copy.deepcopy(r.rounds or []), reflections=copy.deepcopy(r.reflections or []),
            artifacts=copy.deepcopy(r.artifacts or []), job_id=r.job_id, is_engine=bool(r.is_engine),
            created_at=r.created_at, updated_at=r.updated_at, chat_id=r.chat_id,
            awaiting_input=copy.deepcopy(r.awaiting_input),
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _approvals(db: Any):
    from app.services.approval_service import ApprovalService  # lazy: the service imports half the app

    return ApprovalService(db)


async def _close_run_approvals(factory: SessionFactory, tenant_id: Any, conversation_id: Any, status: str) -> None:
    """A run reaching a terminal state closes its pending approvals (KTD11). Never fails the transition."""
    try:
        async with factory() as db:
            await _approvals(db).mark_closed(tenant_id, conversation_id=conversation_id, reason=f"run {status}")
    except Exception:
        logger.warning("Could not close approvals of run %s", conversation_id, exc_info=True)


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _phase_rank(phase: str) -> int:
    try:
        return PHASES.index(phase)
    except ValueError:
        raise ValueError(f"unknown phase {phase!r}")


async def _locked(db: Any, tenant_id: Any, conversation_id: Any) -> Optional[DeepWorkRun]:
    return (
        await db.execute(
            select(DeepWorkRun)
            .where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.conversation_id == _uuid(conversation_id))
            .with_for_update()
        )
    ).scalar_one_or_none()


def _advance(
    row: DeepWorkRun, *, phase: Optional[str] = None, round: Optional[int] = None, status: Optional[str] = None,
    plan: Optional[Any] = None, enriched_query: Optional[str] = None,
    reflection: Optional[Any] = None, artifact: Optional[Any] = None,
) -> None:
    """The guarded field updates of ``update_run``, applied to an already locked, non-terminal row."""
    if phase is not None and _phase_rank(phase) > _phase_rank(row.phase):
        row.phase = phase
    if round is not None and round > (row.round or 0):
        row.round = round
    if plan is not None:
        row.plan = plan
    if enriched_query is not None:
        row.enriched_query = enriched_query
    if reflection is not None and reflection not in (row.reflections or []):
        row.reflections = [*(row.reflections or []), reflection]
    if artifact is not None and artifact not in (row.artifacts or []):
        row.artifacts = [*(row.artifacts or []), artifact]
    if status is not None:
        row.status = status
    row.updated_at = _now()


async def create_run(
    factory: SessionFactory, *, tenant_id: Any, project_id: Any, user_id: Any, conversation_id: Any,
    query: str, enriched_query: Optional[str] = None, job_id: Any = None, chat_id: Any = None,
) -> RunSnapshot:
    """Create the run for a root conversation; a second call returns the existing row unchanged."""
    async with factory() as db:
        existing = await _locked(db, tenant_id, conversation_id)
        if existing is not None:
            return RunSnapshot.of(existing)
        row = DeepWorkRun(
            id=uuid.uuid4(), tenant_id=_uuid(tenant_id), project_id=_uuid(project_id), user_id=_uuid(user_id),
            conversation_id=_uuid(conversation_id), query=query, enriched_query=enriched_query,
            status="running", phase="clarify", round=0, rounds=[], reflections=[], artifacts=[],
            job_id=_uuid(job_id) if job_id else None, is_engine=True, created_at=_now(), updated_at=_now(),
            chat_id=_uuid(chat_id) if chat_id else None,
        )
        db.add(row)
        await db.flush()
        return RunSnapshot.of(row)


async def load_run(factory: SessionFactory, tenant_id: Any, conversation_id: Any) -> Optional[RunSnapshot]:
    async with factory() as db:
        row = (
            await db.execute(
                select(DeepWorkRun).where(
                    DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.conversation_id == _uuid(conversation_id)
                )
            )
        ).scalar_one_or_none()
        return RunSnapshot.of(row) if row else None


async def record_chat_id(factory: SessionFactory, tenant_id: Any, conversation_id: Any, chat_id: Any) -> Optional[RunSnapshot]:
    """Remember the originating chat on the run (no phase/status guard: a terminal run keeps its chat link)."""
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            return None
        if row.chat_id != _uuid(chat_id):
            row.chat_id = _uuid(chat_id)
            await db.flush()
        return RunSnapshot.of(row)


async def load_run_by_chat(factory: SessionFactory, tenant_id: Any, chat_id: Any) -> Optional[RunSnapshot]:
    """The run to show for a chat: an active one if any, else the most recent. Tenant-scoped; callers check the owner."""
    async with factory() as db:
        row = (
            await db.execute(
                select(DeepWorkRun)
                .where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.chat_id == _uuid(chat_id))
                .order_by(
                    case((DeepWorkRun.status == "running", 0), else_=1),  # an active run first
                    DeepWorkRun.created_at.desc(),
                    DeepWorkRun.updated_at.desc(),
                )
                .limit(1)
            )
        ).scalars().first()
        return RunSnapshot.of(row) if row else None


async def update_run(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, *,
    phase: Optional[str] = None, round: Optional[int] = None, status: Optional[str] = None,
    plan: Optional[Any] = None, enriched_query: Optional[str] = None,
    reflection: Optional[Any] = None, artifact: Optional[Any] = None,
) -> Optional[RunSnapshot]:
    """Apply a guarded update; ``None`` when the run does not exist.

    A terminal run is frozen: the call returns it unchanged. Otherwise ``phase`` and ``round`` never
    move backward, ``reflection`` / ``artifact`` are appended (an identical entry is not added twice).
    """
    if phase is not None:
        _phase_rank(phase)
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            return RunSnapshot.of(row)
        _advance(row, phase=phase, round=round, status=status, plan=plan, enriched_query=enriched_query,
                 reflection=reflection, artifact=artifact)
        if row.status in TERMINAL_STATUSES:
            await _settle_job(db, row)
        await db.flush()
        snap = RunSnapshot.of(row)
    if snap.status in TERMINAL_STATUSES:
        await _close_run_approvals(factory, tenant_id, conversation_id, snap.status)
    return snap


async def save_worker_learnings(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, *, round: int, worker_id: str, learnings: Any,
) -> Optional[RunSnapshot]:
    """Record one worker's learnings for ``round`` without losing a concurrent worker's.

    Keyed by ``(round, worker_id)``: a replay overwrites itself. Raises the run's ``round`` to
    ``round`` if it is behind. A terminal run ignores the save (a cancelled run takes no more output).
    """
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            return None
        if row.status in TERMINAL_STATUSES:
            return RunSnapshot.of(row)
        rounds = copy.deepcopy(row.rounds or [])
        entry = next((r for r in rounds if r.get("round") == round), None)
        if entry is None:
            entry = {"round": round, "workers": {}}
            rounds.append(entry)
            rounds.sort(key=lambda r: r["round"])
        entry["workers"][worker_id] = learnings
        row.rounds = rounds  # new object so the JSONB change is detected
        if round > (row.round or 0):
            row.round = round
        row.updated_at = _now()
        await db.flush()
        return RunSnapshot.of(row)


class LockedRun:
    """The run row held under ``FOR UPDATE`` for a read-modify-write that spans file IO.

    ``run`` is the snapshot taken under the lock; ``advance`` applies the guarded field updates in the
    same transaction (a terminal run ignores them), so a tool can write a workspace file and settle
    the run atomically with respect to other writers.
    """

    def __init__(self, row: DeepWorkRun):
        self._row = row
        self.run = RunSnapshot.of(row)

    def advance(self, **fields: Any) -> RunSnapshot:
        if "phase" in fields and fields["phase"] is not None:
            _phase_rank(fields["phase"])
        if fields.get("status") is not None and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        if self._row.status not in TERMINAL_STATUSES:
            _advance(self._row, **fields)
            self.run = RunSnapshot.of(self._row)
        return self.run


@asynccontextmanager
async def run_lock(factory: SessionFactory, tenant_id: Any, conversation_id: Any) -> AsyncIterator[Optional[LockedRun]]:
    """Hold the run row locked for the body; yields ``None`` when the run does not exist."""
    ended: Optional[str] = None
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            yield None
            return
        was_terminal = row.status in TERMINAL_STATUSES
        locked = LockedRun(row)
        yield locked
        if not was_terminal and row.status in TERMINAL_STATUSES:
            ended = row.status
            await _settle_job(db, row)
        await db.flush()
    if ended is not None:
        await _close_run_approvals(factory, tenant_id, conversation_id, ended)


async def _settle_job(db, row: DeepWorkRun) -> None:
    """The run's background job ends with the run (same transaction): a completed Deep Work left it ``running``."""
    if row.job_id is None:
        return
    job = await db.get(BackgroundJob, row.job_id)
    if job is not None and job.status in ("pending", "running"):
        job.status = row.status
        job.completed_at = _now()


async def _mutate(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, fn: Callable[[DeepWorkRun], None],
) -> Optional[RunSnapshot]:
    async with run_lock(factory, tenant_id, conversation_id) as locked:
        if locked is None:
            return None
        if locked.run.status not in TERMINAL_STATUSES:
            fn(locked._row)
            locked._row.updated_at = _now()
            locked.run = RunSnapshot.of(locked._row)
        return locked.run


async def replace_artifact(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, artifact: Dict[str, Any],
) -> Optional[RunSnapshot]:
    """Record a published deliverable, replacing an earlier one with the same ``filename`` (U5)."""
    def fn(row: DeepWorkRun) -> None:
        kept = [a for a in (row.artifacts or []) if not (isinstance(a, dict) and a.get("filename") == artifact.get("filename"))]
        row.artifacts = [*kept, artifact]
    return await _mutate(factory, tenant_id, conversation_id, fn)


async def register_round(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, *, round: int, workers: List[Dict[str, Any]],
) -> Optional[RunSnapshot]:
    """Register ``round``'s workers once; an already registered round keeps its first registration."""

    def fn(row: DeepWorkRun) -> None:
        rounds = copy.deepcopy(row.rounds or [])
        entry = next((r for r in rounds if r.get("round") == round), None)
        if entry is None:
            entry = {"round": round, "workers": {}}
            rounds.append(entry)
            rounds.sort(key=lambda r: r["round"])
        if "registered" not in entry:
            entry["registered"] = workers
            entry["started_at"] = _now().isoformat()
        row.rounds = rounds
        _advance(row, phase="research", round=round)

    return await _mutate(factory, tenant_id, conversation_id, fn)


async def mark_workers_failed(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, *, round: int, worker_ids: List[str],
) -> Optional[RunSnapshot]:
    """Record workers of ``round`` that ended without saving learnings (set union, so a replay is a no-op)."""

    def fn(row: DeepWorkRun) -> None:
        rounds = copy.deepcopy(row.rounds or [])
        entry = next((r for r in rounds if r.get("round") == round), None)
        if entry is None:
            return
        entry["failed"] = sorted({*entry.get("failed", []), *worker_ids})
        row.rounds = rounds

    return await _mutate(factory, tenant_id, conversation_id, fn)


async def record_reflection(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, *, round: int, reflection: Dict[str, Any],
) -> Optional[RunSnapshot]:
    """Persist the reflection for ``round``, replacing an earlier one for the same round."""

    def fn(row: DeepWorkRun) -> None:
        kept = [r for r in (row.reflections or []) if not (isinstance(r, dict) and r.get("round") == round)]
        kept.append(reflection)
        kept.sort(key=lambda r: r.get("round", 0) if isinstance(r, dict) else 0)
        row.reflections = kept
        _advance(row, phase="reflect")

    return await _mutate(factory, tenant_id, conversation_id, fn)


_ENDED_TO_STATUS = {"run_error": "failed", "aborted": "cancelled", "finished": "failed"}


async def reconcile(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, read_state: ReadEngineState,
) -> Optional[RunSnapshot]:
    """Write ``failed`` or ``cancelled`` for a ``running`` row whose engine run has ended (KTD11).

    ``finished`` without a ``completed`` row means the run ended before ``dw_finalize``: ``failed``.
    Idempotent: a row that is not ``running`` is returned as is, and the engine is not asked.
    """
    run = await load_run(factory, tenant_id, conversation_id)
    if run is None or run.status != "running":
        return run
    state = await read_state()
    if state.active or state.ended not in _ENDED_TO_STATUS:
        return run
    status = _ENDED_TO_STATUS[state.ended]
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            return None
        if row.status != "running":  # settled by a concurrent finalize / cancel / reconcile
            return RunSnapshot.of(row)
        row.status = status
        row.updated_at = _now()
        if row.job_id is not None:
            job = await db.get(BackgroundJob, row.job_id)
            if job is not None and job.status in ("pending", "running"):
                job.status = status
                job.completed_at = _now()
                if status == "failed":
                    job.error = state.error or "The engine run ended before the report was produced."
        await db.flush()
        logger.info("Deep Work run %s reconciled to %s", row.id, status)
        snap = RunSnapshot.of(row)
    await _close_run_approvals(factory, tenant_id, conversation_id, status)
    return snap


async def set_awaiting_input(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, payload: Any,
) -> Optional[RunSnapshot]:
    """Persist the intake clarification the user has to answer (inbox source); a terminal run is left alone."""
    def fn(row: DeepWorkRun) -> None:
        row.awaiting_input = copy.deepcopy(payload)
    return await _mutate(factory, tenant_id, conversation_id, fn)


async def clear_awaiting_input(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any,
) -> Optional[RunSnapshot]:
    def fn(row: DeepWorkRun) -> None:
        row.awaiting_input = None
    return await _mutate(factory, tenant_id, conversation_id, fn)


async def load_run_by_id(factory: SessionFactory, tenant_id: Any, run_id: Any) -> Optional[RunSnapshot]:
    """The run with this ``deep_work_runs.id`` (the client's session id), only within ``tenant_id``."""
    async with factory() as db:
        row = (
            await db.execute(
                select(DeepWorkRun).where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.id == _uuid(run_id))
            )
        ).scalar_one_or_none()
        return RunSnapshot.of(row) if row else None


async def attach_job(
    factory: SessionFactory, tenant_id: Any, conversation_id: Any, job_id: Any,
) -> Optional[RunSnapshot]:
    """Link the ``background_jobs`` row once; a run that already has a job keeps it (the caller compares ``job_id``)."""
    async with factory() as db:
        row = await _locked(db, tenant_id, conversation_id)
        if row is None:
            return None
        if row.job_id is None:
            row.job_id = _uuid(job_id)
            row.updated_at = _now()
            await db.flush()
        return RunSnapshot.of(row)


async def delete_run(factory: SessionFactory, tenant_id: Any, run_id: Any) -> bool:
    async with factory() as db:
        row = (
            await db.execute(
                select(DeepWorkRun).where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.id == _uuid(run_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True
