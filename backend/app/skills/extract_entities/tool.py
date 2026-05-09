"""
Extract Entities Skill
Extract financial entities from text using pattern matching.
"""

import re
from typing import Any, Dict, List

from app.agents.skills.context import SkillContext


async def extract_entities(
    ctx: SkillContext,
    text: str,
    entity_types: List[str] = ["DATE", "MONEY"],
) -> Dict[str, Any]:
    """
    Extract entities from text using regex/heuristics.

    Args:
        ctx: Skill execution context
        text: Text to extract entities from
        entity_types: List of entity types to extract

    Returns:
        Dictionary of extracted entities by type
    """
    entities = {}

    if "DATE" in entity_types:
        # Simple date regex YYYY-MM-DD or MM/DD/YYYY
        date_pattern = r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b'
        dates = re.findall(date_pattern, text)
        entities["dates"] = list(set(dates))

    if "MONEY" in entity_types:
        # Simple money regex $1,000.00
        money_pattern = r'\$\s?[\d,]+(?:\.\d{2})?'
        amounts = re.findall(money_pattern, text)
        entities["money"] = list(set(amounts))

    # "ORG" would require NLP/NER model or LLM
    if "ORG" in entity_types:
        entities["organizations"] = ["Mock Org Inc."]  # Placeholder

    return {
        "success": True,
        "entities": entities
    }
