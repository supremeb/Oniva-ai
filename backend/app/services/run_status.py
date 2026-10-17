"""One run status enum for Fleet jobs, workflow executions and engine tasks (plan 003, KTD10).

The legacy mapping lives here and in ``frontend/lib/run/status.ts``; both are asserted against
``tests/fixtures/run_status_map.json``.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

RUN_STATUSES = (
    "queued", "running", "waiting", "resuming", "completed", "failed", "cancelled", "skipped",
    "interrupted",
)

_LEGACY = {
    "pending": "queued",
    "queued": "queued",
    "running": "running",
    "waiting": "waiting",
    "resuming": "resuming",
    "completed": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "skipped": "skipped",
    "interrupted": "interrupted",
}

_ENGINE_TASK = {"pending": "queued", "running": "running", "completing": "running", "waiting": "waiting"}
_ENGINE_OUTCOME = {"done": "completed", "aborted": "cancelled", "faulted": "failed"}


def normalize(legacy_value: Any, source: str = "") -> str:
    """Map a legacy job/execution status to the enum. Unknown values become ``failed`` (logged)."""
    mapped = _LEGACY.get(legacy_value.strip().lower()) if isinstance(legacy_value, str) else None
    if mapped is None:
        logger.warning("unknown run status %r from %s", legacy_value, source or "unknown source")
        return "failed"
    return mapped


def from_engine_task(status: str, wake: Optional[Any] = None, resumed: bool = False,
                     outcome: Optional[Any] = None) -> str:
    """Map an engine task state to the enum.

    ``wake`` (what a waiting task waits for) does not change the result: every wait reads as
    ``waiting``. ``resumed`` marks a run the engine restarted; it reads ``resuming`` until progress lands.
    ``outcome`` is the terminal outcome type (or the outcome object).
    """
    if status == "terminal":
        kind = outcome.get("type") if isinstance(outcome, dict) else outcome
        mapped = _ENGINE_OUTCOME.get(kind) if isinstance(kind, str) else None
        if mapped is None:
            logger.warning("unknown engine task outcome %r", outcome)
            return "failed"
        return mapped
    mapped = _ENGINE_TASK.get(status)
    if mapped is None:
        logger.warning("unknown engine task status %r", status)
        return "failed"
    if resumed and mapped == "running":
        return "resuming"
    return mapped
