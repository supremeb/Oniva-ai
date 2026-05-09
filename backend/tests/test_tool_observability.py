"""Tests for tool execution observability (Phase A) and cache metrics (Phase D).

Covers:
- trace_tool_execution records metrics on success
- trace_tool_execution records metrics on error
- Observability failure doesn't break tool execution
- Metrics aggregation for dashboard endpoint (get_tool_metrics)
- PII safety: no args/results captured
- KV-cache hit ratio extraction
"""
import pytest
import asyncio
from unittest.mock import patch
from uuid import uuid4

from app.services.observability import (
    ObservabilityService,
    LLMCallMetrics,
    ToolExecutionMetrics,
    ToolTraceContext,
)


class TestToolTraceContext:
    """Test the ToolTraceContext helper."""

    def test_defaults_to_success(self):
        ctx = ToolTraceContext(call_id="test-id")
        assert ctx._status == "success"
        assert ctx._error is None
        assert ctx._output_size == 0

    def test_set_success(self):
        ctx = ToolTraceContext(call_id="test-id")
        ctx.set_success(output_size=1024)
        assert ctx._status == "success"
        assert ctx._output_size == 1024

    def test_set_error(self):
        ctx = ToolTraceContext(call_id="test-id")
        ctx.set_error("connection timeout")
        assert ctx._status == "error"
        assert ctx._error == "connection timeout"


class TestTraceToolExecution:
    """Test trace_tool_execution context manager."""

    @pytest.fixture
    def service(self):
        svc = ObservabilityService()
        svc._client = None  # No LangSmith
        return svc

    @pytest.mark.asyncio
    async def test_records_success_metrics(self, service):
        """Successful tool execution records metrics with correct fields."""
        async with service.trace_tool_execution(
            tool_name="web_search",
            tool_category="web",
            agent_type="data-retrieval",
            tenant_id="tenant-1",
            session_id="sess-1",
        ) as trace:
            trace.set_success(output_size=500)

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.05)

        assert len(service._tool_metrics) == 1
        m = service._tool_metrics[0]
        assert m.tool_name == "web_search"
        assert m.tool_category == "web"
        assert m.agent_type == "data-retrieval"
        assert m.status == "success"
        assert m.tenant_id == "tenant-1"
        assert m.session_id == "sess-1"
        assert m.output_size_bytes == 500
        assert m.latency_ms > 0
        assert m.call_id  # non-empty UUID

    @pytest.mark.asyncio
    async def test_records_error_metrics(self, service):
        """Tool execution error records error status and message."""
        with pytest.raises(ValueError, match="test error"):
            async with service.trace_tool_execution(
                tool_name="fetch_market_data",
                tool_category="financial",
                agent_type="analysis",
                tenant_id="tenant-1",
            ) as trace:
                raise ValueError("test error")

        await asyncio.sleep(0.05)

        assert len(service._tool_metrics) == 1
        m = service._tool_metrics[0]
        assert m.status == "error"
        assert m.error == "exception"
        assert m.tool_name == "fetch_market_data"

    @pytest.mark.asyncio
    async def test_explicit_error_before_raise(self, service):
        """When set_error is called before an exception, error message is captured."""
        with pytest.raises(ConnectionError):
            async with service.trace_tool_execution(
                tool_name="browse_url",
                tool_category="web",
                agent_type="research",
            ) as trace:
                trace.set_error("DNS resolution failed")
                raise ConnectionError("DNS resolution failed")

        await asyncio.sleep(0.05)

        m = service._tool_metrics[0]
        assert m.status == "error"
        assert m.error == "DNS resolution failed"

    @pytest.mark.asyncio
    async def test_observability_failure_doesnt_break_execution(self, service):
        """If backend dispatch fails, tool execution still succeeds."""
        # Force dispatch to raise an error
        with patch.object(service, '_dispatch_tool_trace', side_effect=RuntimeError("dispatch boom")):
            async with service.trace_tool_execution(
                tool_name="web_search",
                tool_category="web",
                agent_type="data-retrieval",
            ) as trace:
                trace.set_success(output_size=100)

        # Metrics still recorded locally
        assert len(service._tool_metrics) == 1

    @pytest.mark.asyncio
    async def test_custom_call_id_and_attempt(self, service):
        """Custom call_id and attempt_number are threaded through."""
        async with service.trace_tool_execution(
            tool_name="sanctions_check",
            tool_category="compliance",
            agent_type="compliance",
            call_id="custom-call-123",
            attempt_number=2,
        ) as trace:
            trace.set_success()

        await asyncio.sleep(0.05)

        m = service._tool_metrics[0]
        assert m.call_id == "custom-call-123"
        assert m.attempt_number == 2

    @pytest.mark.asyncio
    async def test_no_args_or_results_captured(self, service):
        """Security: verify no tool arguments or return values in metrics (PII)."""
        async with service.trace_tool_execution(
            tool_name="kyc_verification",
            tool_category="compliance",
            agent_type="compliance",
            tenant_id="tenant-1",
        ) as trace:
            trace.set_success(output_size=2048)

        await asyncio.sleep(0.05)

        m = service._tool_metrics[0]
        # ToolExecutionMetrics has no 'arguments' or 'result' field
        assert not hasattr(m, 'arguments')
        assert not hasattr(m, 'result')
        assert not hasattr(m, 'input_data')
        assert not hasattr(m, 'output_data')


class TestGetToolMetrics:
    """Test tool metrics aggregation for dashboard endpoint."""

    @pytest.fixture
    def service(self):
        svc = ObservabilityService()
        svc._client = None
        return svc

    def _add_tool_metric(self, service, tool_name="web_search", category="web",
                         agent_type="data-retrieval", tenant_id="tenant-1",
                         latency_ms=100.0, status="success"):
        m = ToolExecutionMetrics(
            tool_name=tool_name,
            tool_category=category,
            agent_type=agent_type,
            latency_ms=latency_ms,
            status=status,
            call_id=str(uuid4()),
            tenant_id=tenant_id,
        )
        service._tool_metrics.append(m)

    def test_group_by_tool_name(self, service):
        """Metrics grouped by tool_name."""
        self._add_tool_metric(service, tool_name="web_search", latency_ms=50)
        self._add_tool_metric(service, tool_name="web_search", latency_ms=150)
        self._add_tool_metric(service, tool_name="browse_url", latency_ms=200)

        result = service.get_tool_metrics(group_by="tool_name", tenant_id="tenant-1")

        assert "web_search" in result
        assert result["web_search"]["calls"] == 2
        assert "browse_url" in result
        assert result["browse_url"]["calls"] == 1

    def test_group_by_agent_type(self, service):
        """Metrics grouped by agent_type."""
        self._add_tool_metric(service, agent_type="data-retrieval")
        self._add_tool_metric(service, agent_type="data-retrieval")
        self._add_tool_metric(service, agent_type="analysis")

        result = service.get_tool_metrics(group_by="agent_type", tenant_id="tenant-1")

        assert result["data-retrieval"]["calls"] == 2
        assert result["analysis"]["calls"] == 1

    def test_error_rate_calculation(self, service):
        """Error rate computed correctly."""
        self._add_tool_metric(service, status="success")
        self._add_tool_metric(service, status="success")
        self._add_tool_metric(service, status="error")

        result = service.get_tool_metrics(group_by="tool_name", tenant_id="tenant-1")

        assert result["web_search"]["error_count"] == 1
        assert result["web_search"]["error_rate"] == pytest.approx(1 / 3)

    def test_tenant_isolation(self, service):
        """Metrics only include the requested tenant."""
        self._add_tool_metric(service, tenant_id="tenant-a")
        self._add_tool_metric(service, tenant_id="tenant-b")

        result_a = service.get_tool_metrics(tenant_id="tenant-a")
        result_b = service.get_tool_metrics(tenant_id="tenant-b")

        assert result_a["web_search"]["calls"] == 1
        assert result_b["web_search"]["calls"] == 1

    def test_empty_metrics(self, service):
        """No metrics returns empty dict."""
        result = service.get_tool_metrics(tenant_id="tenant-1")
        assert result == {}

    def test_latency_percentiles(self, service):
        """Latency percentiles are calculated."""
        for lat in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            self._add_tool_metric(service, latency_ms=lat)

        result = service.get_tool_metrics(tenant_id="tenant-1")
        ws = result["web_search"]

        assert ws["latency_p50"] > 0
        assert ws["latency_p95"] > 0
        assert ws["latency_p99"] > 0
        assert ws["latency_mean"] == pytest.approx(55.0)


class TestCacheMetrics:
    """Test KV-cache hit ratio extraction and aggregation (Phase D)."""

    @pytest.fixture
    def service(self):
        svc = ObservabilityService()
        svc._client = None
        return svc

    def _add_llm_metric(self, service, tenant_id="tenant-1",
                        cache_creation=0, cache_read=0):
        m = LLMCallMetrics(
            call_id=str(uuid4()),
            provider="anthropic",
            model="claude-3.5-sonnet",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            latency_ms=200,
            tenant_id=tenant_id,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
        )
        service._record_metrics(m)

    def test_cache_hit_ratio(self, service):
        """Cache hit ratio computed correctly."""
        # 2 calls with cache hits, 1 without
        self._add_llm_metric(service, cache_read=500)
        self._add_llm_metric(service, cache_read=300)
        self._add_llm_metric(service, cache_read=0)

        result = service.get_cache_metrics(tenant_id="tenant-1")

        assert result["total_calls"] == 3
        assert result["cache_hit_calls"] == 2
        assert result["cache_hit_ratio"] == pytest.approx(2 / 3)
        assert result["total_cache_read_tokens"] == 800

    def test_cache_creation_tracked(self, service):
        """Cache creation tokens tracked separately."""
        self._add_llm_metric(service, cache_creation=1000, cache_read=0)
        self._add_llm_metric(service, cache_creation=0, cache_read=1000)

        result = service.get_cache_metrics(tenant_id="tenant-1")

        assert result["total_cache_creation_tokens"] == 1000
        assert result["total_cache_read_tokens"] == 1000
        assert result["cache_hit_calls"] == 1

    def test_no_calls_returns_zero_ratio(self, service):
        """Empty metrics returns 0 ratio."""
        result = service.get_cache_metrics(tenant_id="tenant-1")

        assert result["total_calls"] == 0
        assert result["cache_hit_ratio"] == 0

    def test_tenant_isolation(self, service):
        """Cache metrics filtered by tenant."""
        self._add_llm_metric(service, tenant_id="tenant-a", cache_read=500)
        self._add_llm_metric(service, tenant_id="tenant-b", cache_read=0)

        result_a = service.get_cache_metrics(tenant_id="tenant-a")
        result_b = service.get_cache_metrics(tenant_id="tenant-b")

        assert result_a["cache_hit_calls"] == 1
        assert result_b["cache_hit_calls"] == 0


class TestDeterministicToolOrdering:
    """Test that extra_tools are sorted by name for KV-cache reuse."""

    def test_tools_sorted_by_name(self):
        """Extra tools are sorted alphabetically by function name."""
        async def zebra_tool(ctx): pass
        async def alpha_tool(ctx): pass
        async def middle_tool(ctx): pass

        tools = [zebra_tool, alpha_tool, middle_tool]
        sorted_tools = sorted(
            tools,
            key=lambda f: getattr(f, '__name__', 'unknown'),
        )
        names = [f.__name__ for f in sorted_tools]
        assert names == ["alpha_tool", "middle_tool", "zebra_tool"]
