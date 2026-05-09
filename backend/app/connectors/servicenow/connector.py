
from typing import Dict, Any, List
from app.connectors.base import BaseConnector, ConnectorMetadata
from .client import ServiceNowClient
from .tools import SERVICENOW_TOOLS

class ServiceNowConnector(BaseConnector):
    metadata = ConnectorMetadata(
        id="servicenow",
        name="ServiceNow",
        description="ITSM Incident Management",
        icon="🛠️",
        category="itsm",
        auth_type="basic_or_oauth",
        version="1.0.0",
        enterprise_only=True
    )
    
    def __init__(self):
        self.client: ServiceNowClient = None
        
    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """
        Credentials expects:
        - instance_url
        - username/password OR token
        """
        url = credentials.get("instance_url")
        if not url:
             return False
             
        self.client = ServiceNowClient(
            instance_url=url,
            username=credentials.get("username"),
            password=credentials.get("password"),
            token=credentials.get("token")
        )
        
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
        return SERVICENOW_TOOLS
        
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self.client:
            raise RuntimeError("Connector not authenticated")
            
        if tool_name == "servicenow_create_incident":
            return await self.client.create_incident(**arguments)
            
        elif tool_name == "servicenow_get_incident":
            return await self.client.get_incident(**arguments)
            
        elif tool_name == "servicenow_update_incident":
            return await self.client.update_incident(**arguments)
            
        elif tool_name == "servicenow_search_incidents":
            return await self.client.search_incidents(**arguments)
            
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
