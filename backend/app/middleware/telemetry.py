"""
OpenTelemetry Tracing Middleware

Part of Phase 1: Production Reliability.

Provides distributed tracing for:
- All FastAPI endpoints
- LLM calls with token counts
- Workflow execution steps
- Agent tool invocations
"""

import time
import logging
from typing import Callable, Optional, Dict, Any
from functools import wraps

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# OpenTelemetry imports - gracefully handle if not installed
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logger.warning("OpenTelemetry not installed. Tracing disabled.")

# Global tracer
_tracer: Optional[Any] = None


def init_telemetry(app) -> None:
    """
    Initialize OpenTelemetry tracing for the application.

    Call this in main.py during startup:
        from app.middleware.telemetry import init_telemetry
        init_telemetry(app)
    """
    global _tracer

    if not OTEL_AVAILABLE:
        logger.info("OpenTelemetry not available, skipping initialization")
        return

    # Create resource with service info
    resource = Resource.create({
        "service.name": settings.APP_NAME,
        "service.version": settings.APP_VERSION,
        "deployment.environment": settings.ENVIRONMENT,
    })

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Configure exporter if endpoint is set
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        exporter = OTLPSpanExporter(
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            insecure=True,  # Set to False in production with TLS
        )
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        logger.info(f"OTLP exporter configured: {settings.OTEL_EXPORTER_OTLP_ENDPOINT}")
    else:
        logger.info("No OTLP endpoint configured, traces will be logged locally")

    # Configure Braintrust span processor if enabled (P2.4 Observability)
    if settings.BRAINTRUST_ENABLED and settings.BRAINTRUST_API_KEY:
        try:
            from app.services.braintrust_observability import get_braintrust_service

            bt_service = get_braintrust_service()
            bt_processor = bt_service.get_span_processor()
            if bt_processor:
                provider.add_span_processor(bt_processor)
                logger.info(
                    f"Braintrust span processor configured for project: {settings.BRAINTRUST_PROJECT}"
                )
        except ImportError as e:
            logger.warning(f"Braintrust not available: {e}")
        except Exception as e:
            logger.error(f"Failed to configure Braintrust: {e}")

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    # Create tracer
    _tracer = trace.get_tracer(settings.APP_NAME, settings.APP_VERSION)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    logger.info("OpenTelemetry tracing initialized")


def get_tracer():
    """Get the global tracer instance."""
    global _tracer
    if _tracer is None and OTEL_AVAILABLE:
        _tracer = trace.get_tracer(settings.APP_NAME, settings.APP_VERSION)
    return _tracer


class TelemetryMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add custom tracing attributes to all requests.

    Adds:
    - Tenant ID from context
    - User ID from JWT
    - Request timing
    - Response status
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Extract tracing context
        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)

        # Get current span if available
        if OTEL_AVAILABLE:
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                current_span.set_attribute("tenant.id", str(tenant_id) if tenant_id else "unknown")
                current_span.set_attribute("user.id", str(user_id) if user_id else "unknown")
                current_span.set_attribute("http.route", str(request.url.path))

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Add response attributes
        if OTEL_AVAILABLE:
            current_span = trace.get_current_span()
            if current_span and current_span.is_recording():
                current_span.set_attribute("http.status_code", response.status_code)
                current_span.set_attribute("http.duration_ms", duration_ms)

                # Set status based on response code
                if response.status_code >= 500:
                    current_span.set_status(Status(StatusCode.ERROR))
                elif response.status_code >= 400:
                    current_span.set_status(Status(StatusCode.ERROR, "Client error"))

        return response


def trace_llm_call(
    provider: str,
    model: str,
    operation: str = "completion",
):
    """
    Decorator to trace LLM API calls.

    Usage:
        @trace_llm_call(provider="openai", model="gpt-4")
        async def call_openai(prompt: str) -> str:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()

            if not tracer or not OTEL_AVAILABLE:
                return await func(*args, **kwargs)

            with tracer.start_as_current_span(
                f"llm.{operation}",
                attributes={
                    "llm.provider": provider,
                    "llm.model": model,
                    "llm.operation": operation,
                }
            ) as span:
                start_time = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    # Extract token counts if available
                    if isinstance(result, dict):
                        if "usage" in result:
                            usage = result["usage"]
                            span.set_attribute("llm.input_tokens", usage.get("prompt_tokens", 0))
                            span.set_attribute("llm.output_tokens", usage.get("completion_tokens", 0))
                            span.set_attribute("llm.total_tokens", usage.get("total_tokens", 0))

                    duration_ms = (time.perf_counter() - start_time) * 1000
                    span.set_attribute("llm.duration_ms", duration_ms)
                    span.set_status(Status(StatusCode.OK))

                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def trace_workflow_step(step_name: str):
    """
    Decorator to trace workflow execution steps.

    Usage:
        @trace_workflow_step("execute_node")
        async def execute_node(node_id: str) -> Dict:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()

            if not tracer or not OTEL_AVAILABLE:
                return await func(*args, **kwargs)

            # Extract node_id if present in kwargs
            node_id = kwargs.get("node_id", "unknown")
            node_type = kwargs.get("node_type", "unknown")

            with tracer.start_as_current_span(
                f"workflow.{step_name}",
                attributes={
                    "workflow.step": step_name,
                    "workflow.node_id": str(node_id),
                    "workflow.node_type": str(node_type),
                }
            ) as span:
                start_time = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    duration_ms = (time.perf_counter() - start_time) * 1000
                    span.set_attribute("workflow.duration_ms", duration_ms)

                    # Extract status if result is a dict or has status attribute
                    if isinstance(result, dict) and "status" in result:
                        span.set_attribute("workflow.result_status", result["status"])
                    elif hasattr(result, "status"):
                        span.set_attribute("workflow.result_status", result.status)

                    span.set_status(Status(StatusCode.OK))
                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def trace_agent_tool(tool_name: str):
    """
    Decorator to trace agent tool invocations.

    Usage:
        @trace_agent_tool("fetch_stock_price")
        async def fetch_stock_price(symbol: str) -> Dict:
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()

            if not tracer or not OTEL_AVAILABLE:
                return await func(*args, **kwargs)

            with tracer.start_as_current_span(
                f"agent.tool.{tool_name}",
                attributes={
                    "agent.tool.name": tool_name,
                }
            ) as span:
                start_time = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    duration_ms = (time.perf_counter() - start_time) * 1000
                    span.set_attribute("agent.tool.duration_ms", duration_ms)
                    span.set_attribute("agent.tool.success", True)
                    span.set_status(Status(StatusCode.OK))

                    return result

                except Exception as e:
                    span.set_attribute("agent.tool.success", False)
                    span.set_attribute("agent.tool.error", str(e))
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        return wrapper
    return decorator


def create_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
):
    """
    Context manager for creating custom spans.

    Usage:
        with create_span("custom.operation", {"custom.attr": "value"}) as span:
            # do work
            span.set_attribute("result", "success")
    """
    tracer = get_tracer()

    if not tracer or not OTEL_AVAILABLE:
        # Return a no-op context manager
        from contextlib import nullcontext
        return nullcontext()

    return tracer.start_as_current_span(name, attributes=attributes or {})
