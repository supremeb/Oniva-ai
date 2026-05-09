"""
Execute Python Skill
Safely execute Python code in a restricted sandbox.
"""

import ast
import io
import os
import signal
import sys
from typing import Any, Dict, Optional

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
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                return False, f"Name not allowed: {node.id}"
            if node.id.startswith("__") and node.id.endswith("__"):
                return False, f"Dunder name access not allowed: {node.id}"

        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRIBUTES:
                return False, f"Attribute access not allowed: {node.attr}"
            if node.attr.startswith("_"):
                return False, f"Private attribute access not allowed: {node.attr}"

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in ALLOWED_IMPORTS:
                    return False, f"Import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] not in ALLOWED_IMPORTS:
                return False, f"Import not allowed: {node.module}"

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BLOCKED_NAMES:
                    return False, f"Function not allowed: {node.func.id}"
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in {"system", "popen", "spawn", "fork", "execv", "execve", "spawnl", "spawnle"}:
                    return False, f"Method not allowed: {node.func.attr}"
                if node.func.attr in BLOCKED_ATTRIBUTES:
                    return False, f"Method not allowed: {node.func.attr}"

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                dangerous_patterns = ["__class__", "__globals__", "__builtins__", "__subclasses__"]
                for pattern in dangerous_patterns:
                    if pattern in node.value:
                        return False, f"Suspicious string content: {pattern}"

    return True, None


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted import function"""
    if name in ALLOWED_IMPORTS:
        return __import__(name, globals, locals, fromlist, level)
    base_module = name.split('.')[0]
    if base_module in ALLOWED_IMPORTS:
        return __import__(name, globals, locals, fromlist, level)
    raise ImportError(f"Import not allowed: {name}")


async def execute_python(
    ctx: SkillContext,
    code: str,
    timeout: int = 5,
) -> Dict[str, Any]:
    """
    Execute Python code in a restricted sandbox.

    Args:
        ctx: Skill execution context
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
        pass

    try:
        import builtins
        import collections
        import datetime
        import decimal
        import fractions
        import functools
        import itertools
        import json as json_mod
        import math
        import random
        import re
        import statistics

        restricted_builtins = {}
        for name in SAFE_BUILTINS:
            if hasattr(builtins, name):
                restricted_builtins[name] = getattr(builtins, name)

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

        exec_locals = {}
        exec(code, safe_globals, exec_locals)

        if exec_locals.get("result"):
            result = exec_locals["result"]
        elif exec_locals:
            result = list(exec_locals.values())[-1]

    except ExecutionTimeoutError:
        error_msg = f"Execution timed out after {timeout} seconds"
    except MemoryError:
        error_msg = "Memory limit exceeded"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
    finally:
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
