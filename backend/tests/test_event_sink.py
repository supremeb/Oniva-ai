"""Unit tests for EventSink implementations.

Covers:
- SSEQueueSink produces correct {id, type, data, timestamp} envelope
- PersistentTraceSink get_trace() returns events with all 4 fields
- NullSink discards events silently
- SSEQueueSink handles QueueFull gracefully
"""

import asyncio
import pytest

from app.services.deep_work.event_sink import (
    EventSink,
    NullSink,
    PersistentTraceSink,
    SSEQueueSink,
)


# ---------------------------------------------------------------------------
# SSEQueueSink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_queue_sink_envelope_format():
    """Emitted envelope must have id, type, data, and timestamp keys."""
    queue: asyncio.Queue = asyncio.Queue()
    sink = SSEQueueSink(queue)

    await sink.emit("test_event", {"key": "value"})

    envelope = queue.get_nowait()
    assert set(envelope.keys()) == {"id", "type", "data", "timestamp"}
    assert envelope["type"] == "test_event"
    assert envelope["data"] == {"key": "value"}
    assert isinstance(envelope["id"], str) and len(envelope["id"]) == 36  # UUID4
    assert isinstance(envelope["timestamp"], float)


@pytest.mark.asyncio
async def test_sse_queue_sink_unique_ids():
    """Each emitted event must receive a unique id."""
    queue: asyncio.Queue = asyncio.Queue()
    sink = SSEQueueSink(queue)

    await sink.emit("event_a", {})
    await sink.emit("event_b", {})

    id_a = queue.get_nowait()["id"]
    id_b = queue.get_nowait()["id"]
    assert id_a != id_b


@pytest.mark.asyncio
async def test_sse_queue_sink_queue_full_does_not_raise():
    """When the queue is full, emit must swallow QueueFull and not raise."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    sink = SSEQueueSink(queue)

    await sink.emit("first", {})   # fills the queue
    # Second emit must not raise even though queue is full
    await sink.emit("second", {})  # should be dropped silently

    # Only the first event should be in the queue
    assert queue.qsize() == 1
    assert queue.get_nowait()["type"] == "first"


# ---------------------------------------------------------------------------
# PersistentTraceSink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_trace_sink_envelope_format():
    """get_trace() events must have id, type, data, and timestamp fields."""
    sink = PersistentTraceSink(job_id="test-job-123")

    await sink.emit("phase_start", {"phase": "planning"})

    trace = sink.get_trace()
    assert len(trace) == 1
    event = trace[0]
    assert set(event.keys()) == {"id", "type", "data", "timestamp"}
    assert event["type"] == "phase_start"
    assert event["data"] == {"phase": "planning"}
    assert isinstance(event["id"], str) and len(event["id"]) == 36
    assert isinstance(event["timestamp"], str)  # ISO-format string


@pytest.mark.asyncio
async def test_persistent_trace_sink_accumulates_events():
    """Multiple emits must all appear in get_trace()."""
    sink = PersistentTraceSink(job_id="test-job-456")

    await sink.emit("event_1", {"n": 1})
    await sink.emit("event_2", {"n": 2})
    await sink.emit("event_3", {"n": 3})

    trace = sink.get_trace()
    assert len(trace) == 3
    assert [e["type"] for e in trace] == ["event_1", "event_2", "event_3"]


@pytest.mark.asyncio
async def test_persistent_trace_sink_get_trace_returns_copy():
    """get_trace() must return an independent copy; mutating it must not affect the buffer."""
    sink = PersistentTraceSink(job_id="test-job-789")

    await sink.emit("original", {})
    trace = sink.get_trace()
    trace.clear()  # mutate the returned copy

    assert len(sink.get_trace()) == 1  # internal buffer unchanged


@pytest.mark.asyncio
async def test_persistent_trace_sink_clear():
    """clear() must empty the internal buffer."""
    sink = PersistentTraceSink(job_id="test-job-clear")

    await sink.emit("event", {})
    assert len(sink.get_trace()) == 1

    sink.clear()
    assert sink.get_trace() == []


# ---------------------------------------------------------------------------
# NullSink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_sink_discards_silently():
    """NullSink.emit must complete without raising and produce no side-effects."""
    sink = NullSink()
    # Must not raise for any input
    await sink.emit("any_event", {"payload": 42})
    await sink.emit("another_event", {})


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_all_sinks_implement_event_sink_protocol():
    """All sink classes must satisfy the EventSink runtime-checkable protocol."""
    queue: asyncio.Queue = asyncio.Queue()
    assert isinstance(SSEQueueSink(queue), EventSink)
    assert isinstance(PersistentTraceSink(job_id="x"), EventSink)
    assert isinstance(NullSink(), EventSink)
