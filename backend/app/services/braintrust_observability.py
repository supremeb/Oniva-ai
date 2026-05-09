"""
Braintrust Observability Service

Part of Phase 2: P2.4 Observability Stack.

Provides enterprise-grade AI observability through Braintrust including:
- Automatic LLM call tracing with token counts
- Evaluation scoring and feedback
- Cost tracking per model/provider
- Production monitoring with alerts
- Integration with existing OpenTelemetry setup

This service complements LangSmith (observability.py) by adding:
- Built-in evaluation capabilities
- Better dashboard UI
- Automatic scoring
- Production alerting
"""

import time
import logging
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime
from functools import wraps
from uuid import uuid4
from dataclasses import dataclass, field

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Braintrust imports - gracefully handle if not installed
try:
    import braintrust
    from braintrust import traced, init_logger, wrap_openai, Eval
    from braintrust.otel import BraintrustSpanProcessor

    BRAINTRUST_AVAILABLE = True
except ImportError:
    BRAINTRUST_AVAILABLE = False
    logger.warning("Braintrust not installed. Run: pip install braintrust[otel]")


@dataclass
class BraintrustCallMetrics:
    """Metrics for a traced Braintrust call."""

    span_id: str
    name: str
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0
    cost_usd: float = 0
    status: str = "success"
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    agent_type: Optional[str] = None
    workflow_id: Optional[str] = None
    tenant_id: Optional[str] = None
    scores: Dict[str, float] = field(default_factory=dict)


class BraintrustObservabilityService:
    """
    Braintrust-based observability service for AI/LLM monitoring.

    Features:
    - OpenTelemetry integration via BraintrustSpanProcessor
    - Automatic LLM tracing with @traced decorator
    - OpenAI client wrapping for automatic instrumentation
    - Evaluation scoring and feedback submission
    - Cost estimation per call
    """

    # Cost per 1K tokens (approximate, as of Jan 2025)
    TOKEN_COSTS = {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "claude-3-sonnet": {"input": 0.003, "output": 0.015},
        "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
        "claude-3.5-sonnet": {"input": 0.003, "output": 0.015},
    }

    def __init__(self):
        self._logger: Optional[Any] = None
        self._metrics: List[BraintrustCallMetrics] = []
        self._max_metrics = 10000  # Rolling window
        self._wrapped_clients: Dict[str, Any] = {}

        # Initialize Braintrust if configured
        if BRAINTRUST_AVAILABLE and settings.BRAINTRUST_API_KEY:
            try:
                self._logger = init_logger(
                    project=settings.BRAINTRUST_PROJECT,
                    api_key=settings.BRAINTRUST_API_KEY,
                )
                logger.info(
                    f"Braintrust initialized for project: {settings.BRAINTRUST_PROJECT}"
                )
            except Exception as e:
                logger.error(f"Failed to initialize Braintrust: {e}")

    @property
    def is_enabled(self) -> bool:
        """Check if Braintrust is enabled and configured."""
        return self._logger is not None

    def get_span_processor(self) -> Optional[Any]:
        """
        Get BraintrustSpanProcessor for OpenTelemetry integration.

        Add this processor to your TracerProvider to automatically
        send spans to Braintrust.

        Usage in telemetry.py:
            from app.services.braintrust_observability import get_braintrust_service
            bt_service = get_braintrust_service()
            if bt_service.is_enabled:
                processor = bt_service.get_span_processor()
                if processor:
                    trace.get_tracer_provider().add_span_processor(processor)
        """
        if not BRAINTRUST_AVAILABLE or not settings.BRAINTRUST_API_KEY:
            return None

        try:
            return BraintrustSpanProcessor(
                api_key=settings.BRAINTRUST_API_KEY,
                project=settings.BRAINTRUST_PROJECT,
                # Filter to only capture AI-related spans
                filter_ai_spans=True,
            )
        except Exception as e:
            logger.error(f"Failed to create BraintrustSpanProcessor: {e}")
            return None

    def wrap_openai_client(self, client: Any) -> Any:
        """
        Wrap an OpenAI client for automatic tracing.

        All calls through the wrapped client will be automatically
        logged to Braintrust with token counts and latency.

        Args:
            client: OpenAI client instance

        Returns:
            Wrapped client with tracing enabled
        """
        if not BRAINTRUST_AVAILABLE:
            return client

        try:
            wrapped = wrap_openai(client)
            logger.info("OpenAI client wrapped for Braintrust tracing")
            return wrapped
        except Exception as e:
            logger.error(f"Failed to wrap OpenAI client: {e}")
            return client

    def trace(
        self,
        name: Optional[str] = None,
        agent_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Decorator to trace a function with Braintrust.

        Usage:
            @braintrust_service.trace(
                name="analyze_portfolio",
                agent_type="analysis"
            )
            async def analyze_portfolio(portfolio_id: str) -> Dict:
                ...
        """

        def decorator(func: Callable):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                if not BRAINTRUST_AVAILABLE or not self._logger:
                    return await func(*args, **kwargs)

                span_name = name or func.__name__
                span_id = str(uuid4())
                start_time = time.perf_counter()

                metrics = BraintrustCallMetrics(
                    span_id=span_id,
                    name=span_name,
                    agent_type=agent_type,
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                )

                try:
                    # Use Braintrust's traced decorator
                    @traced(name=span_name)
                    async def traced_func():
                        return await func(*args, **kwargs)

                    result = await traced_func()

                    metrics.latency_ms = (time.perf_counter() - start_time) * 1000
                    metrics.status = "success"

                    # Extract token usage if available
                    if isinstance(result, dict):
                        if "usage" in result:
                            usage = result["usage"]
                            metrics.input_tokens = usage.get("prompt_tokens", 0)
                            metrics.output_tokens = usage.get("completion_tokens", 0)
                            metrics.total_tokens = usage.get("total_tokens", 0)

                        if "model" in result:
                            metrics.model = result["model"]
                            metrics.cost_usd = self._estimate_cost(
                                metrics.model,
                                metrics.input_tokens,
                                metrics.output_tokens,
                            )

                    self._record_metrics(metrics)
                    return result

                except Exception as e:
                    metrics.latency_ms = (time.perf_counter() - start_time) * 1000
                    metrics.status = "error"
                    metrics.error = str(e)
                    self._record_metrics(metrics)
                    raise

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                if not BRAINTRUST_AVAILABLE or not self._logger:
                    return func(*args, **kwargs)

                span_name = name or func.__name__
                span_id = str(uuid4())
                start_time = time.perf_counter()

                metrics = BraintrustCallMetrics(
                    span_id=span_id,
                    name=span_name,
                    agent_type=agent_type,
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                )

                try:
                    @traced(name=span_name)
                    def traced_func():
                        return func(*args, **kwargs)

                    result = traced_func()

                    metrics.latency_ms = (time.perf_counter() - start_time) * 1000
                    metrics.status = "success"
                    self._record_metrics(metrics)
                    return result

                except Exception as e:
                    metrics.latency_ms = (time.perf_counter() - start_time) * 1000
                    metrics.status = "error"
                    metrics.error = str(e)
                    self._record_metrics(metrics)
                    raise

            # Return appropriate wrapper based on function type
            import asyncio

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate cost based on model and token counts."""
        # Normalize model name
        model_lower = model.lower() if model else ""

        # Sort model keys by length (longest first) to match more specific models first
        # e.g., "gpt-4o-mini" should match before "gpt-4"
        sorted_models = sorted(self.TOKEN_COSTS.keys(), key=len, reverse=True)

        for model_key in sorted_models:
            if model_key in model_lower:
                costs = self.TOKEN_COSTS[model_key]
                input_cost = (input_tokens / 1000) * costs["input"]
                output_cost = (output_tokens / 1000) * costs["output"]
                return round(input_cost + output_cost, 6)

        # Default cost estimate for unknown models
        return round((input_tokens + output_tokens) / 1000 * 0.002, 6)

    def _record_metrics(self, metrics: BraintrustCallMetrics) -> None:
        """Record metrics and maintain rolling window."""
        self._metrics.append(metrics)

        if len(self._metrics) > self._max_metrics:
            self._metrics = self._metrics[-self._max_metrics :]

    async def log_feedback(
        self,
        span_id: str,
        scores: Dict[str, float],
        comment: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Log feedback/scores for a traced span.

        Args:
            span_id: The span ID to score
            scores: Dict of score names to values (0-1)
            comment: Optional feedback comment
            metadata: Optional additional metadata

        Returns:
            True if feedback was logged successfully
        """
        if not self._logger:
            logger.warning("Braintrust not enabled, feedback not logged")
            return False

        try:
            self._logger.log_feedback(
                id=span_id,
                scores=scores,
                comment=comment,
                metadata=metadata or {},
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log feedback: {e}")
            return False

    def get_cost_summary(
        self, window_minutes: int = 60, group_by: str = "model"
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get cost summary grouped by model or agent type.

        Args:
            window_minutes: Time window to consider
            group_by: "model" or "agent_type"

        Returns:
            Dict of cost summaries per group
        """
        from datetime import timedelta
        from collections import defaultdict

        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)

        summary: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "avg_latency_ms": 0.0,
                "errors": 0,
            }
        )

        latencies: Dict[str, List[float]] = defaultdict(list)

        for m in self._metrics:
            if m.timestamp < cutoff:
                continue

            key = getattr(m, group_by, "unknown") or "unknown"
            summary[key]["calls"] += 1
            summary[key]["input_tokens"] += m.input_tokens
            summary[key]["output_tokens"] += m.output_tokens
            summary[key]["total_tokens"] += m.total_tokens
            summary[key]["cost_usd"] += m.cost_usd

            if m.status == "error":
                summary[key]["errors"] += 1

            latencies[key].append(m.latency_ms)

        # Calculate average latencies
        for key in summary:
            if latencies[key]:
                summary[key]["avg_latency_ms"] = round(
                    sum(latencies[key]) / len(latencies[key]), 2
                )

        return dict(summary)

    def get_dashboard_metrics(self, window_minutes: int = 60) -> Dict[str, Any]:
        """
        Get comprehensive metrics for dashboard display.

        Returns:
            Dict with cost, latency, error rate, and usage metrics
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)

        relevant = [m for m in self._metrics if m.timestamp >= cutoff]

        if not relevant:
            return {
                "total_calls": 0,
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "error_rate": 0.0,
                "avg_latency_ms": 0.0,
                "cost_by_model": {},
                "cost_by_agent": {},
                "window_minutes": window_minutes,
                "generated_at": datetime.utcnow().isoformat(),
            }

        total_cost = sum(m.cost_usd for m in relevant)
        total_tokens = sum(m.total_tokens for m in relevant)
        errors = sum(1 for m in relevant if m.status == "error")
        latencies = [m.latency_ms for m in relevant if m.status == "success"]

        return {
            "total_calls": len(relevant),
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "error_rate": round(errors / len(relevant), 4) if relevant else 0,
            "error_count": errors,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2)
            if latencies
            else 0,
            "p95_latency_ms": round(
                sorted(latencies)[int(len(latencies) * 0.95)], 2
            )
            if len(latencies) > 20
            else 0,
            "cost_by_model": self.get_cost_summary(window_minutes, "model"),
            "cost_by_agent": self.get_cost_summary(window_minutes, "agent_type"),
            "window_minutes": window_minutes,
            "generated_at": datetime.utcnow().isoformat(),
        }


# Global service instance
_braintrust_service: Optional[BraintrustObservabilityService] = None


def get_braintrust_service() -> BraintrustObservabilityService:
    """Get or create the global Braintrust observability service."""
    global _braintrust_service
    if _braintrust_service is None:
        _braintrust_service = BraintrustObservabilityService()
    return _braintrust_service


# Convenience decorator
def trace_with_braintrust(
    name: Optional[str] = None,
    agent_type: Optional[str] = None,
):
    """
    Convenience decorator for Braintrust tracing.

    Usage:
        @trace_with_braintrust("fetch_stock_data", agent_type="data-retrieval")
        async def fetch_stock_data(symbol: str) -> Dict:
            ...
    """
    service = get_braintrust_service()
    return service.trace(name=name, agent_type=agent_type)
