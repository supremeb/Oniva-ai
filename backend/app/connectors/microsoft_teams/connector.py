
from typing import Dict, Any, List
from app.connectors.base import BaseConnector, ConnectorMetadata
from .client import TeamsClient
from .tools import TEAMS_TOOLS

class TeamsConnector(BaseConnector):
    metadata = ConnectorMetadata(
        id="microsoft_teams",
        name="Microsoft Teams",
        description="Team collaboration and chat",
        icon="💬",
        category="communication",
        auth_type="oauth2",
        version="1.0.0",
        enterprise_only=False
    )
    
    def __init__(self):
        self.client: TeamsClient = None
        
    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """
        Credentials expects:
        - access_token (OAuth token)
        """
        token = credentials.get("access_token")
        if not token:
             return False
             
        self.client = TeamsClient(token=token)
        return await self.client.verify_connection()
        
    async def disconnect(self) -> bool:
        self.client = None
        return True
        
    async def health_check(self) -> Dict[str, Any]:
        if not self.client:
            return {"status": "disconnected"}
        
        alive = await self.client.verify_connection()
        return {"status": "connected" if alive else "error"}
        
    def get_tools(self) -> List[Dict[str, Any]]:
        return TEAMS_TOOLS
        
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self.client:
            raise RuntimeError("Connector not authenticated")
            
        if tool_name == "teams_send_message":
            return await self.client.send_message(**arguments)
            
        elif tool_name == "teams_list_channels":
            return await self.client.list_channels(**arguments)
            
        elif tool_name == "teams_create_channel":
            return await self.client.create_channel(**arguments)
            
        elif tool_name == "teams_get_messages":
            return await self.client.get_messages(**arguments)
            
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
