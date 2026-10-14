"""Tests for observability API endpoints and tenant isolation fix."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.observability import ObservabilityService, LLMCallMetrics


class TestObservabilityServiceTenantIsolation:
    """Test that ObservabilityService properly filters by tenant_id."""

    @pytest.fixture
    def service(self):
        svc = ObservabilityService()
        svc._client = None  # No LangSmith
        return svc

    def _add_metric(self, service, tenant_id: str, provider="openai", model="gpt-4o",
                    latency_ms=100.0, status="success", agent_type=None):
        metric = LLMCallMetrics(
            call_id=str(uuid4()),
            provider=provider,
            model=model,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            latency_ms=latency_ms,
            status=status,
            tenant_id=tenant_id,
            agent_type=agent_type,
        )
        service._record_metrics(metric)
        return metric

    def test_latency_filters_by_tenant(self, service):
        """Only metrics for the requested tenant are included in latency stats."""
        self._add_metric(service, "tenant-a", latency_ms=100)
        self._add_metric(service, "tenant-a", latency_ms=200)
        self._add_metric(service, "tenant-b", latency_ms=999)

        result_a = service.get_latency_percentiles(tenant_id="tenant-a")
        result_b = service.get_latency_percentiles(tenant_id="tenant-b")

        assert result_a["count"] == 2
        assert result_b["count"] == 1

    def test_token_usage_filters_by_tenant(self, service):
        """Token usage grouped by model only shows tenant's data."""
        self._add_metric(service, "tenant-a", model="gpt-4o")
        self._add_metric(service, "tenant-a", model="claude-3.5")
        self._add_metric(service, "tenant-b", model="gpt-4o")

        result_a = service.get_token_usage(group_by="model", tenant_id="tenant-a")
        result_b = service.get_token_usage(group_by="model", tenant_id="tenant-b")

        assert len(result_a) == 2
        assert len(result_b) == 1

    def test_error_rate_filters_by_tenant(self, service):
        """Error rate calculation only includes tenant's data."""
        self._add_metric(service, "tenant-a", status="success")
        self._add_metric(service, "tenant-a", status="error")
        self._add_metric(service, "tenant-b", status="error")

        result_a = service.get_error_rate(tenant_id="tenant-a")
        result_b = service.get_error_rate(tenant_id="tenant-b")

        assert result_a["total_calls"] == 2
        assert result_a["error_count"] == 1
        assert result_a["error_rate"] == pytest.approx(0.5)

        assert result_b["total_calls"] == 1
        assert result_b["error_count"] == 1
        assert result_b["error_rate"] == pytest.approx(1.0)

    def test_dashboard_metrics_filters_by_tenant(self, service):
        """get_dashboard_metrics passes tenant_id through to all sub-methods."""
        self._add_metric(service, "tenant-a", model="gpt-4o")
        self._add_metric(service, "tenant-b", model="claude-3.5")

        result = service.get_dashboard_metrics(tenant_id="tenant-a")

        assert result["latency"]["count"] == 1
        assert result["error_rate"]["total_calls"] == 1
        assert "gpt-4o" in result["token_usage_by_model"]
        assert "claude-3.5" not in result["token_usage_by_model"]

    def test_no_tenant_id_returns_all(self, service):
        """When tenant_id is None, all metrics are included (backward compat)."""
        self._add_metric(service, "tenant-a")
        self._add_metric(service, "tenant-b")

        result = service.get_latency_percentiles(tenant_id=None)
        assert result["count"] == 2

    def test_empty_metrics(self, service):
        """Empty service returns zeros."""
        result = service.get_dashboard_metrics(tenant_id="nonexistent")

        assert result["latency"]["count"] == 0
        assert result["error_rate"]["total_calls"] == 0
        assert len(result["token_usage_by_model"]) == 0


class TestDashboardMetricsEndpoint:
    """Test GET /api/v1/observability/dashboard-metrics."""

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: handler called directly without db; gets a Depends object")
    @pytest.mark.asyncio
    async def test_dashboard_metrics_transforms_response(self):
        """Endpoint correctly transforms dict output to typed response."""
        from app.api.v1.observability_api import get_dashboard_metrics

        mock_service = MagicMock()
        mock_service.get_dashboard_metrics.return_value = {
            "latency": {"p50": 100, "p95": 200, "p99": 300, "count": 10, "mean": 150},
            "token_usage": {},
            "token_usage_by_model": {
                "gpt-4o": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500, "calls": 5},
            },
            "error_rate": {"total_calls": 10, "error_count": 1, "error_rate": 0.1, "success_rate": 0.9},
            "tool_success_rate": {},
            "window_minutes": 60,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        tenant_id = uuid4()
        mock_user = MagicMock()

        with patch("app.api.v1.observability_api.get_observability_service", return_value=mock_service):
            response = await get_dashboard_metrics(
                window_minutes=60,
                user=mock_user,
                tenant_id=tenant_id,
            )

        assert response.latency.p50 == 100
        assert response.latency.p95 == 200
        assert len(response.token_usage_by_model) == 1
        assert response.token_usage_by_model[0].group == "gpt-4o"
        assert response.error_rate.error_rate == pytest.approx(0.1)

        # Verify tenant_id was passed
        mock_service.get_dashboard_metrics.assert_called_once_with(60, tenant_id=str(tenant_id))

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: handler called directly without db; gets a Depends object")
    @pytest.mark.asyncio
    async def test_dashboard_metrics_empty(self):
        """Endpoint handles empty data correctly."""
        from app.api.v1.observability_api import get_dashboard_metrics

        mock_service = MagicMock()
        mock_service.get_dashboard_metrics.return_value = {
            "latency": {"p50": 0, "p95": 0, "p99": 0, "count": 0, "mean": 0},
            "token_usage": {},
            "token_usage_by_model": {},
            "error_rate": {"total_calls": 0, "error_count": 0, "error_rate": 0, "success_rate": 1},
            "tool_success_rate": {},
            "window_minutes": 60,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch("app.api.v1.observability_api.get_observability_service", return_value=mock_service):
            response = await get_dashboard_metrics(
                window_minutes=60,
                user=MagicMock(),
                tenant_id=uuid4(),
            )

        assert response.latency.count == 0
        assert len(response.token_usage_by_model) == 0


class TestTraceSearchEndpoint:
    """Test GET /api/v1/observability/traces."""

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: MagicMock LLM records fail pydantic validation")
    @pytest.mark.asyncio
    async def test_trace_search_with_results(self):
        """Search returns formatted trace results."""
        from app.api.v1.observability_api import search_traces

        job_id = uuid4()
        wf_id = uuid4()
        tenant_id = uuid4()
        now = datetime.now(timezone.utc)

        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.workflow_id = wf_id
        mock_job.workflow.name = "KYC Screening"
        mock_job.status = "completed"
        mock_job.created_at = now - timedelta(minutes=5)
        mock_job.started_at = now - timedelta(minutes=5)
        mock_job.completed_at = now - timedelta(minutes=4)
        mock_job.execution_trace = [{"step_id": "1"}, {"step_id": "2"}]

        # Mock count query
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        # Mock data query
        mock_data_result = MagicMock()
        mock_data_result.scalars.return_value.all.return_value = [mock_job]

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_count_result, mock_data_result]

        response = await search_traces(
            status=None,
            workflow_id=None,
            date_from=None,
            date_to=None,
            offset=0,
            limit=50,
            db=mock_db,
            user=MagicMock(),
            tenant_id=tenant_id,
        )

        assert response.total == 1
        assert len(response.traces) == 1
        assert response.traces[0].run_id == job_id
        assert response.traces[0].workflow_name == "KYC Screening"
        assert response.traces[0].total_steps == 2
        assert response.traces[0].duration_ms is not None

    @pytest.mark.asyncio
    async def test_trace_search_empty(self):
        """Search with no results returns empty list."""
        from app.api.v1.observability_api import search_traces

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_data_result = MagicMock()
        mock_data_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_count_result, mock_data_result]

        response = await search_traces(
            status=None,
            workflow_id=None,
            date_from=None,
            date_to=None,
            offset=0,
            limit=50,
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        assert response.total == 0
        assert len(response.traces) == 0

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: MagicMock LLM records fail pydantic validation")
    @pytest.mark.asyncio
    async def test_trace_search_null_execution_trace(self):
        """Job with null execution_trace reports 0 steps."""
        from app.api.v1.observability_api import search_traces

        mock_job = MagicMock()
        mock_job.id = uuid4()
        mock_job.workflow_id = uuid4()
        mock_job.workflow.name = "Test"
        mock_job.status = "running"
        mock_job.created_at = datetime.now(timezone.utc)
        mock_job.started_at = None
        mock_job.completed_at = None
        mock_job.execution_trace = None

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        mock_data_result = MagicMock()
        mock_data_result.scalars.return_value.all.return_value = [mock_job]

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_count_result, mock_data_result]

        response = await search_traces(
            status=None, workflow_id=None,
            date_from=None, date_to=None,
            offset=0, limit=50,
            db=mock_db, user=MagicMock(),
            tenant_id=uuid4(),
        )

        assert response.traces[0].total_steps == 0
        assert response.traces[0].duration_ms is None


class TestBulkAgentHealth:
    """Test GET /api/v1/observability/agent-health."""

    @pytest.mark.asyncio
    async def test_agent_health_no_agents(self):
        """Empty tenant returns empty list."""
        from app.api.v1.observability_api import get_all_agents_health

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        response = await get_all_agents_health(
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        assert response.total == 0
        assert len(response.agents) == 0

    @pytest.mark.asyncio
    async def test_agent_health_with_agents(self):
        """Returns combined circuit breaker + accuracy data."""
        from app.api.v1.observability_api import get_all_agents_health

        agent_id = uuid4()
        tenant_id = uuid4()

        mock_agent = MagicMock()
        mock_agent.id = agent_id
        mock_agent.name = "KYC Screener"
        mock_agent.agent_type = "custom"

        mock_acc = MagicMock()
        mock_acc.agent_id = agent_id
        mock_acc.accuracy = 0.95
        mock_acc.accuracy_delta = -0.02
        mock_acc.evaluated_at = datetime.now(timezone.utc)

        # Mock agents query
        mock_agents_result = MagicMock()
        mock_agents_result.scalars.return_value.all.return_value = [mock_agent]

        # Mock accuracy query
        mock_acc_result = MagicMock()
        mock_acc_result.scalars.return_value.all.return_value = [mock_acc]

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [mock_agents_result, mock_acc_result]

        mock_redis = AsyncMock()
        mock_redis.mget.return_value = [b"healthy"]

        mock_breaker = MagicMock()

        with patch("app.api.v1.observability_api.get_redis_service", return_value=mock_redis), \
             patch("app.api.v1.observability_api.AgentCircuitBreaker") as MockBreaker:
            mock_breaker_instance = AsyncMock()
            mock_breaker_instance.get_bulk_status.return_value = {
                str(agent_id): {"state": "healthy"}
            }
            MockBreaker.return_value = mock_breaker_instance

            response = await get_all_agents_health(
                db=mock_db,
                user=MagicMock(),
                tenant_id=tenant_id,
            )

        assert response.total == 1
        assert response.agents[0].agent_name == "KYC Screener"
        assert response.agents[0].circuit_breaker_status == "healthy"
        assert response.agents[0].accuracy == 0.95
        assert response.agents[0].has_drift is False  # -0.02 > -0.05


class TestBulkMGET:
    """Test AgentCircuitBreaker.get_bulk_status."""

    @pytest.mark.asyncio
    async def test_bulk_status_mixed(self):
        """Bulk status returns healthy for unknown and degraded for tripped."""
        redis = AsyncMock()
        # Simulate two agents: first healthy (no key), second degraded
        redis.mget.return_value = [None, b"degraded"]

        breaker = AgentCircuitBreaker(redis)
        agent1 = uuid4()
        agent2 = uuid4()

        result = await breaker.get_bulk_status([agent1, agent2])

        assert result[str(agent1)]["state"] == "healthy"
        assert result[str(agent2)]["state"] == "degraded"

    @pytest.mark.asyncio
    async def test_bulk_status_empty(self):
        """Empty list returns empty dict."""
        redis = AsyncMock()
        breaker = AgentCircuitBreaker(redis)

        result = await breaker.get_bulk_status([])
        assert result == {}


# Need to import here for the MGET tests
from app.services.agent_circuit_breaker import AgentCircuitBreaker
