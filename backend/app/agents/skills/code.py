"""
Code Skills
Skills for code execution, analysis, and visualization generation

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/

SECURITY: Supplied code is NEVER executed inside the API process. All execution
goes through the E2B sandbox executor; with the sandbox disabled or unconfigured
the entry points return a refusal. There is no in-process fallback.
"""

from typing import Dict, Any
import io
import ast
import base64

from app.agents.skills.context import SkillContext
from app.config import get_settings
from app.services.sandbox_executor import get_sandbox_executor

SANDBOX_REFUSAL = (
    "Code execution is unavailable: the sandbox is not enabled. "
    "Set ENABLE_SANDBOX=True and configure E2B_API_KEY. "
    "Code is never run inside the API process."
)

_MAX_TIMEOUT = 60


def sandbox_available() -> bool:
    """True only when the E2B sandbox is enabled and has an API key."""
    settings = get_settings()
    return bool(settings.ENABLE_SANDBOX and settings.E2B_API_KEY)


async def run_in_sandbox(
    code: str,
    *,
    tenant_id: Any,
    session_id: Any,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Run code in the E2B sandbox, or refuse. The single code-execution path."""
    if not sandbox_available():
        return {
            "success": False,
            "output": None,
            "result": None,
            "stdout": "",
            "stderr": "",
            "error": SANDBOX_REFUSAL,
        }

    executor = get_sandbox_executor()
    res = await executor.execute_with_lifecycle(
        code,
        str(tenant_id),
        str(session_id),
        timeout=min(timeout, _MAX_TIMEOUT),
    )
    return {
        "success": bool(res.success),
        "output": res.stdout or None,
        "result": res.stdout or None,
        "stdout": res.stdout or "",
        "stderr": res.stderr or "",
        "error": None if res.success else (res.error or res.stderr or "Execution failed"),
        "output_files": res.output_files,
    }


async def execute_python(
    ctx: SkillContext,
    code: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Execute Python code in the E2B sandbox.

    Refuses when the sandbox is disabled; never executes in-process.
    """
    if not code or not code.strip():
        return {"success": False, "error": "No code provided.", "output": None}
    out = await run_in_sandbox(
        code, tenant_id=ctx.tenant_id, session_id=ctx.session_id, timeout=timeout
    )
    return {k: out[k] for k in ("success", "output", "result", "error")} | {
        "errors": out["stderr"] or None
    }


async def analyze_code(
    ctx: SkillContext,
    code: str
) -> Dict[str, Any]:
    """
    Analyze code quality and structure
    
    Args:
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


async def generate_chart(
    ctx: SkillContext,
    data: Dict[str, Any],
    chart_type: str = "bar",
    title: str = "Chart"
) -> Dict[str, Any]:
    """
    Generate a chart from data
    
    Args:
        data: Data to visualize (dict with 'labels' and 'values')
        chart_type: Type of chart ('bar', 'line', 'pie')
        title: Chart title
    
    Returns:
        Base64-encoded chart image
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt
        
        labels = data.get("labels", [])
        values = data.get("values", [])
        
        if not labels or not values:
            return {"error": "Data must contain 'labels' and 'values' arrays"}
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        if chart_type == "bar":
            ax.bar(labels, values)
        elif chart_type == "line":
            ax.plot(labels, values, marker='o')
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct='%1.1f%%')
        else:
            return {"error": f"Unsupported chart type: {chart_type}"}
        
        ax.set_title(title)
        
        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        
        # Build structured chart config for interactive frontend rendering
        chart_data = [
            {"name": str(label), "value": val}
            for label, val in zip(labels, values)
        ]
        chart_config = {
            "type": chart_type,
            "title": title,
            "data": chart_data,
            "series": [{"dataKey": "value", "name": title}],
            "xAxisKey": "name",
        }

        return {
            "success": True,
            "chart_type": chart_type,
            "image_base64": image_base64,
            "image_url": f"data:image/png;base64,{image_base64}",
            "chart_config": chart_config,
        }
    except ImportError:
        return {
            "success": False,
            "error": "matplotlib not available",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }
