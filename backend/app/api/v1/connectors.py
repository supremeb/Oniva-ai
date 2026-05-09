
from fastapi import APIRouter, HTTPException
from typing import List, Any, Dict
from app.connectors.registry import ConnectorRegistry
from app.connectors.base import ConnectorMetadata
from pydantic import BaseModel

router = APIRouter(tags=["Connectors"])

# Request model
class ConnectRequest(BaseModel):
    credentials: Dict[str, Any]

@router.get("/", response_model=List[ConnectorMetadata])
async def list_connectors():
    """List all available connectors"""
    return ConnectorRegistry.list_available()

@router.get("/{connector_id}", response_model=ConnectorMetadata)
async def get_connector_details(connector_id: str):
    """Get details of a specific connector"""
    connector_cls = ConnectorRegistry.get_connector_class(connector_id)
    if not connector_cls:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector_cls.metadata

@router.post("/{connector_id}/connect")
async def connect_connector(connector_id: str, request: ConnectRequest):
    """
    Configure and connect a connector.
    For MVP: authenticates and checks health, but persistence might be mock or needs integrating `ConnectorCredential`.
    """
    connector = ConnectorRegistry.get_connector(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    
    # Try authentication
    try:
        success = await connector.authenticate(request.credentials)
        if not success:
             raise HTTPException(status_code=400, detail="Authentication failed")
        
        # Save credentials (TODO: Use ConnectorCredential model)
        # For MVP, we just return success
        return {"status": "connected", "message": f"Successfully connected to {connector.metadata.name}"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{connector_id}/status")
async def check_status(connector_id: str):
    connector = ConnectorRegistry.get_connector(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
        
    try:
        status = await connector.health_check()
        return status
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/{connector_id}/tools")
async def list_tools(connector_id: str):
    connector = ConnectorRegistry.get_connector(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
        
    try:
        return connector.get_tools()
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))
