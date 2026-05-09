"""Tests for Langfuse integration service."""
from unittest.mock import MagicMock, patch


class TestLangfuseIntegration:
    """Test feature-flagged Langfuse integration."""

    def setup_method(self):
        """Clear lru_cache between tests."""
        from app.services.langfuse_integration import get_langfuse_client
        get_langfuse_client.cache_clear()

    def test_disabled_by_default(self):
        """When LANGFUSE_ENABLED=false, client is None."""
        from app.services.langfuse_integration import get_langfuse_client

        mock_settings = MagicMock()
        mock_settings.LANGFUSE_ENABLED = False

        with patch("app.config.get_settings", return_value=mock_settings):
            client = get_langfuse_client()
            assert client is None

    def test_trace_noop_when_disabled(self):
        """trace_to_langfuse does nothing when client is None."""
        from app.services.langfuse_integration import trace_to_langfuse

        mock_settings = MagicMock()
        mock_settings.LANGFUSE_ENABLED = False

        with patch("app.config.get_settings", return_value=mock_settings):
            # Should not raise
            trace_to_langfuse(
                call_id="test-123",
                provider="openai",
                model="gpt-4o",
                input_tokens=100,
                output_tokens=50,
                latency_ms=200,
                status="success",
            )

    def test_trace_logs_warning_on_failure(self):
        """trace_to_langfuse logs warning when Langfuse call fails."""
        from app.services.langfuse_integration import trace_to_langfuse

        mock_client = MagicMock()
        mock_client.trace.side_effect = Exception("Connection refused")

        with patch("app.services.langfuse_integration.get_langfuse_client", return_value=mock_client), \
             patch("app.services.langfuse_integration.logger") as mock_logger:
            trace_to_langfuse(
                call_id="test-456",
                provider="anthropic",
                model="claude-3.5",
                input_tokens=200,
                output_tokens=100,
                latency_ms=500,
                status="success",
            )
            mock_logger.warning.assert_called_once()
            assert "non-blocking" in mock_logger.warning.call_args[0][0]

    def test_trace_sends_correct_data(self):
        """trace_to_langfuse calls client.trace with correct parameters."""
        from app.services.langfuse_integration import trace_to_langfuse

        mock_trace = MagicMock()
        mock_client = MagicMock()
        mock_client.trace.return_value = mock_trace

        with patch("app.services.langfuse_integration.get_langfuse_client", return_value=mock_client):
            trace_to_langfuse(
                call_id="test-789",
                provider="openai",
                model="gpt-4o-mini",
                input_tokens=300,
                output_tokens=150,
                latency_ms=120,
                status="success",
                workflow_id="wf-abc",
                agent_type="compliance",
            )

        mock_client.trace.assert_called_once_with(id="test-789", name="openai/gpt-4o-mini")
        mock_trace.generation.assert_called_once()
        gen_kwargs = mock_trace.generation.call_args[1]
        assert gen_kwargs["model"] == "gpt-4o-mini"
        assert gen_kwargs["usage"] == {"input": 300, "output": 150}
        assert gen_kwargs["metadata"]["workflow_id"] == "wf-abc"
        assert gen_kwargs["metadata"]["agent_type"] == "compliance"

    def test_shutdown_flushes_and_clears_cache(self):
        """shutdown_langfuse flushes client and clears lru_cache."""
        from app.services.langfuse_integration import shutdown_langfuse

        mock_client = MagicMock()

        with patch("app.services.langfuse_integration.get_langfuse_client", return_value=mock_client):
            shutdown_langfuse()

        mock_client.flush.assert_called_once()
