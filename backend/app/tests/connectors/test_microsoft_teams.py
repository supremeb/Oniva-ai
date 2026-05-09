
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.connectors.microsoft_teams.connector import TeamsConnector
from app.connectors.microsoft_teams.client import TeamsClient

@pytest.fixture
def mock_httpx_client():
    with patch("httpx.AsyncClient") as mock:
        # Make request an AsyncMock
        instance = mock.return_value.__aenter__.return_value
        instance.request = AsyncMock()
        # Make response sync MagicMock
        instance.request.return_value = MagicMock()
        yield mock

@pytest.mark.asyncio
async def test_teams_authentication_success(mock_httpx_client):
    connector = TeamsConnector()
    
    # Mock verify_connection response (/me)
    mock_instance = mock_httpx_client.return_value.__aenter__.return_value
    mock_response = mock_instance.request.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = {"displayName": "Test User"}
    
    success = await connector.authenticate({"access_token": "fake_token"})
    
    assert success is True
    assert connector.client is not None

@pytest.mark.asyncio
async def test_teams_send_message(mock_httpx_client):
    connector = TeamsConnector()
    connector.client = TeamsClient(token="fake_token")
    
    mock_instance = mock_httpx_client.return_value.__aenter__.return_value
    mock_response = mock_instance.request.return_value
    mock_response.status_code = 201
    mock_response.json.return_value = {"id": "msg123"}
    
    result = await connector.call_tool("teams_send_message", {
        "team_id": "team1",
        "channel_id": "chan1",
        "message": "Hello World"
    })
    
    assert result["id"] == "msg123"
    
    call_args = mock_instance.request.call_args
    # Verify method and URL
    assert call_args[0][0] == "POST" # method
    assert "channels/chan1/messages" in call_args[0][1] # url
    # Verify payload
    payload = call_args[1]["json"]
    assert payload["body"]["content"] == "Hello World"

@pytest.mark.asyncio
async def test_teams_list_channels(mock_httpx_client):
    connector = TeamsConnector()
    connector.client = TeamsClient(token="fake_token")
    
    mock_instance = mock_httpx_client.return_value.__aenter__.return_value
    mock_response = mock_instance.request.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": [{"id": "chan1", "displayName": "General"}]}
    
    result = await connector.call_tool("teams_list_channels", {"team_id": "team1"})
    
    assert len(result["value"]) == 1
    assert result["value"][0]["displayName"] == "General"
