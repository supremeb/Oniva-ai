

SERVICENOW_TOOLS = [
    {
        "name": "servicenow_create_incident",
        "description": "Create a new incident in ServiceNow",
        "inputSchema": {
            "type": "object",
            "properties": {
                "short_description": {"type": "string", "description": "Brief summary of the issue"},
                "description": {"type": "string", "description": "Detailed description"},
                "urgency": {"type": "integer", "description": "Urgency level (1-3)", "enum": [1, 2, 3]},
                "impact": {"type": "integer", "description": "Impact level (1-3)", "enum": [1, 2, 3]}
            },
            "required": ["short_description"]
        }
    },
    {
        "name": "servicenow_get_incident",
        "description": "Get incident details by ID or Number",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sys_id": {"type": "string", "description": "System ID of the incident"},
                "number": {"type": "string", "description": "Incident number (e.g. INC001234)"}
            },
            # One of them is required logic handled in code usually, but schema can verify "anyOf" if strict
        }
    },
    {
        "name": "servicenow_update_incident",
        "description": "Update an existing incident",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sys_id": {"type": "string", "description": "System ID of the incident"},
                "short_description": {"type": "string"},
                "description": {"type": "string"},
                "state": {"type": "integer", "description": "State code (e.g. 2=In Progress, 7=Closed)"},
                "comments": {"type": "string", "description": "Add a work note or comment"}
            },
            "required": ["sys_id"]
        }
    },
    {
        "name": "servicenow_search_incidents",
        "description": "Search for incidents using a query",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Encoded query string (e.g. 'active=true^priority=1')"},
                "limit": {"type": "integer", "description": "Max results", "default": 10},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0}
            },
            "required": ["query"]
        }
    }
]
