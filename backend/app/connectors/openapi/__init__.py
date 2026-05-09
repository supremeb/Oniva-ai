"""
OpenAPI Connector Factory

Dynamically generates connectors from OpenAPI/Swagger specifications,
enabling 100+ integrations without custom code.
"""

from .connector import OpenAPIConnector
from .parser import OpenAPIParser
from .tool_generator import ToolGenerator
from .registry import CONNECTOR_REGISTRY, get_registry_connector
from .http_client import SafeHTTPClient
from .auth import AuthHandler, BearerAuth, APIKeyAuth, BasicAuth

__all__ = [
    "OpenAPIConnector",
    "OpenAPIParser",
    "ToolGenerator",
    "CONNECTOR_REGISTRY",
    "get_registry_connector",
    "SafeHTTPClient",
    "AuthHandler",
    "BearerAuth",
    "APIKeyAuth",
    "BasicAuth",
]
