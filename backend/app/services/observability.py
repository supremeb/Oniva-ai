"""
LangSmith Observability Service

Part of Phase 1: Production Reliability.

Provides LLM observability including:
- Trace logging for all LLM calls
- Token usage tracking
- Latency metrics (p50, p95, p99)
- Error rate monitoring
- Custom feedback scoring
"""

import time
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from functools import wraps
from dataclasses import dataclass, field
from collections import defaultdict, deque
import statistics

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# LangSmith imports - gracefully handle if not installed
try:
    from langsmith import Client
    from langsmith.run_trees import RunTree

    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False
    logger.warning("LangSmith not installed. LLM observability limited.")


@dataclass
class LLMCallMetrics:
    """Metrics for a single LLM call."""
    call_id: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0
    status: str = "success"
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agent_type: Optional[str] = None
    workflow_id: Optional[str] = None
    tenant_id: Optional[str] = None
    # KV-cache metrics (provider prompt caching)
    cache_creation_tokens: int = 0   # Tokens written to cache (Anthropic/OpenAI)
    cache_read_tokens: int = 0       # Tokens read from cache (cache hits)


@dataclass
class ToolExecutionMetrics:
    """Metrics for a single tool execution.

    Security: Does NOT capture tool arguments or return values (PII risk).
    Only captures sizes, not content. Span attributes limited to allowlisted keys.
    """
    tool_name: str
    tool_category: str          # "skill", "agent_tool", "mcp"
    agent_type: str
    latency_ms: float = 0
    status: str = "success"     # "success", "error", "timeout"
    call_id: str = ""           # uuid4, generated per-invocation for trace correlation
    tenant_id: str = ""         # Required for multi-tenant isolation
    error: Optional[str] = None
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    attempt_number: int = 1     # Which retry attempt (1=first, 2=second, etc.)
    session_id: Optional[str] = None
    workflow_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ToolTraceContext:
    """Context object yielded by trace_tool_execution for recording outcome."""

    def __init__(self, call_id: str, attempt_number: int = 1):
        self.call_id = call_id
        self.attempt_number = attempt_number
        self._status = "success"
        self._error: Optional[str] = None
        self._output_size: int = 0

    def set_success(self, output_size: int = 0) -> None:
        self._status = "success"
        self._output_size = output_size

    def set_error(self, error: str) -> None:
        self._status = "error"
        self._error = error


class ObservabilityService:
    """
    Central observability service for LLM and agent monitoring.

    Features:
    - LangSmith integration for trace logging
    - In-memory metrics aggregation
    - Percentile latency calculations
    - Token usage tracking by agent/workflow
    """

    def __init__(self):
        self._client: Optional[Any] = None
        self._metrics: List[LLMCallMetrics] = []
        self._tool_metrics: deque[ToolExecutionMetrics] = deque(maxlen=10000)
        self._max_metrics = 10000  # Rolling window

        # Initialize LangSmith client
        if LANGSMITH_AVAILABLE and settings.LANGCHAIN_API_KEY:
            try:
                self._client = Client(
                    api_key=settings.LANGCHAIN_API_KEY,
                )
                logger.info(f"LangSmith client initialized for project: {settings.LANGCHAIN_PROJECT}")
            except Exception as e:
                logger.error(f"Failed to initialize LangSmith: {e}")

    @property
    def is_enabled(self) -> bool:
        """Check if LangSmith tracing is enabled."""
        return self._client is not None

    def trace_llm_call(
        self,
        provider: str,
        model: str,
        agent_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Decorator to trace LLM calls and log to LangSmith.

        Usage:
            @observability.trace_llm_call(
                provider="openai",
                model="gpt-4",
                agent_type="compliance"
            )
            async def call_llm(prompt: str) -> str:
                ...
        """
        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                call_id = str(uuid4())
                start_time = time.perf_counter()

                # Create run tree for LangSmith
                run_tree = None
                if self._client and LANGSMITH_AVAILABLE:
                    run_tree = RunTree(
                        name=f"llm.{provider}.{model}",
                        run_type="llm",
                        project_name=settings.LANGCHAIN_PROJECT,
                        extra={
                            "provider": provider,
                            "model": model,
                            "agent_type": agent_type,
                            "workflow_id": workflow_id,
                        }
                    )

                metrics = LLMCallMetrics(
                    call_id=call_id,
                    provider=provider,
                    model=model,
                    agent_type=agent_type,
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                )

                try:
                    # Execute LLM call
                    result = await func(*args, **kwargs)

                    # Calculate latency
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    metrics.latency_ms = latency_ms

                    # Extract token usage if available
                    if isinstance(result, dict) and "usage" in result:
                        usage = result["usage"]
                        metrics.input_tokens = usage.get("prompt_tokens", 0)
                        metrics.output_tokens = usage.get("completion_tokens", 0)
                        metrics.total_tokens = usage.get("total_tokens", 0)

                        # Extract cache metrics from provider-specific fields
                        # Anthropic: cache_creation_input_tokens, cache_read_input_tokens
                        # OpenAI: cached_tokens (in prompt_tokens_details)
                        metrics.cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                        metrics.cache_read_tokens = usage.get("cache_read_input_tokens", 0)

                        # OpenAI puts cache info in prompt_tokens_details
                        prompt_details = usage.get("prompt_tokens_details", {})
                        if isinstance(prompt_details, dict):
                            cached = prompt_details.get("cached_tokens", 0)
                            if cached:
                                metrics.cache_read_tokens = cached

                    metrics.status = "success"

                    # Log to LangSmith (no result content — PII risk)
                    if run_tree:
                        run_tree.end(
                            outputs={"status": "success"},
                            extra={
                                "input_tokens": metrics.input_tokens,
                                "output_tokens": metrics.output_tokens,
                                "latency_ms": metrics.latency_ms,
                            }
                        )
                        run_tree.post()

                    # Store metrics
                    self._record_metrics(metrics)

                    return result

                except Exception as e:
                    metrics.status = "error"
                    metrics.error = str(e)
                    metrics.latency_ms = (time.perf_counter() - start_time) * 1000

                    # Log error to LangSmith
                    if run_tree:
                        run_tree.end(error=str(e))
                        run_tree.post()

                    self._record_metrics(metrics)
                    raise

            return wrapper
        return decorator

    def _record_metrics(self, metrics: LLMCallMetrics) -> None:
        """Record metrics and maintain rolling window."""
        self._metrics.append(metrics)

        # Maintain rolling window
        if len(self._metrics) > self._max_metrics:
            self._metrics = self._metrics[-self._max_metrics:]

    # =========================================================================
    # Tool Execution Tracing (Phase A — Agent Observability)
    # =========================================================================

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def trace_tool_execution(
        self,
        tool_name: str,
        tool_category: str,
        agent_type: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        call_id: Optional[str] = None,
        attempt_number: int = 1,
    ):
        """Async context manager that traces tool execution to all enabled backends.

        Security: Only allowlisted keys are recorded (tool_name, latency, status,
        call_id, attempt_number, sizes). No arguments or return values captured.

        Yields a ToolTraceContext for the caller to set_success / set_error.
        Backend dispatch is fire-and-forget via asyncio.create_task (P1 #3).
        """
        cid = call_id or str(uuid4())
        trace = ToolTraceContext(call_id=cid, attempt_number=attempt_number)
        start = time.perf_counter()

        try:
            yield trace
        except Exception:
            # Only set error if caller hasn't already set one
            if trace._status != "error":
                trace.set_error("exception")
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            metrics = ToolExecutionMetrics(
                tool_name=tool_name,
                tool_category=tool_category,
                agent_type=agent_type,
                latency_ms=latency_ms,
                status=trace._status,
                call_id=cid,
                tenant_id=tenant_id or "",
                error=trace._error,
                output_size_bytes=trace._output_size,
                attempt_number=attempt_number,
                session_id=session_id,
            )
            self._tool_metrics.append(metrics)

            # Fire-and-forget dispatch to enabled backends (P1 #3)
            asyncio.create_task(self._dispatch_tool_trace(metrics))

    async def _dispatch_tool_trace(self, metrics: ToolExecutionMetrics) -> None:
        """Dispatch tool trace to enabled observability backends.

        Each dispatch is wrapped in try/except — observability failures
        never surface to the caller.
        """
        # LangSmith
        if self._client and LANGSMITH_AVAILABLE:
            try:
                run_tree = RunTree(
                    name=f"tool.{metrics.tool_category}.{metrics.tool_name}",
                    run_type="tool",
                    project_name=settings.LANGCHAIN_PROJECT,
                    extra={
                        "tool_name": metrics.tool_name,
                        "tool_category": metrics.tool_category,
                        "agent_type": metrics.agent_type,
                        "call_id": metrics.call_id,
                        "attempt_number": metrics.attempt_number,
                        "latency_ms": metrics.latency_ms,
                        "status": metrics.status,
                    },
                )
                if metrics.status == "error":
                    run_tree.end(error=metrics.error)
                else:
                    run_tree.end(
                        outputs={"status": metrics.status, "output_size": metrics.output_size_bytes}
                    )
                run_tree.post()
            except Exception:
                logger.debug("LangSmith tool trace dispatch failed", exc_info=True)

        # Braintrust
        if getattr(settings, 'BRAINTRUST_ENABLED', False):
            try:
                from app.services.braintrust_observability import get_braintrust_service
                bt = get_braintrust_service()
                if bt:
                    await bt.log_tool_execution(metrics)
            except Exception:
                logger.debug("Braintrust tool trace dispatch failed", exc_info=True)

        # Langfuse
        if getattr(settings, 'LANGFUSE_ENABLED', False):
            try:
                from app.services.langfuse_integration import trace_tool_to_langfuse
                trace_tool_to_langfuse(metrics)
            except Exception:
                logger.debug("Langfuse tool trace dispatch failed", exc_info=True)

    def get_tool_metrics(
        self,
        window_minutes: int = 60,
        group_by: str = "tool_name",
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate tool execution metrics for dashboard.

        Args:
            window_minutes: Time window to consider
            group_by: "tool_name", "agent_type", or "tool_category"
            tenant_id: Filter by tenant

        Returns:
            Dict of per-group latency percentiles, error rates, and call counts
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        groups: Dict[str, list] = defaultdict(list)

        for m in self._tool_metrics:
            if m.timestamp < cutoff:
                continue
            if tenant_id and m.tenant_id != tenant_id:
                continue
            key = getattr(m, group_by, "unknown") or "unknown"
            groups[key].append(m)

        result = {}
        for key, metrics_list in groups.items():
            latencies = [m.latency_ms for m in metrics_list]
            errors = sum(1 for m in metrics_list if m.status == "error")
            total = len(metrics_list)
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            result[key] = {
                "calls": total,
                "error_count": errors,
                "error_rate": errors / total if total else 0,
                "latency_p50": sorted_lat[int(n * 0.5)] if n else 0,
                "latency_p95": sorted_lat[int(n * 0.95)] if n else 0,
                "latency_p99": sorted_lat[int(n * 0.99)] if n else 0,
                "latency_mean": statistics.mean(latencies) if latencies else 0,
            }
        return result

    def get_cache_metrics(
        self,
        window_minutes: int = 60,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate KV-cache hit/miss metrics from LLM calls.

        Returns:
            Dict with total_calls, cache_hit_calls, cache_hit_ratio,
            total_cache_read_tokens, total_cache_creation_tokens.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        total_calls = 0
        cache_hit_calls = 0
        total_read = 0
        total_creation = 0

        for m in self._metrics:
            if m.timestamp < cutoff:
                continue
            if tenant_id and m.tenant_id != tenant_id:
                continue
            total_calls += 1
            if m.cache_read_tokens > 0:
                cache_hit_calls += 1
            total_read += m.cache_read_tokens
            total_creation += m.cache_creation_tokens

        return {
            "total_calls": total_calls,
            "cache_hit_calls": cache_hit_calls,
            "cache_hit_ratio": cache_hit_calls / total_calls if total_calls > 0 else 0,
            "total_cache_read_tokens": total_read,
            "total_cache_creation_tokens": total_creation,
        }

    def get_latency_percentiles(
        self,
        window_minutes: int = 60,
        agent_type: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Calculate latency percentiles for recent calls.

        Returns:
            Dict with p50, p95, p99 latency in milliseconds
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        latencies = [
            m.latency_ms for m in self._metrics
            if m.timestamp >= cutoff
            and (agent_type is None or m.agent_type == agent_type)
            and (tenant_id is None or m.tenant_id == tenant_id)
            and m.status == "success"
        ]

        if not latencies:
            return {"p50": 0, "p95": 0, "p99": 0, "count": 0, "mean": 0}

        sorted_latencies = sorted(latencies)
        n = len(sorted_latencies)

        return {
            "p50": sorted_latencies[int(n * 0.5)] if n > 0 else 0,
            "p95": sorted_latencies[int(n * 0.95)] if n > 0 else 0,
            "p99": sorted_latencies[int(n * 0.99)] if n > 0 else 0,
            "count": n,
            "mean": statistics.mean(latencies) if latencies else 0,
        }

    def get_token_usage(
        self,
        window_minutes: int = 60,
        group_by: str = "agent_type",
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, int]]:
        """
        Get token usage aggregated by agent type or model.

        Args:
            window_minutes: Time window to consider
            group_by: "agent_type" or "model"
            tenant_id: Filter by tenant (required for REST exposure)

        Returns:
            Dict of token usage per group
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        usage: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
        )

        for m in self._metrics:
            if m.timestamp < cutoff:
                continue
            if tenant_id is not None and m.tenant_id != tenant_id:
                continue

            key = getattr(m, group_by, "unknown") or "unknown"
            usage[key]["input_tokens"] += m.input_tokens
            usage[key]["output_tokens"] += m.output_tokens
            usage[key]["total_tokens"] += m.total_tokens
            usage[key]["calls"] += 1

        return dict(usage)

    def get_error_rate(
        self,
        window_minutes: int = 60,
        agent_type: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate error rate for recent calls.

        Returns:
            Dict with error count, total count, and error rate
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        relevant = [
            m for m in self._metrics
            if m.timestamp >= cutoff
            and (agent_type is None or m.agent_type == agent_type)
            and (tenant_id is None or m.tenant_id == tenant_id)
        ]

        total = len(relevant)
        errors = sum(1 for m in relevant if m.status == "error")

        return {
            "total_calls": total,
            "error_count": errors,
            "error_rate": errors / total if total > 0 else 0,
            "success_rate": (total - errors) / total if total > 0 else 1,
        }

    def get_tool_success_rate(
        self,
        window_minutes: int = 60,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Calculate tool call success rate by agent type.

        Note: This is a placeholder - actual tool call tracking
        would need to be added to the agent framework.
        """
        # For now, return success rate based on LLM call success
        rates = {}

        for agent_type in set(m.agent_type for m in self._metrics if m.agent_type):
            stats = self.get_error_rate(window_minutes, agent_type, tenant_id=tenant_id)
            rates[agent_type] = stats["success_rate"]

        return rates

    def get_dashboard_metrics(
        self,
        window_minutes: int = 60,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all metrics for dashboard display.

        Returns comprehensive metrics including:
        - Latency percentiles
        - Token usage by agent
        - Error rates
        - Call counts
        """
        return {
            "latency": self.get_latency_percentiles(window_minutes, tenant_id=tenant_id),
            "token_usage": self.get_token_usage(window_minutes, "agent_type", tenant_id=tenant_id),
            "token_usage_by_model": self.get_token_usage(window_minutes, "model", tenant_id=tenant_id),
            "error_rate": self.get_error_rate(window_minutes, tenant_id=tenant_id),
            "tool_success_rate": self.get_tool_success_rate(window_minutes, tenant_id=tenant_id),
            "window_minutes": window_minutes,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def submit_feedback(
        self,
        run_id: str,
        score: float,
        comment: Optional[str] = None,
        feedback_type: str = "user",
    ) -> bool:
        """
        Submit feedback for a LangSmith run.

        Args:
            run_id: The LangSmith run ID
            score: Feedback score (0-1)
            comment: Optional feedback comment
            feedback_type: Type of feedback ("user", "system", "evaluation")

        Returns:
            True if feedback was submitted successfully
        """
        if not self._client:
            logger.warning("LangSmith not enabled, feedback not submitted")
            return False

        try:
            self._client.create_feedback(
                run_id=run_id,
                key=feedback_type,
                score=score,
                comment=comment,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to submit feedback: {e}")
            return False


# Global observability service instance
_observability_service: Optional[ObservabilityService] = None


def get_observability_service() -> ObservabilityService:
    """Get or create the global observability service."""
    global _observability_service
    if _observability_service is None:
        _observability_service = ObservabilityService()
    return _observability_service


# Convenience decorators
def trace_llm(
    provider: str,
    model: str,
    agent_type: Optional[str] = None,
):
    """
    Convenience decorator for tracing LLM calls.

    Usage:
        @trace_llm("openai", "gpt-4", "compliance")
        async def call_openai(prompt: str) -> str:
            ...
    """
    service = get_observability_service()
    return service.trace_llm_call(provider, model, agent_type)
