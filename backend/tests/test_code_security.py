"""
Security tests for CodeExecutor
"""

import pytest
from app.node_executors.code_executor import CodeExecutor
from app.node_executors import NodeExecutionContext

@pytest.mark.asyncio
async def test_code_executor_blocks_os_import(test_db, test_user_id):
    """
    Test that the CodeExecutor blocks importing the 'os' module.
    """
    executor = CodeExecutor()
    
    context = NodeExecutionContext(
        node_id="test_node",
        node_type="code",
        node_data={
            "code": "import os\nresult = os.getcwd()",
            "language": "python3"
        },
        inputs={},
        variables={},
        user_id=str(test_user_id)
    )
    
    result = await executor.execute(context, test_db)
    
    # We expect this to fail
    assert result.status == "failed"
    # Ideally, it should say something about import not allowed or NameError
    # Or specifically "__import__ not found" if we removed it from builtins
    assert "Execution error" in result.error or "__import__ not found" in result.error or "NameError" in result.error or "not allowed" in result.error

@pytest.mark.asyncio
async def test_code_executor_timeout(test_db, test_user_id):
    """
    Test that the CodeExecutor times out for infinite loops.
    """
    executor = CodeExecutor()
    
    context = NodeExecutionContext(
        node_id="test_timeout",
        node_type="code",
        node_data={
            "code": "while True: pass",
            "language": "python3",
            "timeout": 1 # 1 second timeout
        },
        inputs={},
        variables={},
        user_id=str(test_user_id)
    )
    
    # This should return a failed result, not hang forever
    import time
    import asyncio
    start = time.time()
    
    try:
        # Wrap in a test-level timeout to prevent hanging the suite
        result = await asyncio.wait_for(executor.execute(context, test_db), timeout=3.0)
    except asyncio.TimeoutError:
        pytest.fail("Executor did not time out the user code")
        
    duration = time.time() - start
    
    assert result.status == "failed"
    assert "Timeout" in result.error or "timed out" in result.error
    assert duration < 2.0 # Should be close to 1 second

