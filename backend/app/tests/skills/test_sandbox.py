
import pytest
from unittest.mock import MagicMock, patch
from app.agents.skills.sandbox import SandboxExecutor

class TestSandboxExecutor:
    
    @patch("app.agents.skills.sandbox.docker")
    @pytest.mark.asyncio
    async def test_execute_success(self, mock_docker):
        # Mock Docker Client and Container
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.containers.create.return_value = mock_container
        
        # Mock execution result
        mock_container.wait.return_value = {'StatusCode': 0}
        mock_container.logs.return_value = b"Hello Sandbox"
        
        executor = SandboxExecutor()
        result = await executor.execute("print('Hello Sandbox')")
        
        assert result == "Hello Sandbox"
        mock_client.containers.create.assert_called_once()
        # Verify security settings
        created_kwargs = mock_client.containers.create.call_args[1]
        assert created_kwargs['network_mode'] == "none"
        assert created_kwargs['read_only'] is True
        assert created_kwargs['user'] == "nobody"

    @patch("app.agents.skills.sandbox.docker")
    @pytest.mark.asyncio
    async def test_execute_failure_exit_code(self, mock_docker):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_client.containers.create.return_value = mock_container
        
        mock_container.wait.return_value = {'StatusCode': 1}
        mock_container.logs.return_value = b"SyntaxError: invalid syntax"
        
        executor = SandboxExecutor()
        result = await executor.execute("bad code")
        
        assert "Error (Exit Code 1)" in result
        assert "SyntaxError" in result

    @patch("app.agents.skills.sandbox.docker")
    @pytest.mark.asyncio
    async def test_docker_not_available(self, mock_docker):
        mock_docker.errors.DockerException = Exception
        mock_docker.from_env.side_effect = Exception("Docker not found")
        
        executor = SandboxExecutor()
        assert executor.client is None
        
        with pytest.raises(RuntimeError):
            await executor.execute("print('fail')")
