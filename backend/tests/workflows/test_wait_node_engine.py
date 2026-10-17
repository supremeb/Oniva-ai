"""U21: wait nodes return a ``wait`` prepare body for the engine's deadline wait (R6, KTD7)."""
import uuid
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.internal_api import workflow_contract as wc
from app.node_executors import NodeExecutionContext, NodeExecutionResult
from app.node_executors import wait_executor
from app.node_executors.wait_executor import WaitExecutor

USER_ID = str(uuid.uuid4())
NY = ZoneInfo("America/New_York")
# 2026-10-17 is a Saturday.
SATURDAY_NOON = datetime(2026, 10, 17, 12, 0, tzinfo=NY)
MONDAY_9AM = datetime(2026, 10, 19, 9, 0, tzinfo=NY)
WEDNESDAY_10AM = datetime(2026, 10, 21, 10, 0, tzinfo=NY)


def ctx(**node_data):
    return NodeExecutionContext(
        node_id="w", node_type="wait", node_data=node_data, inputs={}, variables={}, user_id=USER_ID
    )


def fin(**over):
    return wc.validate_finish_input({"contractVersion": 1, "kind": "wait", "wakeReason": "deadline", **over})


async def prep(**node_data):
    return await WaitExecutor().prepare(ctx(**node_data), MagicMock())


def frozen(monkeypatch, now):
    monkeypatch.setattr(wait_executor, "_now", lambda tz: now.astimezone(tz))


async def test_duration_two_hours_is_a_relative_wait_body():
    body = await prep(waitType="duration", duration=2, durationUnit="hours")
    parsed = wc.validate_prepare_response(body)
    assert parsed.kind == "wait" and parsed.durationMs == 7_200_000
    assert parsed.untilEpochMs is None and parsed.signal is None


@pytest.mark.parametrize("duration", [0, 0.0])
async def test_zero_wait_is_final_immediately(duration):
    result = await prep(waitType="duration", duration=duration, durationUnit="seconds")
    assert isinstance(result, NodeExecutionResult) and result.status == "success"
    assert result.output["wait_seconds"] == 0


async def test_until_time_in_the_past_is_final_with_no_wait(monkeypatch):
    frozen(monkeypatch, WEDNESDAY_10AM)
    result = await prep(waitType="until_time", targetTime="2026-10-20T09:00:00+00:00", timezone="UTC")
    assert isinstance(result, NodeExecutionResult) and result.status == "success"


async def test_until_time_in_the_future_is_an_absolute_epoch(monkeypatch):
    frozen(monkeypatch, WEDNESDAY_10AM)
    target = "2026-10-22T09:00:00+00:00"
    body = await prep(waitType="until_time", targetTime=target, timezone="UTC")
    parsed = wc.validate_prepare_response(body)
    assert parsed.untilEpochMs == int(datetime.fromisoformat(target).timestamp() * 1000)
    assert parsed.durationMs is None


async def test_until_time_without_a_target_fails():
    result = await prep(waitType="until_time")
    assert result.status == "failed" and "No target time" in result.error


async def test_business_hours_outside_hours_waits_for_the_next_window_in_the_node_timezone(monkeypatch):
    frozen(monkeypatch, SATURDAY_NOON)
    body = await prep(waitType="business_hours", timezone="America/New_York")
    parsed = wc.validate_prepare_response(body)
    assert parsed.untilEpochMs == int(MONDAY_9AM.timestamp() * 1000)


async def test_business_hours_inside_hours_is_final(monkeypatch):
    frozen(monkeypatch, WEDNESDAY_10AM)
    result = await prep(waitType="business_hours", timezone="America/New_York")
    assert isinstance(result, NodeExecutionResult) and result.status == "success"
    assert result.output["reason"] == "Already within business hours"


async def test_a_wait_above_max_wait_seconds_fails_with_the_existing_message():
    result = await prep(waitType="duration", duration=2, durationUnit="hours", maxWaitSeconds=3600)
    assert result.status == "failed"
    assert result.error == "Wait duration (7200s) exceeds maximum (3600s)"


async def test_the_default_cap_is_seven_days_and_the_in_process_cap_is_gone():
    assert (await prep(waitType="duration", duration=7, durationUnit="days"))["kind"] == "wait"
    assert (await prep(waitType="duration", duration=8, durationUnit="days")).status == "failed"
    assert not hasattr(wait_executor, "MAX_IN_PROCESS_WAIT_SECONDS")


@pytest.mark.parametrize("duration,unit", [(-5, "seconds"), ("ten", "seconds"), (5, "fortnights"), (True, "seconds")])
async def test_invalid_durations_fail(duration, unit):
    result = await prep(waitType="duration", duration=duration, durationUnit=unit)
    assert result.status == "failed" and "Invalid wait duration" in result.error


async def test_finish_after_a_deadline_wake_succeeds_and_records_the_wake():
    c = ctx(waitType="duration", duration=2, durationUnit="hours")
    result = await WaitExecutor().finish(c, MagicMock(), fin())
    assert result.status == "success"
    assert result.output["output"] == "Wait completed" and result.output["wake_reason"] == "deadline"
    assert result.output["wait_type"] == "duration" and result.output["resumed_at"]
