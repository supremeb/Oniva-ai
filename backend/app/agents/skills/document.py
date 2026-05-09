"""
Document Skills
Skills for parsing PDFs, extracting entities, and document summarization

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/
"""

from typing import Dict, Any, List
import re
import os

from app.agents.skills.context import SkillContext
from app.agents.skills.file import is_path_allowed

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


async def parse_pdf(
    ctx: SkillContext,
    path: str,
    max_pages: int = 10
) -> Dict[str, Any]:
    """
    Parse a PDF file to extract text.
    
    Args:
        path: Path to the PDF file
        max_pages: Maximum pages to extract text from
    """
    if not HAS_PYPDF:
        return {
            "success": False,
            "error": "pypdf library not installed. Install with 'pip install pypdf'"
        }
        
    if not is_path_allowed(path):
        return {
            "success": False,
            "error": f"Path not allowed: {path}"
        }
        
    if not os.path.exists(path):
        return {
            "success": False,
            "error": f"File not found: {path}"
        }
        
    try:
        reader = PdfReader(path)
        num_pages = len(reader.pages)
        meta = reader.metadata
        
        extracted_text = []
        pages_to_read = min(num_pages, max_pages)
        
        for i in range(pages_to_read):
            page = reader.pages[i]
            text = page.extract_text()
            extracted_text.append({
                "page": i + 1,
                "content": text
            })
            
        full_text = "\n\n".join([p["content"] for p in extracted_text])
            
        return {
            "success": True,
            "metadata": {
                "title": meta.title if meta else None,
                "author": meta.author if meta else None,
                "pages": num_pages
            },
            "pages_extracted": pages_to_read,
            "text": full_text[:50000],  # Limit size for safety
            "truncated": len(full_text) > 50000
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to parse PDF: {str(e)}"
        }

async def extract_entities(
    ctx: SkillContext,
    text: str,
    entity_types: List[str] = ["DATE", "MONEY"]
) -> Dict[str, Any]:
    """
    Extract entities from text using regex/heuristics (Mock NER).
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
        
    # "ORG" would require NLP/NER model or LLM.
    if "ORG" in entity_types:
         entities["organizations"] = ["Mock Org Inc."] # Placeholder
         
    return {
        "success": True,
        "entities": entities
    }

async def summarize_document(
    ctx: SkillContext,
    text: str,
    max_length: int = 500,
    style: str = "concise"
) -> Dict[str, Any]:
    """
    Summarize text using LLM-based anchored iterative summarization.

    Args:
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
