
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.connectors.servicenow.connector import ServiceNowConnector
from app.connectors.servicenow.client import ServiceNowClient

@pytest.fixture
def mock_httpx_client():
    with patch("httpx.AsyncClient") as mock:
        # Make the instance's request method an AsyncMock
        instance = mock.return_value.__aenter__.return_value
        instance.request = AsyncMock()
        # IMPORTANT: The response object must be a MagicMock (sync), not AsyncMock
        instance.request.return_value = MagicMock()
        yield mock

@pytest.mark.asyncio
async def test_servicenow_authentication_success(mock_httpx_client):
    connector = ServiceNowConnector()
    
    # Mock verify_connection response
    mock_instance = mock_httpx_client.return_value.__aenter__.return_value
    # Config the response mock
    mock_response = mock_instance.request.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": [{"number": "INC001"}]}
    
    success = await connector.authenticate({
        "instance_url": "https://dev12345.service-now.com",
        "username": "admin",
        "password": "password"
    })
    
    assert success is True
    assert connector.client is not None

@pytest.mark.asyncio
async def test_servicenow_tools_definitions():
    connector = ServiceNowConnector()
    tools = connector.get_tools()
    
    assert len(tools) == 4
    tool_names = [t["name"] for t in tools]
    assert "servicenow_create_incident" in tool_names
    assert "servicenow_get_incident" in tool_names

@pytest.mark.asyncio
async def test_create_incident(mock_httpx_client):
    connector = ServiceNowConnector()
    connector.client = ServiceNowClient("https://test.service-now.com", "user", "pass")
    
    mock_instance = mock_httpx_client.return_value.__aenter__.return_value
    mock_instance.request.return_value.status_code = 201
    mock_instance.request.return_value.json.return_value = {"result": {"sys_id": "sys123", "number": "INC999"}}
    
    result = await connector.call_tool("servicenow_create_incident", {
        "short_description": "Test Incident"
    })
    
    assert result["result"]["number"] == "INC999"
    # Verify request payload
    call_args = mock_instance.request.call_args
    # request(method, url, ...) - url is second positional arg
    assert call_args[0][1] == "https://test.service-now.com/api/now/table/incident"
    assert call_args[1]["json"]["short_description"] == "Test Incident"
