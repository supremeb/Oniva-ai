
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

# Mock mcp before importing registry if possible, or patch it
# Since registry imports mcp at top level (inside try/except), we rely on patching MCP_AVAILABLE
# or patching the modules before import.

from app.agents.skills.registry import SkillRegistry

@pytest.mark.asyncio
async def test_mcp_initialization_no_lib():
    """Test graceful degradation when MCP lib is missing"""
    with patch("app.agents.skills.registry.MCP_AVAILABLE", False):
        await SkillRegistry.initialize_mcp_clients()
        # Should not raise exception
        assert True

@pytest.mark.asyncio
async def test_mcp_initialization_with_lib():
    """Test initialization logic when MCP is available"""
    
    # Mock MCP components
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    # Create tool mock with proper name attribute
    tool_mock = MagicMock()
    tool_mock.name = "test_tool"
    tool_mock.description = "A test tool"
    tool_mock.inputSchema = {}
    
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool_mock]))
    
    mock_stdio_ctx = AsyncMock()
    mock_stdio_ctx.__aenter__.return_value = (MagicMock(), MagicMock())
    mock_stdio_ctx.__aexit__.return_value = None
    
    with patch("app.agents.skills.registry.MCP_AVAILABLE", True), \
         patch("app.agents.skills.registry.MCP_SERVERS_CONFIG", {"test-server": {"command": "echo", "args": []}}), \
         patch("app.agents.skills.registry.stdio_client", return_value=mock_stdio_ctx), \
         patch("app.agents.skills.registry.ClientSession", return_value=mock_session), \
         patch("app.agents.skills.registry._manifests", {}) as mock_manifests:
            
        await SkillRegistry.initialize_mcp_clients()
        
        # Verify connection was attempted
        mock_stdio_ctx.__aenter__.assert_called()
        mock_session.initialize.assert_awaited()
        mock_session.list_tools.assert_awaited()
        
        # Verify manifest registration
        expected_name = "mcp_test-server_test_tool"
        assert expected_name in mock_manifests
        manifest = mock_manifests[expected_name]
        assert manifest.mcp_server == "test-server"
        assert manifest.mcp_tool == "test_tool"

@pytest.mark.asyncio
async def test_mcp_execution():
    """Test executing an MCP skill"""
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value="Success")
    
    with patch("app.agents.skills.registry.MCP_AVAILABLE", True), \
         patch("app.agents.skills.registry._mcp_sessions", {"test-server": mock_session}):
            
        ctx = MagicMock()
        # Determine the handler indirectly or call _execute_mcp_tool directly
        from app.agents.skills.registry import _execute_mcp_tool
        
        result = await _execute_mcp_tool(ctx, "test-server", "test_tool", arg1="val1")
        
        mock_session.call_tool.assert_awaited_with("test_tool", arguments={"arg1": "val1"})
        assert result == "Success"
