"""EventSink protocol for Deep Work Engine event dispatch.

Decouples the engine from asyncio.Queue so it can run in both:
- Live mode (SSEQueueSink → streams to connected client)
- Headless mode (PersistentTraceSink → writes to BackgroundJob.execution_trace)

Usage:
    # Live (existing behavior):
    sink = SSEQueueSink(sse_queue)
    engine = DeepWorkEngine(..., event_sink=sink)

    # Headless (background agents):
    sink = PersistentTraceSink(job_id, db_session)
    engine = DeepWorkEngine(..., event_sink=sink)
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


@runtime_checkable
class EventSink(Protocol):
    """Protocol for dispatching Deep Work Engine events."""

    async def emit(self, event_type: str, data: dict) -> None:
        """Emit an event. Implementations decide where it goes."""
        ...


class SSEQueueSink:
    """Wraps an asyncio.Queue for live SSE streaming (existing behavior)."""

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    async def emit(self, event_type: str, data: dict) -> None:
        try:
            self._queue.put_nowait({
                "id": str(uuid4()),
                "type": event_type,
                "data": data,
                "timestamp": time.time(),
            })
        except asyncio.QueueFull:
            logger.warning(f"SSE queue full, dropping event: {event_type}")


class PersistentTraceSink:
    """Writes events to BackgroundJob.execution_trace for headless background execution.

    Events are accumulated in memory and flushed periodically or on completion.
    This avoids DB writes on every single event (which could be hundreds per run).
    """

    def __init__(self, job_id: UUID | str, max_buffer: int = 50):
        self._job_id = str(job_id)
        self._buffer: list[dict] = []
        self._max_buffer = max_buffer

    async def emit(self, event_type: str, data: dict) -> None:
        self._buffer.append({
            "id": str(uuid4()),
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Don't flush here — let the engine flush at completion
        # This keeps the sink independent of DB sessions

    def get_trace(self) -> list[dict]:
        """Get all accumulated events for persisting to BackgroundJob."""
        return self._buffer.copy()

    def clear(self) -> None:
        """Clear the buffer after persisting."""
        self._buffer.clear()


class NullSink:
    """Discards all events. For testing or when no output is needed."""

    async def emit(self, event_type: str, data: dict) -> None:
        pass
