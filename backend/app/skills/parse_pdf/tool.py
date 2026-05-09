"""
Parse PDF Skill
Extract text and metadata from PDF documents.
"""

import os
from typing import Any, Dict

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
    max_pages: int = 10,
) -> Dict[str, Any]:
    """
    Parse a PDF file to extract text.

    Args:
        ctx: Skill execution context
        path: Path to the PDF file
        max_pages: Maximum pages to extract text from

    Returns:
        Extracted text, metadata, and page information
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
