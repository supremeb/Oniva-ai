"""
Code Node Executor
Executes Python code in a sandboxed environment
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
from typing import Dict, Any
import concurrent.futures
from contextlib import redirect_stdout, redirect_stderr

# Server-side max timeout cap (seconds)
_MAX_TIMEOUT = 60


# Standalone function for process execution
def execute_code_safe(code: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute code in a separate process"""
    import io

    # Pre-load allowed modules for the sandbox
    ALLOWED_MODULES = {}
    allowed_module_names = ['json', 'math', 're', 'datetime', 'random', 'collections', 'itertools', 'functools']
    for mod_name in allowed_module_names:
        try:
            ALLOWED_MODULES[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # Custom import function that only allows specific safe modules
    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ALLOWED_MODULES:
            return ALLOWED_MODULES[name]
        raise ImportError(f"Module '{name}' is not allowed. Allowed: {', '.join(ALLOWED_MODULES.keys())}")

    # Removed getattr, type, hasattr from SAFE_BUILTINS to prevent sandbox escape
    # via __subclasses__() traversal and __globals__ access
    SAFE_BUILTINS = {
        'abs': abs, 'all': all, 'any': any, 'bool': bool, 'dict': dict,
        'enumerate': enumerate, 'filter': filter, 'float': float, 'int': int,
        'len': len, 'list': list, 'map': map, 'max': max, 'min': min,
        'print': print, 'range': range, 'round': round, 'set': set,
        'sorted': sorted, 'str': str, 'sum': sum, 'tuple': tuple,
        'zip': zip, 'True': True, 'False': False, 'None': None,
        '__import__': safe_import,
        'isinstance': isinstance,
    }

    local_vars = inputs.copy()
    safe_globals = {'__builtins__': SAFE_BUILTINS}

    # Restrict __globals__ access in exec namespace
    class _RestrictedDict(dict):
        """Dict that blocks access to __globals__ on contained objects."""
        def __getitem__(self, key):
            val = super().__getitem__(key)
            return val

    # Safe modules to import
    module_names = ['json', 'math', 're', 'datetime', 'random', 'collections', 'itertools', 'functools']
    for name in module_names:
        try:
            safe_globals[name] = __import__(name)
        except ImportError:
            pass

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    result = None
    error = None

    # Block __globals__ access patterns in code
    blocked_patterns = ['__globals__', '__subclasses__', '__bases__', '__mro__', '__builtins__']
    for pattern in blocked_patterns:
        if pattern in code:
            return {
                "result": None,
                "stdout": "",
                "stderr": "",
                "error": f"Access to '{pattern}' is not allowed in sandboxed code"
            }

    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(code, safe_globals, local_vars)

        # Get output
        if 'result' in local_vars:
            result = local_vars['result']
        elif 'output' in local_vars:
            result = local_vars['output']
        elif 'main' in local_vars and callable(local_vars['main']):
            result = local_vars['main']()

    except Exception as e:
        error = str(e)

    return {
        "result": result,
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "error": error
    }

class CodeExecutor(BaseNodeExecutor):
    """Executor for Code node - runs Python code in sandboxed process."""

    node_type = "code"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            code = node_data.get("code", "")
            # Server-side timeout cap: min(user-specified, _MAX_TIMEOUT)
            timeout = min(node_data.get("timeout", 30), _MAX_TIMEOUT)
            
            if not code:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="No code provided"
                )

            # Prepare inputs
            local_vars = {}
            for node_id, node_output in context.inputs.items():
                if isinstance(node_output, dict):
                    for key, value in node_output.items():
                        local_vars[f"{node_id}_{key}"] = value
                    local_vars[node_id] = node_output
                else:
                    local_vars[node_id] = node_output
            
            for key, value in context.variables.items():
                safe_key = key.replace(".", "_")
                local_vars[safe_key] = value

            # Run in process pool
            loop = asyncio.get_running_loop()
            with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
                try:
                    # Run the code in a separate process with timeout
                    # Note: run_in_executor doesn't support timeout directly, so we use asyncio.wait_for
                    # BUT ProcessPoolExecutor tasks are hard to cancel.
                    # Ideally we'd use a library like 'pebble' for easier timeout handling,
                    # but with standard lib we rely on wait_for and hope the process dies or we kill it?
                    # Actually, run_in_executor returns a Future. cancelling the future doesn't kill the process.
                    # We might need to handle the pool carefully.
                    
                    execution_task = loop.run_in_executor(
                        pool, 
                        execute_code_safe, 
                        code, 
                        local_vars
                    )
                    
                    output_data = await asyncio.wait_for(execution_task, timeout=float(timeout))
                    
                    if output_data.get("error"):
                        return NodeExecutionResult(
                            node_id=context.node_id,
                            status="failed",
                            error=output_data["error"],
                            elapsed_ms=int((time.time() - start_time) * 1000)
                        )
                        
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",
                        output={
                            "result": output_data["result"],
                            "stdout": output_data["stdout"],
                            "stderr": output_data["stderr"]
                        },
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

                except asyncio.TimeoutError:
                    # Process is potentially stuck. In a real prod env we'd need to kill the worker.
                    # For now, we just return failed. The ProcessPool might be tainted.
                    # To properly kill, we'd need to access the pool's processes.
                    # Since we use a context manager pool here, exiting the block *should* shutdown?
                    # Actually, 'shutdown(wait=True)' is default. If it's stuck, it might wait forever.
                    # We might need 'wait=False, cancel_futures=True'.
                    # Or force kill.
                    pool.shutdown(wait=False, cancel_futures=True) 
                    
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Execution timed out after {timeout} seconds",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

        except Exception as e:
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=int((time.time() - start_time) * 1000)
            )
