"""
Classification Audit Worker for Oniva Security Foundation.

Drains classification audit events from Redis queue to PostgreSQL.
Run as: python -m app.workers.classification_audit_worker

This worker provides non-blocking audit logging for classification events:
- EXPORT_BLOCKED: Logged when CONFIDENTIAL data is blocked from external APIs
- EXPORT_ALLOWED: Logged when data is allowed to external APIs
- CLASSIFICATION_ASSIGNED: Logged when classification is assigned to data
- CLASSIFICATION_PROPAGATED: Logged when classification propagates through workflow

Design:
- Events are pushed to Redis list "audit:classification" by executors
- Worker pops events and inserts into ComplianceAuditLog table
- Uses BLPOP for efficient blocking wait
- Handles graceful shutdown via SIGTERM/SIGINT
- Auto-reconnects on Redis/Postgres connection failures

Usage:
    # Run as standalone worker
    python -m app.workers.classification_audit_worker

    # Or use with a process manager like supervisord
    [program:classification_audit_worker]
    command=python -m app.workers.classification_audit_worker
    directory=/app/backend
    autostart=true
    autorestart=true
"""
import asyncio
import json
import logging
import signal
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.compliance_audit_log import ComplianceAuditLog, AuditAction

logger = logging.getLogger(__name__)

# Graceful shutdown flag
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


class ClassificationAuditWorker:
    """
    Worker that drains classification audit events from Redis to PostgreSQL.

    Events are stored in the ComplianceAuditLog table for long-term retention
    and regulatory compliance.
    """

    QUEUE_NAME = "audit:classification"
    BATCH_SIZE = 100  # Process up to 100 events before committing
    POLL_TIMEOUT = 5  # Seconds to wait for new events

    def __init__(self):
        self.settings = get_settings()
        self._redis: redis.Redis = None
        self._db_engine = None
        self._async_session = None
        self._events_processed = 0
        self._errors_count = 0

    async def connect(self):
        """Initialize Redis and PostgreSQL connections."""
        # Redis connection
        self._redis = redis.from_url(
            self.settings.REDIS_URL,
            decode_responses=True,
        )

        # PostgreSQL connection
        self._db_engine = create_async_engine(
            self.settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
        )
        self._async_session = sessionmaker(
            self._db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info("Connected to Redis and PostgreSQL")

    async def disconnect(self):
        """Close connections gracefully."""
        if self._redis:
            await self._redis.close()
            logger.info("Redis connection closed")

        if self._db_engine:
            await self._db_engine.dispose()
            logger.info("PostgreSQL connection closed")

    async def process_event(self, event_json: str, session: AsyncSession) -> bool:
        """
        Process a single audit event and insert into database.

        Args:
            event_json: JSON string of the audit event
            session: Database session

        Returns:
            True if processed successfully, False on error
        """
        try:
            event = json.loads(event_json)

            # Map event type to AuditAction
            event_type = event.get("event_type", "EXPORT_BLOCKED")
            action_map = {
                "EXPORT_BLOCKED": AuditAction.EXPORT_BLOCKED,
                "EXPORT_ALLOWED": AuditAction.EXPORT_ALLOWED,
                "CLASSIFICATION_ASSIGNED": AuditAction.CLASSIFICATION_ASSIGNED,
                "CLASSIFICATION_PROPAGATED": AuditAction.CLASSIFICATION_PROPAGATED,
                "CLASSIFICATION_DOWNGRADE_REQUESTED": AuditAction.CLASSIFICATION_DOWNGRADE_REQUESTED,
            }
            action = action_map.get(event_type, AuditAction.EXPORT_BLOCKED)

            # Calculate retention date (7 years)
            retention_until = datetime.utcnow() + timedelta(days=7 * 365)

            # Build audit log entry
            # Note: We use a simplified entry for classification events
            # (no RSA signature since these are high-volume, low-risk events)
            audit_entry = ComplianceAuditLog(
                id=uuid4(),
                tenant_id=UUID(event["tenant_id"]) if event.get("tenant_id") else None,
                agent_id=event.get("node_type", "system"),
                user_id=UUID(event["user_id"]) if event.get("user_id") else None,
                session_id=UUID(event["session_id"]) if event.get("session_id") else None,
                workflow_id=UUID(event["workflow_id"]) if event.get("workflow_id") else None,
                action=action.value,
                decision=f"{event_type}: {event.get('classification', 'unknown')}",
                rationale=f"Node {event.get('node_id', 'unknown')} of type {event.get('node_type', 'unknown')}",
                data_sources=[],
                workflow_state=event.get("workflow_state"),
                tools_called=[event.get("node_type")] if event.get("node_type") else [],
                signature="async_worker",  # No RSA for performance
                previous_hash=None,
                entry_hash=f"async_{uuid4().hex[:16]}",  # Simplified hash
                extra_metadata=event,
                retention_until=retention_until,
            )

            session.add(audit_entry)
            return True

        except Exception as e:
            logger.error(f"Failed to process audit event: {e}")
            logger.debug(f"Event data: {event_json}")
            return False

    async def drain_queue(self):
        """
        Drain events from Redis queue to PostgreSQL.

        Uses batching for efficiency - commits after BATCH_SIZE events
        or POLL_TIMEOUT seconds of waiting.
        """
        async with self._async_session() as session:
            batch_count = 0

            while not _shutdown_requested:
                try:
                    # BLPOP with timeout for efficient blocking
                    result = await self._redis.blpop(
                        self.QUEUE_NAME,
                        timeout=self.POLL_TIMEOUT,
                    )

                    if result:
                        _, event_json = result
                        if await self.process_event(event_json, session):
                            batch_count += 1
                            self._events_processed += 1
                        else:
                            self._errors_count += 1

                        # Commit batch
                        if batch_count >= self.BATCH_SIZE:
                            await session.commit()
                            logger.info(f"Committed batch of {batch_count} events")
                            batch_count = 0

                    else:
                        # Timeout - commit any pending events
                        if batch_count > 0:
                            await session.commit()
                            logger.debug(f"Committed {batch_count} events (timeout)")
                            batch_count = 0

                except redis.ConnectionError as e:
                    logger.error(f"Redis connection error: {e}")
                    await asyncio.sleep(5)  # Wait before retry

                except Exception as e:
                    logger.error(f"Error in drain loop: {e}")
                    self._errors_count += 1
                    await asyncio.sleep(1)

            # Commit any remaining events on shutdown
            if batch_count > 0:
                await session.commit()
                logger.info(f"Final commit of {batch_count} events before shutdown")

    async def run(self):
        """Main entry point for the worker."""
        logger.info("Starting Classification Audit Worker")

        try:
            await self.connect()
            await self.drain_queue()
        finally:
            logger.info(
                f"Worker shutting down. "
                f"Processed: {self._events_processed}, Errors: {self._errors_count}"
            )
            await self.disconnect()

    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "events_processed": self._events_processed,
            "errors_count": self._errors_count,
            "queue_name": self.QUEUE_NAME,
        }


async def main():
    """Run the classification audit worker."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Run worker
    worker = ClassificationAuditWorker()
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
