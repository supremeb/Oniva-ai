"""Feature-flagged Langfuse integration.

When LANGFUSE_ENABLED=true, LLM calls are traced to Langfuse
for deep trace exploration and prompt playground.

Multi-tenant note: All tenants share one Langfuse project when
configured via env vars. Per-tenant isolation requires Phase 2.
"""
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_langfuse_client():
    """Thread-safe lazy singleton via lru_cache."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.LANGFUSE_ENABLED:
        return None
    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info(f"Langfuse client initialized (host: {settings.LANGFUSE_HOST})")
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse: {e}")
        return None


def shutdown_langfuse() -> None:
    """Flush and close Langfuse client. Call from FastAPI lifespan shutdown."""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
        except Exception:
            pass
    get_langfuse_client.cache_clear()


def trace_to_langfuse(
    call_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    status: str,
    workflow_id: str | None = None,
    agent_type: str | None = None,
) -> None:
    """Log a trace to Langfuse if enabled. Fire-and-forget."""
    client = get_langfuse_client()
    if not client:
        return
    try:
        trace = client.trace(id=call_id, name=f"{provider}/{model}")
        trace.generation(
            name=f"llm.{provider}",
            model=model,
            usage={"input": input_tokens, "output": output_tokens},
            metadata={
                "workflow_id": workflow_id,
                "agent_type": agent_type,
                "latency_ms": latency_ms,
            },
            status_message=status,
        )
    except Exception as e:
        logger.warning(f"Langfuse trace failed (non-blocking): {e}")
