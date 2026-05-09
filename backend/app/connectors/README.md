# FI Agents Connectors

This directory contains connector implementations for external data sources and services. Connectors enable AI agents to interact with various platforms through the Model Context Protocol (MCP) and custom integrations.

## Available Connectors

### ServiceNow
**Status**: 🚧 In Development
**Type**: Custom Integration

ServiceNow connector for IT service management.

**Directory**: `servicenow/`

### Microsoft Teams
**Status**: 🚧 In Development
**Type**: Custom Integration

Microsoft Teams connector for communication and collaboration.

**Directory**: `microsoft_teams/`

## Connector Architecture

### Base Structure

Each connector should follow this structure:

```
connector_name/
├── __init__.py           # Exports connector class
├── connector.py          # Main connector implementation
├── schemas.py            # Pydantic schemas (optional)
├── utils.py              # Helper functions (optional)
└── README.md             # Connector-specific documentation
```

### Connector Interface

All connectors should implement these core methods:

```python
class BaseConnector:
    async def connect(self) -> bool:
        """Establish connection to the service"""
        pass

    async def disconnect(self) -> bool:
        """Close connection to the service"""
        pass

    async def test_connection(self) -> Dict[str, Any]:
        """Test connectivity and return status"""
        pass

    async def get_available_tools(self) -> List[Dict[str, Any]]:
        """List available tools/operations"""
        pass
```

## MCP Connectors

MCP (Model Context Protocol) connectors use the standard MCP client from `app/services/mcp_client.py`.

### Configuration Format

```python
{
    "name": "Connector Name",
    "mcp_server": "command to start MCP server",
    "args": {},  # Server-specific arguments
    "credentials": {}  # Authentication credentials
}
```

### Example: Linear

```python
from app.connectors.linear import LinearConnector

config = {
    "name": "Linear",
    "mcp_server": "npx -y mcp-remote https://mcp.linear.app/mcp",
    "config": {},
    "credentials": {}  # OAuth handled by MCP
}

connector = LinearConnector(config)
await connector.connect()

# Search issues
result = await connector.search_issues("priority:high status:todo")

# Create issue
issue = await connector.create_issue(
    title="New feature request",
    priority="high"
)
```

## Custom Connectors

Custom connectors implement direct API integrations when MCP servers are not available.

### Configuration Format

```python
{
    "name": "Service Name",
    "base_url": "https://api.service.com",
    "auth_type": "bearer|oauth|api_key",
    "credentials": {
        "api_key": "...",
        # or other auth credentials
    }
}
```

## Adding a New Connector

1. **Create directory structure**:
   ```bash
   mkdir backend/app/connectors/new_connector
   cd backend/app/connectors/new_connector
   touch __init__.py connector.py README.md
   ```

2. **Implement connector class**:
   ```python
   # connector.py
   from typing import Dict, Any

   class NewConnector:
       def __init__(self, config: Dict[str, Any]):
           self.config = config

       async def connect(self) -> bool:
           # Implementation
           pass
   ```

3. **Export in __init__.py**:
   ```python
   from .connector import NewConnector
   __all__ = ["NewConnector"]
   ```

4. **Document in README.md**:
   - Purpose and features
   - Authentication requirements
   - Configuration examples
   - Available methods
   - Usage examples

5. **Create example usage** in `backend/examples/`:
   ```python
   # new_connector_example.py
   async def main():
       connector = NewConnector(config)
       await connector.connect()
       # Example operations
   ```

6. **Add to connector registry** (if using dynamic loading):
   Update `backend/app/connectors/__init__.py` with new connector.

## Database Integration

Connectors are stored in the database using the `Connector` model:

```python
from app.models.connector import Connector

# Create connector record
connector = Connector(
    user_id=user.id,
    connector_type="mcp",  # or "rest_api", "graphql"
    provider="linear",
    name="Linear Project Management",
    config={
        "mcp_server": "npx -y mcp-remote https://mcp.linear.app/mcp",
        "args": {}
    },
    is_active=True
)
```

## API Integration

Use the Connectors API to manage connectors:

```bash
# Create connector
POST /api/v1/connectors
{
  "name": "Linear",
  "connector_type": "mcp",
  "provider": "linear",
  "config": {...},
  "credentials": {...}
}

# Test connection
POST /api/v1/connectors/{id}/test

# Call tool
POST /api/v1/connectors/{id}/call-tool
{
  "tool_name": "search_issues",
  "arguments": {"query": "priority:high"}
}
```

## Security Considerations

1. **Credentials Storage**:
   - Always encrypt credentials using `EncryptionService`
   - Never store plain text API keys or tokens
   - Use environment variables for sensitive defaults

2. **OAuth Flows**:
   - Use MCP's built-in OAuth when available
   - Implement proper token refresh logic
   - Validate redirect URIs

3. **API Rate Limiting**:
   - Implement rate limiting in connectors
   - Use exponential backoff for retries
   - Cache responses when appropriate

4. **Error Handling**:
   - Catch and log all exceptions
   - Return structured error responses
   - Don't expose sensitive information in errors

## Testing

Create tests for each connector:

```python
# tests/test_connectors/test_linear.py
import pytest
from app.connectors.linear import LinearConnector

@pytest.mark.asyncio
async def test_linear_connection():
    connector = LinearConnector(config)
    result = await connector.test_connection()
    assert result["status"] == "success"
```

## Best Practices

1. **Async/Await**: Use async methods for all I/O operations
2. **Type Hints**: Add type hints to all methods
3. **Error Handling**: Implement comprehensive error handling
4. **Logging**: Use structured logging for debugging
5. **Documentation**: Document all public methods
6. **Testing**: Write unit and integration tests
7. **Configuration**: Validate configuration on initialization
8. **Connection Pooling**: Reuse connections when possible

## Troubleshooting

### Connection Issues
1. Verify credentials are correct
2. Check network connectivity
3. Validate API endpoint URLs
4. Review firewall/proxy settings

### Authentication Issues
1. Check token expiration
2. Verify OAuth flow completion
3. Validate API key permissions
4. Review authentication headers

### MCP Issues
1. Ensure `npx` is installed and accessible
2. Verify MCP server package is available
3. Check MCP server logs
4. Test MCP server independently

## Resources

- [Model Context Protocol](https://modelcontextprotocol.io)
- [Linear MCP Documentation](https://linear.app/docs/mcp)
- [FI Agents API Documentation](../api/README.md)

## Support

For issues with:
- **Linear connector**: See [LINEAR_MCP_SETUP.md](../../../LINEAR_MCP_SETUP.md)
- **MCP client**: See `app/services/mcp_client.py`
- **Connector API**: See `app/api/v1/connectors.py`
