"""API Key scope definitions and validation.

Enterprise feature: Granular permission control for API keys.

Scopes follow the pattern: resource:action
- resource: workflows, agents, connectors, settings, audit, admin
- action: read, write, execute, delete, all
"""
from typing import List

# Valid scope definitions
VALID_SCOPES = {
    # Workflow scopes
    "workflows:read",      # List, get workflows
    "workflows:write",     # Create, update workflows
    "workflows:execute",   # Execute workflows
    "workflows:delete",    # Delete workflows
    "workflows:all",       # All workflow operations

    # Agent scopes
    "agents:read",         # List, get agents
    "agents:write",        # Create, update custom agents
    "agents:all",

    # Connector scopes
    "connectors:read",     # List connectors
    "connectors:write",    # Configure connectors
    "connectors:all",

    # Settings scopes
    "settings:read",       # View settings
    "settings:write",      # Modify settings (API keys, etc.)
    "settings:all",

    # Audit scopes
    "audit:read",          # View audit logs
    "audit:all",

    # Background job scopes
    "jobs:read",           # View background jobs
    "jobs:write",          # Create, cancel jobs
    "jobs:all",

    # Batch execution scopes
    "batches:write",       # Create batch executions
    "batches:read",        # View batch status
    "batches:all",

    # Admin scopes (highest privilege)
    "admin:all",           # Full access to all resources

    # Wildcard (backward compatibility)
    "*:*"                  # Full access (legacy keys)
}


# Scope hierarchy - higher scopes include lower scopes
SCOPE_HIERARCHY = {
    "*:*": [  # Wildcard includes everything
        "admin:all", "workflows:all", "agents:all", "connectors:all",
        "settings:all", "audit:all", "jobs:all", "batches:all"
    ],
    "admin:all": [  # Admin includes all resource scopes
        "workflows:all", "agents:all", "connectors:all",
        "settings:all", "audit:all", "jobs:all", "batches:all"
    ],
    "workflows:all": ["workflows:read", "workflows:write", "workflows:execute", "workflows:delete"],
    "agents:all": ["agents:read", "agents:write"],
    "connectors:all": ["connectors:read", "connectors:write"],
    "settings:all": ["settings:read", "settings:write"],
    "audit:all": ["audit:read"],
    "jobs:all": ["jobs:read", "jobs:write"],
    "batches:all": ["batches:read", "batches:write"],
}


def check_scope(required_scope: str, held_scopes: List[str]) -> bool:
    """Check if held scopes satisfy required scope.

    Args:
        required_scope: Scope required for the operation (e.g., "workflows:execute")
        held_scopes: List of scopes granted to the API key

    Returns:
        True if any held scope includes the required scope

    Examples:
        >>> check_scope("workflows:read", ["workflows:read"])
        True
        >>> check_scope("workflows:execute", ["workflows:all"])
        True
        >>> check_scope("workflows:write", ["workflows:read"])
        False
        >>> check_scope("workflows:read", ["admin:all"])
        True
        >>> check_scope("workflows:read", ["*:*"])
        True
    """
    if not held_scopes:
        return False

    # Direct match
    if required_scope in held_scopes:
        return True

    # Check hierarchy - do any held scopes include the required scope?
    for held_scope in held_scopes:
        if held_scope in SCOPE_HIERARCHY:
            included_scopes = SCOPE_HIERARCHY[held_scope]
            # Recursively check included scopes
            if required_scope in included_scopes or check_scope(required_scope, included_scopes):
                return True

    return False


def validate_scopes(scopes: List[str]) -> List[str]:
    """Validate that all scopes are valid.

    Args:
        scopes: List of scopes to validate

    Returns:
        List of valid scopes (filters out invalid ones)

    Raises:
        ValueError: If no valid scopes provided
    """
    if not scopes:
        raise ValueError("At least one scope must be provided")

    valid = [s for s in scopes if s in VALID_SCOPES]

    if not valid:
        raise ValueError(
            f"No valid scopes provided. Must be one of: {sorted(VALID_SCOPES)}"
        )

    return valid


def get_scope_description(scope: str) -> str:
    """Get human-readable description of a scope."""
    descriptions = {
        "workflows:read": "View workflow definitions and execution history",
        "workflows:write": "Create and modify workflows",
        "workflows:execute": "Execute workflows and view results",
        "workflows:delete": "Delete workflows",
        "workflows:all": "Full access to workflows",
        "agents:read": "View agents and agent configurations",
        "agents:write": "Create and modify custom agents",
        "agents:all": "Full access to agents",
        "connectors:read": "View configured connectors",
        "connectors:write": "Configure connectors and API keys",
        "connectors:all": "Full access to connectors",
        "settings:read": "View system settings",
        "settings:write": "Modify system settings",
        "settings:all": "Full access to settings",
        "audit:read": "View audit logs and compliance records",
        "audit:all": "Full access to audit system",
        "jobs:read": "View background jobs status",
        "jobs:write": "Create and cancel background jobs",
        "jobs:all": "Full access to background jobs",
        "batches:write": "Create batch workflow executions",
        "batches:read": "View batch execution status",
        "batches:all": "Full access to batch execution",
        "admin:all": "Full administrative access to all resources",
        "*:*": "Complete access to all resources and operations"
    }
    return descriptions.get(scope, "Unknown scope")


def expand_scope_wildcards(scopes: List[str]) -> List[str]:
    """Expand wildcard scopes to their included scopes.

    Args:
        scopes: List of scopes (may include wildcards)

    Returns:
        Expanded list of all included scopes
    """
    expanded = set(scopes)

    for scope in scopes:
        if scope in SCOPE_HIERARCHY:
            # Add all included scopes recursively
            for included in SCOPE_HIERARCHY[scope]:
                if included not in expanded:
                    expanded.add(included)
                    # Recursively expand
                    nested = expand_scope_wildcards([included])
                    expanded.update(nested)

    return list(expanded)
