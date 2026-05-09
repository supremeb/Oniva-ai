"""
Parse Document Skill
Parse content from documents (CSV, JSON, etc.).
"""

import csv
import io
import json
from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def parse_document(
    ctx: SkillContext,
    content: str,
    format: str = "auto",
) -> Dict[str, Any]:
    """
    Parse structured document content.

    Args:
        ctx: Skill execution context
        content: Document content (string)
        format: Format type ('csv', 'json', 'auto')

    Returns:
        Parsed data structure
    """
    # Auto-detect format
    if format == "auto":
        content_stripped = content.strip()
        if content_stripped.startswith('{') or content_stripped.startswith('['):
            format = "json"
        elif ',' in content_stripped.split('\n')[0]:
            format = "csv"
        else:
            format = "text"

    try:
        if format == "json":
            data = json.loads(content)
            return {
                "success": True,
                "format": "json",
                "data": data,
                "type": type(data).__name__,
            }

        elif format == "csv":
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
            return {
                "success": True,
                "format": "csv",
                "columns": reader.fieldnames or [],
                "rows": rows[:100],  # Limit to 100 rows
                "total_rows": len(rows),
                "truncated": len(rows) > 100,
            }

        else:
            # Plain text - split into lines
            lines = content.split('\n')
            return {
                "success": True,
                "format": "text",
                "lines": lines[:100],
                "total_lines": len(lines),
                "truncated": len(lines) > 100,
            }

    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": f"JSON parse error: {e}",
            "format": format,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "format": format,
        }
