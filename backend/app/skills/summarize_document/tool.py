"""
Summarize Document Skill
LLM-based document summarization with anchored iterative approach.
"""

from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def summarize_document(
    ctx: SkillContext,
    text: str,
    max_length: int = 500,
    style: str = "concise",
) -> Dict[str, Any]:
    """
    Summarize text using LLM-based anchored iterative summarization.

    Args:
        ctx: Skill execution context
        text: Text to summarize
        max_length: Maximum summary length in words (default: 500)
        style: Summary style - "concise", "detailed", or "bullet"

    Returns:
        Dictionary with success status and summary
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.USE_CONTEXT_SUMMARIZATION:
        # Fallback to simple truncation when feature is disabled
        sentences = text.split('.')
        summary = ". ".join(sentences[:3]) + "."

        if len(summary) > max_length * 5:  # rough char to word ratio
            summary = summary[:max_length * 5] + "..."

        return {
            "success": True,
            "summary": summary,
            "original_length": len(text),
            "method": "extractive_fallback"
        }

    # Use LLM-based summarization
    try:
        from app.services.summarization import get_summarization_service
        service = get_summarization_service()
        summary = await service.summarize(text, max_length, style)

        return {
            "success": True,
            "summary": summary,
            "original_length": len(text),
            "method": "llm_anchored_iterative" if len(text.split()) >= 1000 else "llm_single_pass"
        }
    except Exception as e:
        # Fallback to truncation on error
        sentences = text.split('.')
        summary = ". ".join(sentences[:3]) + "."

        return {
            "success": True,
            "summary": summary,
            "original_length": len(text),
            "method": "extractive_fallback",
            "note": f"LLM summarization failed: {str(e)}"
        }
