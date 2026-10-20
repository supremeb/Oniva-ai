"""Run handler registry (plan 003, U5): one place where every kind of engine run registers its hooks.

A *kind* is a family of domain rows that run as engine work (``fleet``, ``workflow``, ``batch``,
``scheduled``). Hooks are registered separately, possibly by different modules, and are used by
two callers: ``POST /internal/runs/finished`` (the engine's push) and the reconciler (the safety
net for a push that never arrived or a start that never happened).

``on_finished(ref, snapshot)``  apply a settled engine run to the domain row. Idempotent. Never moves a terminal row.
``start_missing(ref)``          the row exists but the engine run was never started; re-issue the start with the
                                same deterministic id and idempotency key. Returns a ``StartResult``.
``find_stuck(session, older_than)``  rows the reconciler should look at.
``fail(ref, code)``             terminal failure: the engine conversation is missing or start retries ran out.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

HOOKS = ("on_finished", "start_missing", "find_stuck", "fail")

# Modules that register hooks. Each later unit adds its module here (U7 fleet_engine, U9
# fleet_run_sync, U16 workflow_run_sync, U18 workflow_batch_engine, U19 scheduled_run_hooks).
BOOTSTRAP_MODULES: tuple[str, ...] = (
    "app.services.fleet_engine",
    "app.services.fleet_run_sync",
    "app.services.workflow_batch_engine",  # U18: batch on_finished, start_missing, find_stuck, fail
    "app.services.workflow_engine_runs",  # U15: start_missing, find_stuck, fail
    "app.services.workflow_run_sync",  # U16: on_finished
    "app.services.scheduled_run_hooks",  # U19: scheduled start_missing, find_stuck, fail
)


@dataclass(frozen=True)
class RunRef:
    """A domain row that is (or should be) an engine run."""

    kind: str
    ref_id: UUID
    tenant_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    session_id: Optional[str] = None
    conversation_id: Optional[UUID] = None
    # The engine's root task id when the domain row recorded it.
    task_id: Optional[str] = None
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def with_binding(self, **changes: Any) -> "RunRef":
        return replace(self, **changes)


@dataclass(frozen=True)
class StartResult:
    status: str  # "started" | "already_started" | "failed"
    error: Optional[str] = None
    # The engine's root task id when the start (or the idempotent repeat) returned it.
    task_id: Optional[str] = None
    # The handler's persisted attempt counter after this try; lets the bound survive a restart.
    attempts: Optional[int] = None

    @classmethod
    def started(cls, task_id: Optional[str] = None, attempts: Optional[int] = None) -> "StartResult":
        return cls("started", None, task_id, attempts)

    @classmethod
    def already_started(cls, task_id: Optional[str] = None) -> "StartResult":
        return cls("already_started", None, task_id)

    @classmethod
    def failed(cls, error: str, attempts: Optional[int] = None) -> "StartResult":
        return cls("failed", error, None, attempts)


OnFinished = Callable[[RunRef, dict], Awaitable[None]]
StartMissing = Callable[[RunRef], Awaitable[StartResult]]
FindStuck = Callable[[Any, timedelta], Awaitable[list]]
Fail = Callable[[RunRef, str], Awaitable[None]]


class RunHandlerError(Exception):
    pass


_registry: dict[str, dict[str, Callable]] = {}


def register(kind: str, *, on_finished: Optional[OnFinished] = None,
             start_missing: Optional[StartMissing] = None, find_stuck: Optional[FindStuck] = None,
             fail: Optional[Fail] = None) -> None:
    """Register hooks for a kind. Separate calls for one kind merge; a hook registered twice raises."""
    given = {"on_finished": on_finished, "start_missing": start_missing,
             "find_stuck": find_stuck, "fail": fail}
    hooks = {name: fn for name, fn in given.items() if fn is not None}
    if not hooks:
        raise RunHandlerError(f"register({kind!r}) called with no hooks")
    current = _registry.get(kind, {})
    dup = sorted(set(hooks) & set(current))
    if dup:
        raise RunHandlerError(f"run kind {kind!r} already has hook(s): {', '.join(dup)}")
    _registry[kind] = {**current, **hooks}


def hook(kind: str, name: str) -> Optional[Callable]:
    return _registry.get(kind, {}).get(name)


def kinds() -> list[str]:
    return sorted(_registry)


def reset() -> None:
    """Forget every registration (tests)."""
    _registry.clear()


def missing_on_finished() -> list[str]:
    return sorted(k for k, hooks in _registry.items() if "on_finished" not in hooks)


def bootstrap(modules: Optional[tuple[str, ...]] = None) -> list[str]:
    """Import every module that registers hooks, then report any kind that cannot settle a run.

    Idempotent: importing a module twice runs its registration once. Returns the registered kinds.
    """
    for name in (BOOTSTRAP_MODULES if modules is None else modules):
        importlib.import_module(name)
    bad = missing_on_finished()
    if bad:
        # Not fatal at boot (units land one hook at a time); tests assert it is empty.
        logger.error("run kind(s) without an on_finished hook: %s", ", ".join(bad))
    return kinds()
