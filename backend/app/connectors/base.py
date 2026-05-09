
from abc import ABC, abstractmethod
from typing import Dict, List, Any
from pydantic import BaseModel

class ConnectorMetadata(BaseModel):
    """Connector metadata for registry and UI"""
    id: str                           # Unique ID (e.g., "servicenow")
    name: str                         # Display name
    description: str                  # Short description
    icon: str                         # Icon URL or emoji
    category: str                     # Category: "itsm", "communication", "crm"
    auth_type: str                    # "oauth2", "api_key", "basic"
    version: str                      # Connector version
    enterprise_only: bool = False     # Require enterprise license
    
class BaseConnector(ABC):
    """Abstract base class for all connectors"""
    
    metadata: ConnectorMetadata
    
    @abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """Authenticate with the external service. Should validate credentials."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> bool:
        """Disconnect and cleanup resources"""
        pass
    
    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Check connection status. Returns dict with 'status' key."""
        pass
    
    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return list of available MCP tools definitions"""
        pass
    
    @abstractmethod
    async def call_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Any:
        """Execute a tool and return result"""
        pass
