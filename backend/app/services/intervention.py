"""Intervention service for live agent message injection.

Allows operators to inject messages into running background agents.
Messages are stored in a Redis list queue keyed by job_id, enabling
reliable cross-worker delivery in multi-process deployments.

Redis key pattern: fleet:intervention:{job_id}
  - send:  LPUSH serialized payload + EXPIRE 3600s
  - check: LRANGE 0 -1 (reversed for enqueue order) + DELETE
  - clear: DELETE

The agent executor checks for pending interventions before each iteration.
"""

import json
import logging
from datetime import datetime, timezone

from app.services.redis_ha import get_redis_service

logger = logging.getLogger(__name__)

_KEY_PREFIX = "fleet:intervention"
_TTL_SECONDS = 3600  # 1 hour — auto-evicts stale keys for crashed jobs


def _key(job_id: str) -> str:
    return f"{_KEY_PREFIX}:{job_id}"


async def send_intervention(job_id: str, message: str, sender: str) -> dict:
    """Send an intervention message to a running agent.

    Args:
        job_id: BackgroundJob UUID (string)
        message: The intervention message text
        sender: Who sent it (email or user_id)

    Returns:
        Dict with intervention details

    Raises:
        redis.exceptions.RedisError: If Redis is unavailable (caller handles).
    """
    intervention = {
        "message": message,
        "sender": sender,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    redis = await get_redis_service()
    key = _key(job_id)
    await redis.lpush(key, json.dumps(intervention))
    await redis.expire(key, _TTL_SECONDS)

    logger.info(f"Intervention queued for job {job_id} from {sender}")
    return intervention


async def check_interventions(job_id: str) -> list[dict]:
    """Check and consume pending interventions for a job.

    Called by the agent executor before each iteration.
    Returns messages in send order and clears the queue.
    """
    redis = await get_redis_service()
    key = _key(job_id)

    # Read all then delete — use pipeline for atomicity
    async with redis.pipeline() as pipe:
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await redis.execute_pipeline(pipe)

    raw_messages = results[0] if results and len(results) > 0 else []
    if not raw_messages:
        return []

    # LPUSH is LIFO — reverse to get enqueue order
    messages = [json.loads(m) for m in reversed(raw_messages)]

    logger.info(f"Agent {job_id} received {len(messages)} intervention(s)")
    return messages


async def clear_interventions(job_id: str) -> None:
    """Clear all pending interventions for a job (on completion/failure)."""
    redis = await get_redis_service()
    await redis.delete(_key(job_id))
