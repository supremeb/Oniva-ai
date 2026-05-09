"""
File Skills
Skills for reading, writing, and parsing documents

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/
"""

from typing import Dict, Any
import os
import tempfile
import json
import csv
import io

from app.agents.skills.context import SkillContext


# Allowed directories for file operations
ALLOWED_READ_PATHS = [
    "/tmp",
    tempfile.gettempdir(),
]


def is_path_allowed(path: str, for_write: bool = False) -> bool:
    """Check if a path is allowed for reading/writing"""
    abs_path = os.path.abspath(path)
    
    if for_write:
        # Only allow writing to temp directory
        return abs_path.startswith(tempfile.gettempdir())
    else:
        # Allow reading from configured paths
        for allowed in ALLOWED_READ_PATHS:
            if abs_path.startswith(os.path.abspath(allowed)):
                return True
        return False


async def read_file(
    ctx: SkillContext,
    path: str,
    encoding: str = "utf-8",
    max_size: int = 100000
) -> Dict[str, Any]:
    """
    Read content from a file
    
    Args:
        path: Path to file
        encoding: File encoding (default: utf-8)
        max_size: Maximum bytes to read
    
    Returns:
        File content and metadata
    """
    if not is_path_allowed(path):
        return {
            "success": False,
            "error": f"Path not allowed: {path}",
        }
    
    if not os.path.exists(path):
        return {
            "success": False,
            "error": f"File not found: {path}",
        }
    
    try:
        file_size = os.path.getsize(path)
        
        with open(path, "r", encoding=encoding) as f:
            content = f.read(max_size)
        
        return {
            "success": True,
            "path": path,
            "content": content,
            "size": file_size,
            "truncated": file_size > max_size,
            "encoding": encoding,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


async def parse_document(
    ctx: SkillContext,
    content: str,
    format: str = "auto"
) -> Dict[str, Any]:
    """
    Parse structured document content
    
    Args:
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
