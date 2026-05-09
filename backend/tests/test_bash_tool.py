"""Unit tests for bash_tool security and functionality.

Tests for:
- Security validation (path traversal, dangerous commands)
- Sandboxed execution
- Timeout handling
- Output truncation
- Feature flag enforcement
"""

import pytest
from pathlib import Path
from unittest.mock import patch
import tempfile

from app.agents.tools.bash_tool import (
    is_unsafe_command,
    get_safe_env,
    execute_bash,
    bash_tool,
    BashToolConfig,
)


# ============================================================================
# Security Validation Tests
# ============================================================================

class TestSecurityValidation:
    """Tests for command security validation."""

    def test_path_traversal_blocked(self):
        """Test that path traversal attempts are blocked."""
        dangerous_commands = [
            "cat ../../../etc/passwd",
            "ls /../../root",
            "cd ../ && cat secret.txt",
        ]
        for cmd in dangerous_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert is_unsafe, f"Should block: {cmd}"
            assert "path traversal" in reason.lower() or "pattern" in reason.lower()

    def test_privilege_escalation_blocked(self):
        """Test that privilege escalation is blocked."""
        dangerous_commands = [
            "sudo rm -rf /",
            "su root",
            "sudo apt-get install",
        ]
        for cmd in dangerous_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert is_unsafe, f"Should block: {cmd}"

    def test_dangerous_deletions_blocked(self):
        """Test that dangerous deletion commands are blocked."""
        dangerous_commands = [
            "rm -rf /",
            "rm -fr /home",
            "rm -rf ~",
        ]
        for cmd in dangerous_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert is_unsafe, f"Should block: {cmd}"

    def test_network_backdoors_blocked(self):
        """Test that network backdoor tools are blocked."""
        dangerous_commands = [
            "nc -l 4444",
            "netcat 192.168.1.1 80",
            "ncat --listen 8080",
        ]
        for cmd in dangerous_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert is_unsafe, f"Should block: {cmd}"

    def test_shell_escapes_blocked(self):
        """Test that shell escape attempts are blocked."""
        dangerous_commands = [
            "sh -c 'rm -rf /'",
            "bash -c 'cat /etc/shadow'",
        ]
        for cmd in dangerous_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert is_unsafe, f"Should block: {cmd}"

    def test_safe_commands_allowed(self):
        """Test that safe commands are allowed."""
        safe_commands = [
            "cat file.txt",
            "ls -la",
            "grep pattern file.txt",
            "wc -l data.csv",
            "head -10 output.txt",
            "tail -f log.txt",
            "awk '{print $1}' data.txt",
            "sed 's/old/new/g' file.txt",
            "python -c 'print(1+1)'",
            "jq '.results' data.json",
            "curl -s https://api.example.com/data",
        ]
        for cmd in safe_commands:
            is_unsafe, reason = is_unsafe_command(cmd)
            assert not is_unsafe, f"Should allow: {cmd} (blocked with: {reason})"


class TestSafeEnvironment:
    """Tests for safe environment creation."""

    def test_minimal_path(self):
        """Test that PATH is minimal."""
        session_path = Path("/tmp/test_session")
        env = get_safe_env(session_path)

        assert "PATH" in env
        # Should not include /usr/sbin or other admin paths
        assert "/sbin" not in env["PATH"]
        assert "/usr/sbin" not in env["PATH"]

    def test_pythonpath_cleared(self):
        """Test that PYTHONPATH is cleared."""
        session_path = Path("/tmp/test_session")
        env = get_safe_env(session_path)

        assert env["PYTHONPATH"] == ""

    def test_home_set_to_session(self):
        """Test that HOME is set to session directory."""
        session_path = Path("/tmp/test_session")
        env = get_safe_env(session_path)

        assert env["HOME"] == str(session_path)

    def test_session_path_in_env(self):
        """Test that SESSION_PATH is available."""
        session_path = Path("/tmp/test_session")
        env = get_safe_env(session_path)

        assert env["SESSION_PATH"] == str(session_path)


# ============================================================================
# Execution Tests
# ============================================================================

class TestBashExecution:
    """Tests for bash command execution."""

    @pytest.mark.asyncio
    async def test_feature_flag_disabled(self):
        """Test that execution fails when feature flag is disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(session_path=Path(tmpdir))

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = False

                result = await execute_bash("echo hello", config)

                assert not result.success
                assert "disabled" in result.output.lower()

    @pytest.mark.asyncio
    async def test_simple_command_execution(self):
        """Test executing a simple command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(session_path=Path(tmpdir))

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                result = await execute_bash("echo hello", config)

                assert result.success
                assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_working_directory_within_session(self):
        """Test that working directory stays within session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir)
            subdir = session_path / "subdir"
            subdir.mkdir()

            config = BashToolConfig(session_path=session_path)

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                result = await execute_bash("pwd", config, working_dir="subdir")

                assert result.success
                assert "subdir" in result.output

    @pytest.mark.asyncio
    async def test_working_directory_escape_blocked(self):
        """Test that escaping session directory is blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(session_path=Path(tmpdir))

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                result = await execute_bash("pwd", config, working_dir="../..")

                assert not result.success
                assert "within session" in result.output.lower()

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        """Test that commands are timed out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(
                session_path=Path(tmpdir),
                timeout_seconds=1
            )

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                # This command should timeout
                result = await execute_bash("sleep 10", config)

                assert not result.success
                assert "timed out" in result.output.lower()

    @pytest.mark.asyncio
    async def test_output_truncation(self):
        """Test that large output is truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(
                session_path=Path(tmpdir),
                max_output_chars=100
            )

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                # Generate large output
                result = await execute_bash("seq 1 1000", config)

                assert result.truncated
                assert "[Output truncated" in result.output

    @pytest.mark.asyncio
    async def test_file_operations_in_session(self):
        """Test file operations work within session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir)
            test_file = session_path / "test.txt"
            test_file.write_text("hello world")

            config = BashToolConfig(session_path=session_path)

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                result = await execute_bash("cat test.txt", config)

                assert result.success
                assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked(self):
        """Test that dangerous commands are blocked before execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = BashToolConfig(session_path=Path(tmpdir))

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                result = await execute_bash("sudo rm -rf /", config)

                assert not result.success
                assert "blocked" in result.output.lower()


# ============================================================================
# Bash Tool Wrapper Tests
# ============================================================================

class TestBashToolWrapper:
    """Tests for the high-level bash_tool function."""

    @pytest.mark.asyncio
    async def test_bash_tool_returns_string(self):
        """Test that bash_tool returns string output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                output = await bash_tool("echo test", Path(tmpdir))

                assert isinstance(output, str)
                assert "test" in output

    @pytest.mark.asyncio
    async def test_bash_tool_with_working_dir(self):
        """Test bash_tool with custom working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir)
            subdir = session_path / "data"
            subdir.mkdir()
            (subdir / "file.txt").write_text("content")

            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                output = await bash_tool(
                    "cat file.txt",
                    session_path,
                    working_dir="data"
                )

                assert "content" in output

    @pytest.mark.asyncio
    async def test_bash_tool_with_timeout(self):
        """Test bash_tool with custom timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('app.agents.tools.bash_tool.get_settings') as mock_settings:
                mock_settings.return_value.USE_BASH_TOOL = True

                output = await bash_tool(
                    "sleep 5",
                    Path(tmpdir),
                    timeout_seconds=1
                )

                assert "timed out" in output.lower()
