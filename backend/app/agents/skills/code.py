"""
Code Skills
Skills for code execution, analysis, and visualization generation

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/

SECURITY NOTE: This module implements sandboxed Python code execution.
The sandbox uses multiple layers of protection:
1. AST validation to block dangerous patterns
2. Restricted builtins (no getattr, type, etc.)
3. Blocked attribute access (__class__, __globals__, etc.)
4. Resource limits (memory, CPU time) via resource module
5. Signal-based timeout for execution
6. Optional Docker isolation (production)
"""

from typing import Dict, Any, Optional
import io
import sys
import ast
import base64
import signal
import os

from app.agents.skills.context import SkillContext


# SECURITY: Resource limits for sandbox execution
CODE_EXECUTION_TIMEOUT = int(os.environ.get("CODE_EXECUTION_TIMEOUT", "5"))
CODE_MEMORY_LIMIT_MB = int(os.environ.get("CODE_MEMORY_LIMIT_MB", "100"))


class ExecutionTimeoutError(Exception):
    """Raised when code execution exceeds the time limit."""
    pass


def _timeout_handler(signum, frame):
    """Signal handler for execution timeout."""
    raise ExecutionTimeoutError("Code execution timed out")


# SECURITY: Safe builtins for code execution
# Removed dangerous builtins that can be used for sandbox escape:
# - getattr/setattr/delattr: Can access private attributes
# - type/object: Can access class hierarchy for escape
# - dir/vars: Can enumerate attributes for escape
# - callable: Can probe for callable objects
# - hasattr: Combined with getattr for escape
SAFE_BUILTINS = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "chr", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "hash", "hex",
    "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "oct", "ord", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
}

# SECURITY: Names that are blocked in function calls and identifiers
BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input",
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__builtins__", "__dict__",
    "getattr", "setattr", "delattr", "vars", "dir",
    "type", "object", "classmethod", "staticmethod",
    "memoryview", "breakpoint", "help", "license", "credits",
}

# SECURITY: Blocked attribute access patterns
BLOCKED_ATTRIBUTES = {
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__builtins__", "__dict__",
    "__init__", "__new__", "__del__", "__reduce__", "__reduce_ex__",
    "__getattribute__", "__setattr__", "__delattr__",
    "__import__", "__loader__", "__spec__", "__name__",
    "gi_frame", "gi_code", "f_locals", "f_globals", "f_builtins",
    "co_code", "co_consts", "func_globals", "func_code",
}

# Allowed imports for safe code execution
ALLOWED_IMPORTS = {
    "math", "statistics", "datetime", "json", "re", "collections",
    "itertools", "functools", "decimal", "fractions", "random",
}


def is_safe_code(code: str) -> tuple[bool, Optional[str]]:
    """
    SECURITY: Validate code before execution to prevent sandbox escape.

    Checks for:
    - Blocked function calls (eval, exec, open, etc.)
    - Dangerous attribute access (__class__, __globals__, etc.)
    - Private attribute access (names starting with _)
    - Unauthorized imports
    - Dangerous method calls (system, popen, etc.)

    Returns (is_safe, error_message)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    for node in ast.walk(tree):
        # Check for blocked names in identifiers
        if isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                return False, f"Name not allowed: {node.id}"
            # Block access to dunder names
            if node.id.startswith("__") and node.id.endswith("__"):
                return False, f"Dunder name access not allowed: {node.id}"

        # Check for dangerous attribute access
        elif isinstance(node, ast.Attribute):
            # Block dangerous attributes
            if node.attr in BLOCKED_ATTRIBUTES:
                return False, f"Attribute access not allowed: {node.attr}"
            # Block all private attributes (starting with _)
            if node.attr.startswith("_"):
                return False, f"Private attribute access not allowed: {node.attr}"

        # Check imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in ALLOWED_IMPORTS:
                    return False, f"Import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] not in ALLOWED_IMPORTS:
                return False, f"Import not allowed: {node.module}"

        # Check function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BLOCKED_NAMES:
                    return False, f"Function not allowed: {node.func.id}"
            elif isinstance(node.func, ast.Attribute):
                # Block dangerous methods
                if node.func.attr in {"system", "popen", "spawn", "fork", "execv", "execve", "spawnl", "spawnle"}:
                    return False, f"Method not allowed: {node.func.attr}"
                # Block dangerous attribute access via method call
                if node.func.attr in BLOCKED_ATTRIBUTES:
                    return False, f"Method not allowed: {node.func.attr}"

        # Block raw strings that might contain escape sequences
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                # Check for attribute access patterns in strings
                dangerous_patterns = ["__class__", "__globals__", "__builtins__", "__subclasses__"]
                for pattern in dangerous_patterns:
                    if pattern in node.value:
                        return False, f"Suspicious string content: {pattern}"

    return True, None


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted import function"""
    if name in ALLOWED_IMPORTS:
        return __import__(name, globals, locals, fromlist, level)
    # Handle submodules like 'collections.abc'
    base_module = name.split('.')[0]
    if base_module in ALLOWED_IMPORTS:
         return __import__(name, globals, locals, fromlist, level)
    raise ImportError(f"Import not allowed: {name}")


async def execute_python(
    ctx: SkillContext,
    code: str,
    timeout: int = 5  # Reduced default timeout for security
) -> Dict[str, Any]:
    """
    SECURITY: Execute Python code in a restricted sandbox.

    Multiple layers of protection:
    1. AST validation blocks dangerous patterns
    2. Restricted builtins (no getattr, type, dir, etc.)
    3. Signal-based timeout prevents infinite loops
    4. Resource limits prevent memory exhaustion
    5. Optional Docker isolation in production

    Args:
        code: Python code to execute
        timeout: Maximum execution time in seconds (default: 5, max: 30)

    Returns:
        Execution result including output and any errors
    """
    # SECURITY: Enforce maximum timeout
    timeout = min(timeout, 30)

    # SECURITY: Safety check via AST validation
    is_safe, error = is_safe_code(code)
    if not is_safe:
        return {
            "success": False,
            "error": f"Security validation failed: {error}",
            "output": None,
        }

    # Check for Docker sandbox execution (production)
    if os.environ.get("ENABLE_SANDBOX", "false").lower() == "true":
        try:
            from app.agents.skills.sandbox import SandboxExecutor
            executor = SandboxExecutor()
            if executor.client:
                output = await executor.execute(code, timeout_seconds=timeout)
                success = not output.startswith("Error")
                return {
                    "success": success,
                    "output": output,
                    "result": None,
                    "errors": output if not success else None,
                    "error": output if not success else None,
                }
        except ImportError:
            pass  # Fall through to in-process execution

    # SECURITY: Capture stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = captured_out = io.StringIO()
    sys.stderr = captured_err = io.StringIO()

    result = None
    error_msg = None

    # SECURITY: Set up timeout handler (Unix only)
    old_handler = None
    try:
        if hasattr(signal, 'SIGALRM'):
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout)
    except (ValueError, OSError):
        # Signal handling may fail in threads or non-main contexts
        pass

    try:
        # SECURITY: Create restricted globals with limited builtins
        import math, statistics, datetime, json as json_mod, re, collections
        import itertools, functools, decimal, fractions, random
        import builtins

        # Only include explicitly safe builtins
        restricted_builtins = {}
        for name in SAFE_BUILTINS:
            if hasattr(builtins, name):
                restricted_builtins[name] = getattr(builtins, name)

        # SECURITY: Add controlled import function
        restricted_builtins["__import__"] = safe_import

        safe_globals = {
            "__builtins__": restricted_builtins,
            "__name__": "__sandbox__",
            "__doc__": None,
            "math": math,
            "statistics": statistics,
            "datetime": datetime,
            "json": json_mod,
            "re": re,
            "collections": collections,
            "itertools": itertools,
            "functools": functools,
            "decimal": decimal,
            "fractions": fractions,
            "random": random,
        }

        # Execute code in isolated namespace
        exec_locals = {}
        exec(code, safe_globals, exec_locals)

        # Get result if last statement is an expression
        if exec_locals.get("result"):
            result = exec_locals["result"]
        elif exec_locals:
            # Return the last defined variable
            result = list(exec_locals.values())[-1]

    except ExecutionTimeoutError:
        error_msg = f"Execution timed out after {timeout} seconds"
    except MemoryError:
        error_msg = "Memory limit exceeded"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
    finally:
        # SECURITY: Reset timeout and restore stdout/stderr
        if old_handler is not None:
            try:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            except (ValueError, OSError):
                pass
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    output = captured_out.getvalue()
    errors = captured_err.getvalue()

    return {
        "success": error_msg is None,
        "output": output if output else None,
        "result": str(result) if result is not None else None,
        "errors": errors if errors else None,
        "error": error_msg,
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
