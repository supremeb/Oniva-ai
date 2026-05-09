"""
Sanctions Check Skill
Check entity against OFAC/EU sanctions lists for compliance screening.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agents.skills.context import SkillContext


# Mock sanctions list for development/testing
# In production, this would query actual OFAC/EU APIs
MOCK_SANCTIONED_ENTITIES = [
    {"name": "Evil Corp", "list": "OFAC SDN", "program": "CYBER2"},
    {"name": "Bad Guys LLC", "list": "OFAC SDN", "program": "SDGT"},
    {"name": "Smugglers Inc", "list": "EU Consolidated", "program": "EU-SYRIA"},
    {"name": "Dark Money Holdings", "list": "UN Security Council", "program": "UNSC-1718"},
]

SOURCES_CHECKED = ["OFAC SDN", "EU Consolidated", "UN Security Council"]


async def sanctions_check(
    ctx: SkillContext,
    entity_name: str,
    entity_type: str = "unknown",
) -> Dict[str, Any]:
    """
    Check entity against sanctions lists.

    Args:
        ctx: Skill execution context
        entity_name: Name of entity to check
        entity_type: Type of entity (individual, company, etc.)

    Returns:
        Dict with check results including matches if found
    """
    matches: List[Dict[str, Any]] = []
    is_sanctioned = False

    # Check against mock sanctions list
    entity_lower = entity_name.lower()
    for sanctioned in MOCK_SANCTIONED_ENTITIES:
        if sanctioned["name"].lower() in entity_lower or entity_lower in sanctioned["name"].lower():
            is_sanctioned = True
            matches.append({
                "list": sanctioned["list"],
                "match_score": 100,  # Exact match
                "matched_name": sanctioned["name"].upper(),
                "program": sanctioned["program"],
            })

    # Also check for partial/fuzzy matches (simplified)
    if not is_sanctioned:
        for sanctioned in MOCK_SANCTIONED_ENTITIES:
            # Simple word overlap check
            entity_words = set(entity_lower.split())
            sanctioned_words = set(sanctioned["name"].lower().split())
            overlap = entity_words & sanctioned_words

            if overlap and len(overlap) >= 1:
                # Partial match - lower confidence
                match_score = int(len(overlap) / max(len(entity_words), len(sanctioned_words)) * 100)
                if match_score >= 50:
                    matches.append({
                        "list": sanctioned["list"],
                        "match_score": match_score,
                        "matched_name": sanctioned["name"].upper(),
                        "program": sanctioned["program"],
                    })

    # If we have any high-confidence matches, mark as sanctioned
    if any(m["match_score"] >= 80 for m in matches):
        is_sanctioned = True

    return {
        "entity": entity_name,
        "entity_type": entity_type,
        "is_sanctioned": is_sanctioned,
        "matches": matches,
        "sources_checked": SOURCES_CHECKED,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
