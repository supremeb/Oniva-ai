"""
Stable UUIDs for built-in agents.

Zero-prefixed pattern makes them visually distinct from user-generated UUIDs
and avoids collisions. These are the canonical IDs accepted everywhere in
the backend API.
"""


BUILTIN_AGENT_UUIDS: dict[str, str] = {
    "general":          "00000000-0000-0000-0000-000000000001",
    "data-retrieval":   "00000000-0000-0000-0000-000000000002",
    "analysis":         "00000000-0000-0000-0000-000000000003",
    "compliance":       "00000000-0000-0000-0000-000000000004",
    "research":         "00000000-0000-0000-0000-000000000005",
    "kyc-aml":          "00000000-0000-0000-0000-000000000006",
    "market-abuse":     "00000000-0000-0000-0000-000000000007",
    "report-generator": "00000000-0000-0000-0000-000000000008",
}

# Reverse map: UUID string → slug
_UUID_TO_SLUG: dict[str, str] = {v: k for k, v in BUILTIN_AGENT_UUIDS.items()}

_ALL_SLUGS = set(BUILTIN_AGENT_UUIDS.keys())
_ALL_UUIDS = set(BUILTIN_AGENT_UUIDS.values())


def is_builtin_agent_id(agent_id: str) -> bool:
    """Check if an ID (slug or UUID string) belongs to a built-in agent."""
    return agent_id in _ALL_SLUGS or agent_id in _ALL_UUIDS


def slug_to_uuid(slug: str) -> str:
    """Convert a slug to its stable UUID string. Returns input unchanged if not a slug."""
    return BUILTIN_AGENT_UUIDS.get(slug, slug)


def uuid_to_slug(uuid_str: str) -> str:
    """Convert a UUID string to its slug. Returns input unchanged if not a built-in UUID."""
    return _UUID_TO_SLUG.get(uuid_str, uuid_str)
