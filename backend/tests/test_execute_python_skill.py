"""
Unit tests for execute_python skill (E2B sandbox version).

Tests cover:
- E2B path: ReviewTicketCreate construction matches actual interface
- E2B path: successful review ticket creation
- E2B path: missing session_id returns error
- E2B path: ReviewQueueService failure handled gracefully
- Legacy fallback when ENABLE_SANDBOX=False
- Legacy fallback when E2B_API_KEY is empty
- Empty code rejection
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.skills.context import SkillContext
from app.skills.execute_python.tool import execute_python


@pytest.fixture
def skill_ctx():
    """Create a SkillContext with all required fields for E2B path."""
    return SkillContext(
        user_id="user-123",
        tenant_id="tenant-456",
        session_id="session-789",
        metadata={"agent_id": "analysis-agent"},
        db=MagicMock(),
    )


@pytest.fixture
def skill_ctx_no_session():
    """SkillContext without session_id."""
    return SkillContext(
        user_id="user-123",
        tenant_id="tenant-456",
    )


@pytest.fixture
def mock_e2b_settings():
    """Mock settings with E2B sandbox enabled."""
    with patch("app.skills.execute_python.tool.get_settings") as mock:
        settings = MagicMock()
        settings.ENABLE_SANDBOX = True
        settings.E2B_API_KEY = "test-e2b-key"
        mock.return_value = settings
        yield settings


@pytest.fixture
def mock_legacy_settings():
    """Mock settings with sandbox disabled (legacy fallback)."""
    with patch("app.skills.execute_python.tool.get_settings") as mock:
        settings = MagicMock()
        settings.ENABLE_SANDBOX = False
        settings.E2B_API_KEY = ""
        mock.return_value = settings
        yield settings


class TestEmptyCode:
    """Tests for empty code rejection."""

    @pytest.mark.asyncio
    async def test_empty_string(self, skill_ctx, mock_e2b_settings):
        result = await execute_python(skill_ctx, "")
        assert not result["success"]
        assert "No code" in result["error"]

    @pytest.mark.asyncio
    async def test_whitespace_only(self, skill_ctx, mock_e2b_settings):
        result = await execute_python(skill_ctx, "   \n  ")
        assert not result["success"]
        assert "No code" in result["error"]


class TestE2BPath:
    """Tests for E2B sandbox execution path."""

    @pytest.mark.asyncio
    async def test_creates_review_ticket(self, skill_ctx, mock_e2b_settings):
        """Review ticket is created with correct fields matching ReviewTicketCreate interface."""
        mock_ticket = MagicMock()
        mock_ticket.id = "ticket-abc"

        mock_service = AsyncMock()
        mock_service.create_ticket.return_value = mock_ticket

        with patch("app.services.review_queue.ReviewQueueService", return_value=mock_service) as MockQueue:
            with patch("app.services.review_queue.ReviewTicketCreate") as MockCreate:
                MockCreate.return_value = MagicMock()

                result = await execute_python(skill_ctx, "print('hello')")

                # Verify ReviewTicketCreate was called with correct fields
                MockCreate.assert_called_once()
                call_kwargs = MockCreate.call_args[1]
                assert call_kwargs["risk_level"] == "high"
                assert call_kwargs["review_type"] == "code_execution"
                assert call_kwargs["session_id"] == "session-789"
                assert call_kwargs["agent_id"] == "analysis-agent"
                assert call_kwargs["review_data"]["code"] == "print('hello')"
                assert call_kwargs["review_data"]["input_files"] == []

                # Verify create_ticket was called
                mock_service.create_ticket.assert_called_once_with(
                    tenant_id="tenant-456",
                    data=MockCreate.return_value,
                    requested_by="user-123",
                )

        assert result["success"]
        assert result["status"] == "pending_review"
        assert result["ticket_id"] == "ticket-abc"

    @pytest.mark.asyncio
    async def test_includes_input_files(self, skill_ctx, mock_e2b_settings):
        """Input files are passed through to review_data."""
        mock_ticket = MagicMock()
        mock_ticket.id = "ticket-xyz"

        mock_service = AsyncMock()
        mock_service.create_ticket.return_value = mock_ticket

        with patch("app.services.review_queue.ReviewQueueService", return_value=mock_service):
            with patch("app.services.review_queue.ReviewTicketCreate") as MockCreate:
                MockCreate.return_value = MagicMock()

                await execute_python(
                    skill_ctx,
                    "import pandas",
                    input_files=["data.csv", "config.json"],
                )

                call_kwargs = MockCreate.call_args[1]
                assert call_kwargs["review_data"]["input_files"] == ["data.csv", "config.json"]

    @pytest.mark.asyncio
    async def test_no_session_returns_error(self, skill_ctx_no_session, mock_e2b_settings):
        """Missing session_id produces a clear error."""
        result = await execute_python(skill_ctx_no_session, "print('hello')")
        assert not result["success"]
        assert "session" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_review_queue_failure(self, skill_ctx, mock_e2b_settings):
        """Exception during ticket creation is caught and returned as error."""
        with patch(
            "app.services.review_queue.ReviewQueueService",
            side_effect=Exception("DB connection failed"),
        ):
            result = await execute_python(skill_ctx, "print('hello')")

        assert not result["success"]
        assert "review" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_title_truncates_long_code(self, skill_ctx, mock_e2b_settings):
        """Title truncates code longer than 80 chars."""
        long_code = "x = " + "a" * 200

        mock_ticket = MagicMock()
        mock_ticket.id = "ticket-trunc"

        mock_service = AsyncMock()
        mock_service.create_ticket.return_value = mock_ticket

        with patch("app.services.review_queue.ReviewQueueService", return_value=mock_service):
            with patch("app.services.review_queue.ReviewTicketCreate") as MockCreate:
                MockCreate.return_value = MagicMock()

                await execute_python(skill_ctx, long_code)

                call_kwargs = MockCreate.call_args[1]
                assert len(call_kwargs["title"]) < len(long_code) + 50


class TestLegacyFallback:
    """Tests for legacy in-process sandbox fallback."""

    @pytest.mark.asyncio
    async def test_fallback_when_sandbox_disabled(self, skill_ctx, mock_legacy_settings):
        """Falls back to legacy when ENABLE_SANDBOX=False."""
        with patch(
            "app.skills.execute_python.tool._execute_legacy",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "42"},
        ) as mock_legacy:
            result = await execute_python(skill_ctx, "print(42)")

            mock_legacy.assert_called_once_with(skill_ctx, "print(42)", 30)
            assert result["success"]

    @pytest.mark.asyncio
    async def test_fallback_when_no_api_key(self, skill_ctx):
        """Falls back to legacy when E2B_API_KEY is empty."""
        with patch("app.skills.execute_python.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.E2B_API_KEY = ""
            mock_settings.return_value = settings

            with patch(
                "app.skills.execute_python.tool._execute_legacy",
                new_callable=AsyncMock,
                return_value={"success": True, "output": "ok"},
            ) as mock_legacy:
                result = await execute_python(skill_ctx, "print('ok')")
                mock_legacy.assert_called_once()

    @pytest.mark.asyncio
    async def test_legacy_timeout_capped(self, skill_ctx, mock_legacy_settings):
        """Legacy timeout is capped at 30 seconds."""
        with patch(
            "app.skills.execute_python.tool._execute_legacy",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_legacy:
            await execute_python(skill_ctx, "x=1", timeout=120)

            # timeout should be min(120, 30) = 30
            mock_legacy.assert_called_once_with(skill_ctx, "x=1", 30)

    @pytest.mark.asyncio
    async def test_legacy_delegates_to_tool_legacy(self, skill_ctx, mock_legacy_settings):
        """_execute_legacy delegates to tool_legacy.execute_python."""
        with patch(
            "app.skills.execute_python.tool_legacy.execute_python",
            new_callable=AsyncMock,
            return_value={"success": True, "output": "legacy"},
        ) as mock_tool:
            from app.skills.execute_python.tool import _execute_legacy
            result = await _execute_legacy(skill_ctx, "print('hi')", 15)
            mock_tool.assert_called_once_with(skill_ctx, "print('hi')", 15)
            assert result["output"] == "legacy"
