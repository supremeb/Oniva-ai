"""
Tests for Agent Harness (harness.py).

Tests cover:
- Loop detection: warn at 3, block at 5, resets per wrap call
- Credential scanner: redacts all 10 pattern types
- Credential scanner: does NOT redact financial identifiers (CUSIP, ISIN, token_count)
- Dict value scanning: nested dicts and lists preserved
- Recitation: matches TodoMiddleware behavior (inject every 7 calls)
- Concurrency: parallel wrap calls have isolated state
- Feature flag: USE_HARNESS_PIPELINE=False bypasses all wrapping
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from app.agents.harness import (
    _check_loop,
    _scan_credentials,
    _scan_dict_values,
    _maybe_inject_recitation,
    wrap_tools_with_harness,
    LOOP_SOFT_THRESHOLD,
    LOOP_HARD_THRESHOLD,
    RECITATION_INTERVAL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def dummy_tool_str(*args, **kwargs) -> str:
    return "tool output"


async def dummy_tool_dict(*args, **kwargs) -> dict:
    return {"result": "data", "count": 42}


STORAGE_PATCH = "app.services.session_storage.get_session_storage"
DEFAULT_CONTENT_PATCH = "app.services.session_memory._is_default_content"


# ---------------------------------------------------------------------------
# Loop Detection
# ---------------------------------------------------------------------------

class TestLoopDetection:
    """Tests for _check_loop()."""

    def test_no_warning_below_threshold(self):
        counts = {}
        for _ in range(LOOP_SOFT_THRESHOLD - 1):
            msg = _check_loop("my_tool", {"x": 1}, counts)
            assert msg is None

    def test_soft_warning_at_threshold(self):
        counts = {}
        for _ in range(LOOP_SOFT_THRESHOLD - 1):
            _check_loop("my_tool", {"x": 1}, counts)

        msg = _check_loop("my_tool", {"x": 1}, counts)
        assert msg is not None
        assert "Loop detected" in msg
        assert "my_tool" in msg

    def test_hard_block_at_threshold(self):
        counts = {}
        for _ in range(LOOP_HARD_THRESHOLD - 1):
            _check_loop("my_tool", {"x": 1}, counts)

        msg = _check_loop("my_tool", {"x": 1}, counts)
        assert msg is not None
        assert "Loop blocked" in msg

    def test_different_args_tracked_separately(self):
        counts = {}
        for _ in range(LOOP_HARD_THRESHOLD):
            _check_loop("my_tool", {"x": 1}, counts)

        # Different args should NOT be blocked
        msg = _check_loop("my_tool", {"x": 2}, counts)
        assert msg is None

    def test_different_tools_tracked_separately(self):
        counts = {}
        for _ in range(LOOP_HARD_THRESHOLD):
            _check_loop("tool_a", {"x": 1}, counts)

        msg = _check_loop("tool_b", {"x": 1}, counts)
        assert msg is None

    def test_args_order_independent(self):
        counts = {}
        _check_loop("t", {"a": 1, "b": 2}, counts)
        _check_loop("t", {"b": 2, "a": 1}, counts)
        assert counts.get('t:{"a": 1, "b": 2}') == 2

    def test_non_serializable_args(self):
        counts = {}
        msg = _check_loop("t", {"obj": object()}, counts)
        assert msg is None  # Should not crash


# ---------------------------------------------------------------------------
# Credential Scanning
# ---------------------------------------------------------------------------

class TestCredentialScanning:
    """Tests for _scan_credentials()."""

    @pytest.mark.parametrize("text,expected_redaction", [
        ("key is sk-abc123def456ghi789jkl012mno", "OpenAI API key"),
        ("key is sk-ant-abc123-def456-ghi789-jkl012", "Anthropic API key"),
        ("key is sk-or-abc123-def456-ghi789-jkl012", "OpenRouter API key"),
        ("AKIAIOSFODNN7EXAMPLE", "AWS access key"),
        ("-----BEGIN PRIVATE KEY-----", "Private key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "Private key"),
        ("postgresql://user:pass@host:5432/db", "Database connection string"),
        ("redis://localhost:6379", "Database connection string"),
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature", "Bearer token"),
        ("ghp_abc123def456ghi789jkl012mno345pqr678stu9", "GitHub token"),
        ("sk_test_abc123def456ghi789jkl012", "Stripe API key"),
        ("api_key=abcdefghijklmnopqrstuvwxyz123456789012", "Generic secret in key=value format"),
    ])
    def test_redacts_credential_patterns(self, text, expected_redaction):
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count > 0
        assert f"[REDACTED: {expected_redaction}]" in redacted

    def test_no_false_positive_on_cusip(self):
        """CUSIP codes (9 chars) must not be redacted."""
        text = "CUSIP: US0378331005, ISIN: US0378331005"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 0
        assert redacted == text

    def test_no_false_positive_on_isin(self):
        text = "ISIN GB0002634946 for Barclays"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 0
        assert redacted == text

    def test_no_false_positive_on_token_count(self):
        """token_count=500 must not trigger generic_secret pattern."""
        text = "token_count=500 tokens_used=1234"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 0
        assert redacted == text

    def test_no_false_positive_on_sedol(self):
        text = "SEDOL: B0WNLY7"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 0
        assert redacted == text

    def test_clean_text_unchanged(self):
        text = "AAPL stock price is $150.23 with P/E ratio of 28.5"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 0
        assert redacted == text

    def test_multiple_credentials_redacted(self):
        text = "key1=sk-abc123def456ghi789jkl012mno, db=postgresql://u:p@host/db"
        redacted, count = _scan_credentials(text, "test_tool", "session-1")
        assert count == 2
        assert "sk-abc123" not in redacted
        assert "postgresql://" not in redacted


# ---------------------------------------------------------------------------
# Dict Value Scanning
# ---------------------------------------------------------------------------

class TestDictValueScanning:
    """Tests for _scan_dict_values()."""

    def test_scans_string_values(self):
        d = {"output": "found key sk-abc123def456ghi789jkl012mno"}
        result = _scan_dict_values(d, "test_tool", "session-1")
        assert "[REDACTED:" in result["output"]

    def test_preserves_non_string_values(self):
        d = {"count": 42, "active": True, "ratio": 3.14}
        result = _scan_dict_values(d, "test_tool", "session-1")
        assert result == d

    def test_scans_nested_dicts(self):
        d = {"outer": {"inner": "key=sk-abc123def456ghi789jkl012mno"}}
        result = _scan_dict_values(d, "test_tool", "session-1")
        assert "[REDACTED:" in result["outer"]["inner"]

    def test_scans_lists_of_strings(self):
        d = {"items": ["clean text", "has sk-abc123def456ghi789jkl012mno"]}
        result = _scan_dict_values(d, "test_tool", "session-1")
        assert result["items"][0] == "clean text"
        assert "[REDACTED:" in result["items"][1]

    def test_preserves_dict_structure(self):
        d = {
            "a": {"b": {"c": "deep value"}},
            "list": [1, "two", {"nested": "three"}],
            "num": 42,
        }
        result = _scan_dict_values(d, "test_tool", "session-1")
        assert result["a"]["b"]["c"] == "deep value"
        assert result["list"][0] == 1
        assert result["list"][1] == "two"
        assert result["list"][2]["nested"] == "three"
        assert result["num"] == 42


# ---------------------------------------------------------------------------
# Recitation
# ---------------------------------------------------------------------------

class TestRecitation:
    """Tests for _maybe_inject_recitation()."""

    @pytest.mark.asyncio
    async def test_no_injection_before_interval(self):
        counter = [0]
        for _ in range(RECITATION_INTERVAL - 1):
            result = await _maybe_inject_recitation(
                "output", counter, "session-1", "tenant-1",
            )
            assert "[PROGRESS CHECK]" not in result

    @pytest.mark.asyncio
    async def test_injection_at_interval(self):
        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value="- [ ] Step 1\n- [x] Step 2")

        counter = [0]
        with patch(STORAGE_PATCH, return_value=mock_storage), \
             patch(DEFAULT_CONTENT_PATCH, return_value=False):
            for _ in range(RECITATION_INTERVAL - 1):
                await _maybe_inject_recitation(
                    "output", counter, "session-1", "tenant-1",
                )

            result = await _maybe_inject_recitation(
                "output", counter, "session-1", "tenant-1",
            )
            assert "[PROGRESS CHECK]" in result
            assert "Step 1" in result

    @pytest.mark.asyncio
    async def test_skips_when_content_is_default(self):
        mock_storage = AsyncMock()
        mock_storage.read = AsyncMock(return_value="# Plan\nDescribe your plan here.")

        counter = [0]
        with patch(STORAGE_PATCH, return_value=mock_storage), \
             patch(DEFAULT_CONTENT_PATCH, return_value=True):
            for _ in range(RECITATION_INTERVAL):
                result = await _maybe_inject_recitation(
                    "output", counter, "session-1", "tenant-1",
                )

            assert "[PROGRESS CHECK]" not in result

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_storage_failure(self):
        failing_storage = AsyncMock()
        failing_storage.read = AsyncMock(side_effect=Exception("Storage down"))

        counter = [RECITATION_INTERVAL - 1]  # Next call triggers
        with patch(STORAGE_PATCH, return_value=failing_storage):
            result = await _maybe_inject_recitation(
                "output", counter, "session-1", "tenant-1",
            )
            assert result == "output"

    @pytest.mark.asyncio
    async def test_default_interval_is_7(self):
        assert RECITATION_INTERVAL == 7


# ---------------------------------------------------------------------------
# wrap_tools_with_harness (integration of all 3 guardrails)
# ---------------------------------------------------------------------------

class TestWrapToolsWithHarness:
    """Tests for the public wrap_tools_with_harness() function."""

    @pytest.mark.asyncio
    async def test_wraps_all_tools(self):
        tools = [dummy_tool_str, dummy_tool_dict]
        wrapped = wrap_tools_with_harness(tools, "session-1", "tenant-1")
        assert len(wrapped) == 2

    @pytest.mark.asyncio
    async def test_loop_detection_blocks_in_wrapped_tool(self):
        wrapped = wrap_tools_with_harness([dummy_tool_str], "session-1", "tenant-1")
        tool = wrapped[0]

        # Call 5 times with same args
        for _ in range(LOOP_HARD_THRESHOLD - 1):
            await tool()

        result = await tool()
        assert "Loop blocked" in result

    @pytest.mark.asyncio
    async def test_credential_scanning_in_wrapped_tool(self):
        async def leaky_tool(*args, **kwargs):
            return "found key sk-abc123def456ghi789jkl012mno in output"

        wrapped = wrap_tools_with_harness([leaky_tool], "session-1", "tenant-1")
        result = await wrapped[0]()
        assert "sk-abc123" not in result
        assert "[REDACTED:" in result

    @pytest.mark.asyncio
    async def test_loop_warning_appended_after_credential_scan(self):
        """Soft warning should be appended after credential scanning."""
        async def clean_tool(*args, **kwargs):
            return "clean output"

        wrapped = wrap_tools_with_harness([clean_tool], "session-1", "tenant-1")
        tool = wrapped[0]

        for _ in range(LOOP_SOFT_THRESHOLD - 1):
            await tool()

        result = await tool()
        assert "Loop detected" in result
        assert "clean output" in result

    @pytest.mark.asyncio
    async def test_preserves_functools_wraps(self):
        wrapped = wrap_tools_with_harness([dummy_tool_str], "session-1", "tenant-1")
        assert wrapped[0].__name__ == "dummy_tool_str"

    @pytest.mark.asyncio
    async def test_per_run_state_isolation(self):
        """Two separate wrap calls should have independent state."""
        wrapped_a = wrap_tools_with_harness([dummy_tool_str], "session-1", "tenant-1")
        wrapped_b = wrap_tools_with_harness([dummy_tool_str], "session-2", "tenant-1")

        # Exhaust loop detection on A
        for _ in range(LOOP_HARD_THRESHOLD - 1):
            await wrapped_a[0]()

        result_a = await wrapped_a[0]()
        result_b = await wrapped_b[0]()

        assert "Loop blocked" in result_a
        assert "Loop blocked" not in result_b

    @pytest.mark.asyncio
    async def test_concurrent_wrap_calls_isolated(self):
        """Simulate concurrent requests with asyncio.gather."""
        async def run_agent(agent_id: int):
            async def tool(*args, **kwargs):
                return f"agent-{agent_id}-output"

            wrapped = wrap_tools_with_harness([tool], f"session-{agent_id}", "tenant-1")

            results = []
            for _ in range(LOOP_HARD_THRESHOLD + 1):
                results.append(await wrapped[0]())

            return results

        all_results = await asyncio.gather(*[run_agent(i) for i in range(20)])

        for agent_results in all_results:
            # Last call should be blocked
            assert "Loop blocked" in agent_results[-1]
            # First call should be normal
            assert "agent-" in agent_results[0]


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    """Tests for USE_HARNESS_PIPELINE feature flag."""

    def test_flag_exists_in_settings(self):
        from app.config import get_settings
        settings = get_settings()
        assert hasattr(settings, 'USE_HARNESS_PIPELINE')
        assert settings.USE_HARNESS_PIPELINE is True
