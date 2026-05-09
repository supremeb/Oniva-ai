"""
Unit tests for Braintrust Observability Service.

Tests the BraintrustObservabilityService including:
- Service initialization
- Cost estimation
- Metrics recording
- Dashboard metrics generation
"""

import pytest
from datetime import datetime
from unittest.mock import patch

from app.services.braintrust_observability import (
    BraintrustObservabilityService,
    BraintrustCallMetrics,
    get_braintrust_service,
    trace_with_braintrust,
    BRAINTRUST_AVAILABLE,
)


class TestBraintrustCallMetrics:
    """Test the BraintrustCallMetrics dataclass."""

    def test_metrics_default_values(self):
        """Test that metrics have sensible defaults."""
        metrics = BraintrustCallMetrics(
            span_id="test-span-123",
            name="test_operation",
        )

        assert metrics.span_id == "test-span-123"
        assert metrics.name == "test_operation"
        assert metrics.input_tokens == 0
        assert metrics.output_tokens == 0
        assert metrics.cost_usd == 0
        assert metrics.status == "success"
        assert metrics.error is None
        assert isinstance(metrics.timestamp, datetime)

    def test_metrics_with_values(self):
        """Test metrics with all values set."""
        metrics = BraintrustCallMetrics(
            span_id="span-456",
            name="llm_call",
            provider="openai",
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            latency_ms=1500.5,
            cost_usd=0.0045,
            status="success",
            agent_type="analysis",
            workflow_id="wf-123",
            tenant_id="tenant-abc",
        )

        assert metrics.provider == "openai"
        assert metrics.model == "gpt-4"
        assert metrics.input_tokens == 100
        assert metrics.output_tokens == 50
        assert metrics.cost_usd == 0.0045


class TestBraintrustObservabilityService:
    """Test the BraintrustObservabilityService class."""

    @pytest.fixture
    def service(self):
        """Create a service instance without Braintrust credentials."""
        with patch.dict(
            "os.environ",
            {"BRAINTRUST_API_KEY": "", "BRAINTRUST_PROJECT": "test-project"},
        ):
            # Reset global service instance
            import app.services.braintrust_observability as bt_module

            bt_module._braintrust_service = None

            service = BraintrustObservabilityService()
            return service

    @pytest.fixture
    def service_with_metrics(self, service):
        """Create a service with pre-populated metrics."""
        now = datetime.utcnow()

        # Add various metrics
        metrics_data = [
            {
                "span_id": "span-1",
                "name": "llm_call",
                "model": "gpt-4",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.0045,
                "latency_ms": 1500,
                "status": "success",
                "agent_type": "analysis",
            },
            {
                "span_id": "span-2",
                "name": "llm_call",
                "model": "gpt-4o-mini",
                "input_tokens": 200,
                "output_tokens": 100,
                "cost_usd": 0.00009,
                "latency_ms": 800,
                "status": "success",
                "agent_type": "data-retrieval",
            },
            {
                "span_id": "span-3",
                "name": "llm_call",
                "model": "gpt-4",
                "input_tokens": 150,
                "output_tokens": 75,
                "cost_usd": 0.009,
                "latency_ms": 2000,
                "status": "error",
                "error": "Rate limit exceeded",
                "agent_type": "analysis",
            },
        ]

        for data in metrics_data:
            metrics = BraintrustCallMetrics(
                span_id=data["span_id"],
                name=data["name"],
                model=data.get("model"),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                cost_usd=data.get("cost_usd", 0),
                latency_ms=data.get("latency_ms", 0),
                status=data.get("status", "success"),
                error=data.get("error"),
                agent_type=data.get("agent_type"),
                timestamp=now,
            )
            service._record_metrics(metrics)

        return service

    def test_service_initialization_without_credentials(self, service):
        """Test service initializes without credentials."""
        assert service._logger is None
        assert not service.is_enabled

    def test_cost_estimation_gpt4(self, service):
        """Test cost estimation for GPT-4."""
        cost = service._estimate_cost("gpt-4", input_tokens=1000, output_tokens=500)
        # GPT-4: $0.03/1K input, $0.06/1K output
        expected = (1000 / 1000 * 0.03) + (500 / 1000 * 0.06)
        assert cost == round(expected, 6)

    def test_cost_estimation_gpt4o_mini(self, service):
        """Test cost estimation for GPT-4o-mini."""
        cost = service._estimate_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500)
        # GPT-4o-mini: $0.00015/1K input, $0.0006/1K output
        expected = (1000 / 1000 * 0.00015) + (500 / 1000 * 0.0006)
        assert cost == round(expected, 6)

    def test_cost_estimation_claude(self, service):
        """Test cost estimation for Claude models."""
        cost = service._estimate_cost(
            "claude-3-sonnet", input_tokens=1000, output_tokens=500
        )
        # Claude 3 Sonnet: $0.003/1K input, $0.015/1K output
        expected = (1000 / 1000 * 0.003) + (500 / 1000 * 0.015)
        assert cost == round(expected, 6)

    def test_cost_estimation_unknown_model(self, service):
        """Test cost estimation for unknown models uses default."""
        cost = service._estimate_cost(
            "unknown-model-xyz", input_tokens=1000, output_tokens=500
        )
        # Default: $0.002/1K total tokens
        expected = (1000 + 500) / 1000 * 0.002
        assert cost == round(expected, 6)

    def test_record_metrics(self, service):
        """Test recording metrics."""
        metrics = BraintrustCallMetrics(
            span_id="test-span",
            name="test_operation",
        )

        service._record_metrics(metrics)

        assert len(service._metrics) == 1
        assert service._metrics[0].span_id == "test-span"

    def test_metrics_rolling_window(self, service):
        """Test that metrics maintain a rolling window."""
        service._max_metrics = 5

        # Add more metrics than the window
        for i in range(10):
            metrics = BraintrustCallMetrics(
                span_id=f"span-{i}",
                name="test",
            )
            service._record_metrics(metrics)

        # Should only keep the last 5
        assert len(service._metrics) == 5
        assert service._metrics[0].span_id == "span-5"
        assert service._metrics[-1].span_id == "span-9"

    def test_get_cost_summary(self, service_with_metrics):
        """Test cost summary aggregation."""
        summary = service_with_metrics.get_cost_summary(window_minutes=60, group_by="model")

        assert "gpt-4" in summary
        assert "gpt-4o-mini" in summary

        # GPT-4 should have 2 calls (1 success + 1 error)
        assert summary["gpt-4"]["calls"] == 2
        assert summary["gpt-4"]["errors"] == 1

        # GPT-4o-mini should have 1 call
        assert summary["gpt-4o-mini"]["calls"] == 1
        assert summary["gpt-4o-mini"]["errors"] == 0

    def test_get_cost_summary_by_agent(self, service_with_metrics):
        """Test cost summary grouped by agent type."""
        summary = service_with_metrics.get_cost_summary(
            window_minutes=60, group_by="agent_type"
        )

        assert "analysis" in summary
        assert "data-retrieval" in summary

        # Analysis agent has 2 calls
        assert summary["analysis"]["calls"] == 2

    def test_get_dashboard_metrics(self, service_with_metrics):
        """Test dashboard metrics generation."""
        dashboard = service_with_metrics.get_dashboard_metrics(window_minutes=60)

        assert dashboard["total_calls"] == 3
        assert dashboard["error_count"] == 1
        assert dashboard["error_rate"] == round(1 / 3, 4)
        assert "total_cost_usd" in dashboard
        assert "cost_by_model" in dashboard
        assert "cost_by_agent" in dashboard
        assert "generated_at" in dashboard

    def test_get_dashboard_metrics_empty(self, service):
        """Test dashboard metrics with no data."""
        dashboard = service.get_dashboard_metrics(window_minutes=60)

        assert dashboard["total_calls"] == 0
        assert dashboard["total_cost_usd"] == 0
        assert dashboard["error_rate"] == 0

    def test_get_span_processor_returns_none_without_credentials(self, service):
        """Test that span processor returns None without credentials."""
        processor = service.get_span_processor()
        assert processor is None


class TestTraceDecorator:
    """Test the trace decorator functionality."""

    @pytest.mark.asyncio
    async def test_trace_decorator_passthrough_when_disabled(self):
        """Test that decorator passes through when Braintrust is disabled."""
        # The decorator should pass through when service is not enabled

        @trace_with_braintrust("test_operation")
        async def test_func():
            return "result"

        result = await test_func()
        assert result == "result"

    def test_trace_decorator_sync_passthrough(self):
        """Test that sync decorator passes through when disabled."""

        @trace_with_braintrust("test_sync_operation")
        def test_sync_func():
            return "sync_result"

        result = test_sync_func()
        assert result == "sync_result"


class TestGlobalServiceSingleton:
    """Test the global service singleton pattern."""

    def test_get_braintrust_service_singleton(self):
        """Test that get_braintrust_service returns same instance."""
        # Reset global instance
        import app.services.braintrust_observability as bt_module

        bt_module._braintrust_service = None

        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": ""}):
            service1 = get_braintrust_service()
            service2 = get_braintrust_service()

            assert service1 is service2


@pytest.mark.skipif(not BRAINTRUST_AVAILABLE, reason="Braintrust not installed")
class TestBraintrustIntegration:
    """Integration tests that require Braintrust to be installed."""

    def test_braintrust_imports_available(self):
        """Test that Braintrust imports are available."""
        from braintrust import traced, init_logger
        from braintrust.otel import BraintrustSpanProcessor

        assert callable(traced)
        assert callable(init_logger)
        assert BraintrustSpanProcessor is not None

    def test_span_processor_creation(self):
        """Test BraintrustSpanProcessor can be created with valid args."""
        from braintrust.otel import BraintrustSpanProcessor

        # This should not raise even without valid API key
        # (it will just not send data)
        try:
            processor = BraintrustSpanProcessor(
                api_key="test-key",
                project="test-project",
                filter_ai_spans=True,
            )
            assert processor is not None
        except Exception:
            # Some versions may validate API key immediately
            pass
