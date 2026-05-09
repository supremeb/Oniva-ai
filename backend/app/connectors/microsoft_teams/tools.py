

TEAMS_TOOLS = [
    {
        "name": "teams_send_message",
        "description": "Send a message to a Teams channel",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The ID of the Team"},
                "channel_id": {"type": "string", "description": "The ID of the Channel"},
                "message": {"type": "string", "description": "The message content (text or HTML)"}
            },
            "required": ["team_id", "channel_id", "message"]
        }
    },
    {
        "name": "teams_list_channels",
        "description": "List channels in a Team",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The ID of the Team"}
            },
            "required": ["team_id"]
        }
    },
    {
        "name": "teams_create_channel",
        "description": "Create a new channel in a Team",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The ID of the Team"},
                "display_name": {"type": "string", "description": "Name of the new channel"},
                "description": {"type": "string", "description": "Optional description"}
            },
            "required": ["team_id", "display_name"]
        }
    },
    {
        "name": "teams_get_messages",
        "description": "Get messages from a channel",
        "inputSchema": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "The ID of the Team"},
                "channel_id": {"type": "string", "description": "The ID of the Channel"},
                "limit": {"type": "integer", "description": "Max messages to retrieve", "default": 20}
            },
            "required": ["team_id", "channel_id"]
        }
    }
]
