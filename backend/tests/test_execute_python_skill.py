"""
Unit tests for the execute_python skill (E2B sandbox version).

U6: the skill no longer creates review tickets. Through the tool gateway (the engine already waited
for approval and `execute_tool` bound the call) it runs the code in the sandbox; anywhere else it
refuses and creates nothing.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agents.skills.context import SkillContext
from app.skills.execute_python import tool as ep
from app.skills.execute_python.tool import NEEDS_APPROVAL_REFUSAL, execute_python


class FakeSandbox:
    def __init__(self, success=True):
        self.success = success
        self.runs = []
        self.uploads = []
        self.torn_down = []

    async def get_or_create(self, session_id, worker_id=None, **kw):
        return self

    async def upload_files(self, files):
        self.uploads.append(files)

    async def execute_code(self, code, timeout=60):
        self.runs.append((code, timeout))
        return SimpleNamespace(success=self.success, stdout="out", stderr="" if self.success else "boom",
                               error=None if self.success else "boom", output_files=["output.png"],
                               duration_seconds=0.5)

    async def teardown(self, session_id, worker_id=None):
        self.torn_down.append((session_id, worker_id))


@pytest.fixture
def gateway_ctx():
    return SkillContext.for_gateway(tenant_id="tenant-456", user_id="user-123", project_id="p",
                                    conversation_id="c", run_id="run-1", tool_call_id="call-1")


@pytest.fixture
def plain_ctx():
    return SkillContext(user_id="user-123", tenant_id="tenant-456", session_id="session-789",
                        metadata={"agent_id": "analysis-agent"}, db=MagicMock())


@pytest.fixture
def e2b_on():
    with patch("app.skills.execute_python.tool.get_settings") as mock:
        mock.return_value = MagicMock(ENABLE_SANDBOX=True, E2B_API_KEY="test-e2b-key")
        yield


@pytest.fixture
def sandbox(monkeypatch):
    box = FakeSandbox()
    monkeypatch.setattr(ep, "_executor", lambda: box)
    return box


class TestGatewayRun:
    @pytest.mark.asyncio
    async def test_runs_code_in_sandbox_and_returns_output(self, gateway_ctx, e2b_on, sandbox):
        result = await execute_python(gateway_ctx, "print('hi')")
        assert result["success"] is True
        assert result["stdout"] == "out"
        assert result["output_files"] == ["output.png"]
        assert sandbox.runs == [("print('hi')", 60)]
        assert sandbox.torn_down == [("run-1", "exec-call-1")]
        assert "ticket_id" not in result and result.get("status") != "pending_review"

    @pytest.mark.asyncio
    async def test_timeout_is_clamped(self, gateway_ctx, e2b_on, sandbox):
        await execute_python(gateway_ctx, "x", timeout=600)
        assert sandbox.runs[0][1] == 60

    @pytest.mark.asyncio
    async def test_failed_code_reports_failure(self, gateway_ctx, e2b_on, monkeypatch):
        box = FakeSandbox(success=False)
        monkeypatch.setattr(ep, "_executor", lambda: box)
        result = await execute_python(gateway_ctx, "1/0")
        assert result["success"] is False and result["stderr"] == "boom"
        assert box.torn_down  # torn down even when the code failed

    @pytest.mark.asyncio
    async def test_sandbox_unavailable_is_an_error_not_a_crash(self, gateway_ctx, e2b_on, monkeypatch):
        class Broken:
            async def get_or_create(self, *a, **k):
                raise RuntimeError("SANDBOX_PROXY_URL is not set")

        monkeypatch.setattr(ep, "_executor", lambda: Broken())
        result = await execute_python(gateway_ctx, "print(1)")
        assert result.is_error and "SANDBOX_PROXY_URL" in result.content


class TestOutsideGateway:
    @pytest.mark.asyncio
    async def test_refuses_and_creates_nothing(self, plain_ctx, e2b_on, sandbox):
        result = await execute_python(plain_ctx, "print('hello')")
        assert result == {"success": False, "error": NEEDS_APPROVAL_REFUSAL}
        assert sandbox.runs == []


class TestEmptyCode:
    @pytest.mark.asyncio
    async def test_empty_code_rejected(self, gateway_ctx):
        result = await execute_python(gateway_ctx, "   ")
        assert not result["success"]


class TestSandboxDisabledRefusal:
    """With the sandbox off the skill refuses; there is no in-process fallback."""

    @pytest.mark.asyncio
    async def test_refuses_when_sandbox_disabled(self, gateway_ctx, sandbox):
        with patch("app.skills.execute_python.tool.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ENABLE_SANDBOX=False, E2B_API_KEY="")
            result = await execute_python(gateway_ctx, "print(42)")
        assert result.is_error
        assert "sandbox" in result.content.lower()
        assert sandbox.runs == []

    @pytest.mark.asyncio
    async def test_refuses_when_no_api_key(self, gateway_ctx, sandbox):
        with patch("app.skills.execute_python.tool.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(ENABLE_SANDBOX=True, E2B_API_KEY="")
            result = await execute_python(gateway_ctx, "print('ok')")
        assert result.is_error
        assert "sandbox" in result.content.lower()
        assert sandbox.runs == []
