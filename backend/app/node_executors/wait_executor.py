"""
Wait/Delay Node Executor
Parks a workflow node for a duration, until a time, or until the next business window.

Engine runs (plan 003 U21, KTD7): ``prepare`` returns a ``wait`` body and the engine's durable deadline wait
does the waiting, so there is no in-process cap. Durations are relative (the engine adds them to its own
clock); ``until_time`` and ``business_hours`` are computed here, where the timezone logic lives, and sent as
an absolute epoch. ``maxWaitSeconds`` (default 7 days) stays as a validation error.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult, PrepareResult

DEFAULT_MAX_WAIT_SECONDS = 604800  # 7 days

# The legacy in-process orchestrator (removed by U30) still sleeps inside ``execute``; it keeps its old limit.
LEGACY_IN_PROCESS_WAIT_SECONDS = 300

_MULTIPLIERS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def _now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


class _Invalid(Exception):
    """A configuration the node refuses; the message is the node's error."""


class WaitPlan:
    """What a wait node asks for: relative ``duration_s`` or absolute ``until`` (an aware datetime)."""

    def __init__(self, wait_type: str, seconds: float, reason: str, until: Optional[datetime] = None) -> None:
        self.wait_type = wait_type
        self.seconds = seconds
        self.reason = reason
        self.until = until


def plan_wait(node_data: Dict[str, Any]) -> WaitPlan:
    """Pure: the node's wait, or ``_Invalid``. Reads the clock only through ``_now``."""
    wait_type = node_data.get("waitType", "duration")

    if wait_type == "duration":
        duration = node_data.get("duration", 0)
        unit = node_data.get("durationUnit", "seconds")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
            or unit not in _MULTIPLIERS
        ):
            raise _Invalid(f"Invalid wait duration: {duration!r} {unit!r}")
        return WaitPlan(wait_type, duration * _MULTIPLIERS[unit], f"Waiting for {duration} {unit}")

    if wait_type == "until_time":
        target_time_str = node_data.get("targetTime")
        timezone_str = node_data.get("timezone", "UTC")
        if not target_time_str:
            raise _Invalid("No target time specified")
        try:
            tz = ZoneInfo(timezone_str)
            target = datetime.fromisoformat(target_time_str.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=tz)
            seconds = max(0, (target - _now(tz)).total_seconds())
        except Exception as e:
            raise _Invalid(f"Invalid time format: {str(e)}")
        return WaitPlan(wait_type, seconds, f"Waiting until {target.isoformat()}", target)

    if wait_type == "business_hours":
        config = node_data.get("businessHoursConfig", {})
        start_hour = config.get("startHour", 9)
        end_hour = config.get("endHour", 17)
        weekdays = config.get("weekdays", [0, 1, 2, 3, 4])  # Mon-Fri
        try:
            tz = ZoneInfo(node_data.get("timezone", "UTC"))
            now = _now(tz)
            if now.weekday() in weekdays and start_hour <= now.hour < end_hour:
                return WaitPlan(wait_type, 0, "Already within business hours")
            next_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            if now.hour >= end_hour or now.weekday() not in weekdays:
                next_start += timedelta(days=1)
            while next_start.weekday() not in weekdays:
                next_start += timedelta(days=1)
            seconds = max(0, (next_start - now).total_seconds())
        except Exception as e:
            raise _Invalid(f"Business hours calculation failed: {str(e)}")
        return WaitPlan(wait_type, seconds, f"Waiting until business hours: {next_start.isoformat()}", next_start)

    return WaitPlan(wait_type, 0, "")


def check_cap(node_data: Dict[str, Any], seconds: float) -> None:
    max_wait = node_data.get("maxWaitSeconds", DEFAULT_MAX_WAIT_SECONDS)
    if seconds > max_wait:
        raise _Invalid(f"Wait duration ({seconds}s) exceeds maximum ({max_wait}s)")


def _epoch_ms(moment: datetime) -> int:
    return int(moment.astimezone(timezone.utc).timestamp() * 1000)


class WaitExecutor(BaseNodeExecutor):
    """Executor for Wait/Delay node - pauses workflow execution"""

    node_type = "wait"

    def _result(self, context: NodeExecutionContext, plan: WaitPlan, started: float, **extra: Any) -> NodeExecutionResult:
        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={
                "output": "Wait completed",
                "wait_type": plan.wait_type,
                "wait_seconds": plan.seconds,
                "reason": plan.reason,
                "resumed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                **extra,
            },
            elapsed_ms=int((time.time() - started) * 1000),
        )

    def _failed(self, context: NodeExecutionContext, message: str, started: float) -> NodeExecutionResult:
        return NodeExecutionResult(
            node_id=context.node_id, status="failed", error=message, elapsed_ms=int((time.time() - started) * 1000)
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        started = time.time()
        try:
            plan = plan_wait(context.node_data)
            check_cap(context.node_data, plan.seconds)
        except _Invalid as exc:
            return self._failed(context, str(exc), started)
        if plan.seconds <= 0:
            return self._result(context, plan, started)  # nothing to wait for: a final, no engine wait
        if plan.until is not None:
            return PrepareResult.wait(until_epoch_ms=_epoch_ms(plan.until))
        return PrepareResult.wait(duration_ms=max(1, int(plan.seconds * 1000)))

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        """The wake: the deadline passed (a plain wait has no signal). The plan is recomputed for the output only."""
        started = time.time()
        try:
            plan = plan_wait(context.node_data)
        except _Invalid as exc:
            return self._failed(context, str(exc), started)
        wake = getattr(finish_input, "wakeReason", None) or "deadline"
        if plan.wait_type != "duration":
            plan = WaitPlan(plan.wait_type, 0, plan.reason, plan.until)  # the wake time has passed
        return self._result(context, plan, started, wake_reason=wake)

    async def execute(self, context: NodeExecutionContext, db: AsyncSession) -> NodeExecutionResult:
        """Legacy in-process path (removed by U30): sleeps, within ``LEGACY_IN_PROCESS_WAIT_SECONDS``."""
        started = time.time()
        try:
            plan = plan_wait(context.node_data)
            check_cap(context.node_data, plan.seconds)
            if plan.seconds > LEGACY_IN_PROCESS_WAIT_SECONDS:
                raise _Invalid(
                    f"Wait of {plan.seconds:.0f}s exceeds the {LEGACY_IN_PROCESS_WAIT_SECONDS}s "
                    "this node can honor in-process; durable long waits are not supported yet"
                )
            if plan.seconds > 0:
                await asyncio.sleep(plan.seconds)
            return self._result(context, plan, started)
        except _Invalid as exc:
            return self._failed(context, str(exc), started)
        except Exception as e:
            return self._failed(context, str(e), started)


__all__: Tuple[str, ...] = ("WaitExecutor", "plan_wait", "check_cap")
