"""
Analyze Code Skill
Analyze Python code for quality, complexity, and potential issues.
"""

import ast
from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def analyze_code(
    ctx: SkillContext,
    code: str,
) -> Dict[str, Any]:
    """
    Analyze code quality and structure.

    Args:
        ctx: Skill execution context
        code: Python code to analyze

    Returns:
        Analysis results including complexity metrics
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "valid": False,
            "error": f"Syntax error: {e}",
        }

    # Count elements
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]

    # Calculate complexity (simple cyclomatic-like measure)
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1

    return {
        "valid": True,
        "lines": len(code.split("\n")),
        "functions": len(functions),
        "function_names": [f.name for f in functions],
        "classes": len(classes),
        "class_names": [c.name for c in classes],
        "imports": len(imports),
        "complexity": complexity,
        "complexity_rating": "low" if complexity < 5 else "medium" if complexity < 10 else "high",
    }
