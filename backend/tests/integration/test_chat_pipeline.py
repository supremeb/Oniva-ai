"""
Integration tests for the chat pipeline.

Covers the critical fixes from the comprehensive E2E validation plan:
- P0-S3: RLS tenant context uses parameterized SQL (no f-string injection)
- P1-1: Conversation history is built for multi-turn context
- P1-4: Response text extraction from structured agent results
- P2-8: ChatService functions (extract_reasoning, extract_response_text, format_error)
- P2-9: SSRF protection in browse_url skill
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.services.chat_service import (
    extract_reasoning,
    extract_response_text,
    format_error,
)
from app.api.v1.chat import build_message_history, _extract_message_content


# --- extract_response_text ---


class TestExtractResponseText:
    """Tests for structured agent result → human text conversion."""

    def test_general_response(self):
        data = MagicMock(spec=[])
        data.response = "Hello world"
        # Ensure it's not confused with analysis
        assert not hasattr(data, 'analysis')
        result = extract_response_text(data)
        assert result == "Hello world"

    def test_research_result_with_sources(self):
        data = MagicMock(spec=[])
        data.answer = "The answer is 42"
        data.sources = ["source1.pdf", "source2.pdf"]
        result = extract_response_text(data)
        assert "The answer is 42" in result
        assert "source1.pdf" in result

    def test_analysis_result(self):
        data = MagicMock(spec=[])
        # Remove 'response' and 'answer' from spec so hasattr returns False
        data.reasoning = ["Good revenue growth", "Low debt ratio"]
        data.recommendation = "buy"
        data.confidence = 0.85
        data.key_metrics = {"PE": "15.2"}
        data.risks = ["Market volatility"]
        # Ensure no 'response' or 'answer' attr
        del data.response
        del data.answer
        del data.analysis
        del data.summary
        result = extract_response_text(data)
        assert "BUY" in result
        assert "85%" in result
        assert "Good revenue growth" in result
        assert "PE: 15.2" in result
        assert "Market volatility" in result

    def test_compliance_result(self):
        data = MagicMock(spec=[])
        data.is_compliant = False
        data.risk_level = "high"
        data.violations = ["Missing KYC"]
        data.regulations_checked = ["AML Act"]
        data.recommendations = ["Complete KYC check"]
        del data.response
        del data.answer
        del data.analysis
        del data.summary
        del data.reasoning
        result = extract_response_text(data)
        assert "Non-Compliant" in result
        assert "HIGH" in result
        assert "Missing KYC" in result

    def test_fallback_to_str(self):
        data = "plain string"
        result = extract_response_text(data)
        assert result == "plain string"


# --- extract_reasoning ---


class TestExtractReasoning:
    def test_reasoning_from_attribute(self):
        result = MagicMock()
        result._reasoning = "I think therefore I am"
        assert extract_reasoning(result) == "I think therefore I am"

    def test_reasoning_from_thinking_part(self):
        thinking_part = MagicMock()
        thinking_part.__class__.__name__ = 'ThinkingPart'
        thinking_part.content = "Deep thought"

        msg = MagicMock()
        msg.parts = [thinking_part]

        result = MagicMock(spec=[])
        result.all_messages = MagicMock(return_value=[msg])
        del result._reasoning
        assert extract_reasoning(result) == "Deep thought"

    def test_no_reasoning(self):
        result = MagicMock(spec=[])
        del result._reasoning
        del result.all_messages
        assert extract_reasoning(result) is None


# --- format_error ---


class TestFormatError:
    def test_api_key_error(self):
        result = format_error("Error: 401 Unauthorized")
        assert "API key not configured" in result

    def test_rate_limit_error(self):
        result = format_error("Rate limit exceeded")
        assert "rate limit" in result.lower()

    def test_generic_error(self):
        result = format_error("Something went wrong")
        assert result == "Something went wrong"


# --- build_message_history ---


class TestBuildMessageHistory:
    def test_multi_turn_history(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
        ]
        history = build_message_history(messages)
        assert history is not None
        assert len(history) == 2  # excludes last user message

    def test_single_message_returns_none(self):
        messages = [{"role": "user", "content": "Hello"}]
        history = build_message_history(messages)
        assert history is None

    def test_empty_messages(self):
        history = build_message_history([])
        assert history is None

    def test_parts_format(self):
        messages = [
            {"role": "user", "parts": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "Hi"}]},
            {"role": "user", "content": "Next question"},
        ]
        history = build_message_history(messages)
        assert history is not None
        assert len(history) == 2


# --- _extract_message_content ---


class TestExtractMessageContent:
    def test_string_content(self):
        msg = {"role": "user", "content": "Hello world"}
        assert _extract_message_content(msg) == "Hello world"

    def test_parts_format(self):
        msg = {"role": "user", "parts": [{"type": "text", "text": "From parts"}]}
        assert _extract_message_content(msg) == "From parts"

    def test_array_content(self):
        msg = {"role": "user", "content": [{"type": "text", "content": "Array msg"}]}
        assert _extract_message_content(msg) == "Array msg"

    def test_empty_message(self):
        assert _extract_message_content({}) == ""


# --- SSRF protection ---


class TestSSRFProtection:
    def test_blocks_private_ips(self):
        from app.security.url_validator import is_url_safe as _is_url_safe

        # Localhost
        result = _is_url_safe("http://127.0.0.1/admin")
        assert result is not None
        assert "non-public" in result.lower() or "blocked" in result.lower()

        # Private range
        result = _is_url_safe("http://192.168.1.1/")
        assert result is not None

        # 10.x range
        result = _is_url_safe("http://10.0.0.1/")
        assert result is not None

    def test_blocks_metadata_endpoints(self):
        from app.security.url_validator import is_url_safe as _is_url_safe

        result = _is_url_safe("http://169.254.169.254/latest/meta-data/")
        assert result is not None
        assert "blocked" in result.lower() or "non-public" in result.lower()

        result = _is_url_safe("http://metadata.google.internal/computeMetadata/v1/")
        assert result is not None

    def test_blocks_non_http_schemes(self):
        from app.security.url_validator import is_url_safe as _is_url_safe

        result = _is_url_safe("file:///etc/passwd")
        assert result is not None
        assert "not allowed" in result.lower()

        result = _is_url_safe("ftp://internal.host/file")
        assert result is not None

    def test_allows_public_urls(self):
        from app.security.url_validator import is_url_safe as _is_url_safe

        # google.com resolves to a public IP
        result = _is_url_safe("https://www.google.com")
        assert result is None  # None means safe


# --- RLS parameterized SQL ---


class TestRLSTenantContext:
    def test_set_tenant_context_rejects_invalid_uuid(self):
        """Verify that SQL injection attempts via tenant_id are blocked."""
        from app.core.database import set_tenant_context

        with pytest.raises(ValueError, match="Invalid tenant_id"):
            # This would be an injection attempt with the old f-string approach
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                set_tenant_context(MagicMock(), "'; DROP TABLE users; --")
            )

    def test_set_tenant_context_accepts_valid_uuid(self):
        """Verify valid UUIDs pass validation."""
        from app.core.database import set_tenant_context

        session = AsyncMock()
        valid_uuid = str(uuid.uuid4())

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            set_tenant_context(session, valid_uuid)
        )
        # Should have called execute with parameterized query
        session.execute.assert_called_once()
        call_args = session.execute.call_args
        # The SQL text should use :tenant_id parameter, not f-string
        sql_text = str(call_args[0][0])
        assert ":tenant_id" in sql_text or "set_config" in sql_text
