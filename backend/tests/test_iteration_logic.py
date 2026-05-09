"""
Tests for the refactored Iteration node logic.
Expects the node to handle sub-workflow execution.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from app.services.workflow_orchestrator import WorkflowOrchestrator


# Override conftest fixtures locally -- the iteration/template executors
# do not touch the database, and WorkflowOrchestrator skips DB logging
# when tenant_id is None, so an AsyncMock is sufficient.
@pytest.fixture
def test_db():
    return AsyncMock()


@pytest.fixture
def test_user_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_iteration_with_target_node(test_db, test_user_id):
    """
    Test that the Iteration node can be configured with a target node
    to execute for each item.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    # Define a workflow where 'iter' loops over 'processor'
    workflow_spec = {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "data": {"label": "Start"}
            },
            {
                "id": "iter",
                "type": "iteration",
                "data": {
                    "inputArray": "input_list",
                    "targetNodeId": "processor" # The node to run for each item
                }
            },
            {
                "id": "processor",
                "type": "template",
                "data": {
                    "template": "Processed {{iter.item}}"
                }
            }
        ],
        "edges": [
            {"source": "start", "target": "iter"},
            {"source": "iter", "target": "processor"}
        ]
    }
    
    input_variables = {"input_list": ["a", "b", "c"]}
    
    # This should now execute 'processor' 3 times
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    
    # We expect some way to see that 'processor' was executed 3 times
    # Currently the results dict only stores one result per node ID.
    # We might need to refactor how results are stored for iterations.
    
    iter_result = result["results"]["iter"]
    assert iter_result["status"] == "success"
    
    # If it worked, the output of 'iter' should probably contain the aggregated results
    # of the 'processor' executions.
    assert "aggregated_results" in iter_result["output"]
    assert len(iter_result["output"]["aggregated_results"]) == 3

@pytest.mark.asyncio
async def test_iteration_with_multi_node_subgraph(test_db, test_user_id):
    """
    Test that the Iteration node can execute a sequence of nodes for each item.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    # Start -> Iter -> Step1 -> Step2
    workflow_spec = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {
                "id": "iter", 
                "type": "iteration", 
                "data": {"inputArray": "input_list", "targetNodeId": "step1"}
            },
            {"id": "step1", "type": "template", "data": {"template": "Step1: {{iter.item}}"}},
            {"id": "step2", "type": "template", "data": {"template": "Step2: {{step1.text}}"}}
        ],
        "edges": [
            {"source": "start", "target": "iter"},
            {"source": "iter", "target": "step1"},
            {"source": "step1", "target": "step2"}
        ]
    }
    
    input_variables = {"input_list": ["apple", "banana"]}
    
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    iter_result = result["results"]["iter"]
    
    # Check that each item ran both step1 and step2
    agg_results = iter_result["output"]["aggregated_results"]
    assert len(agg_results) == 2
    
    # Item 0 (apple)
    assert "step1" in agg_results[0]["sub_results"]
    assert "step2" in agg_results[0]["sub_results"]
    assert agg_results[0]["sub_results"]["step1"]["text"] == "Step1: apple"
    assert agg_results[0]["sub_results"]["step2"]["text"] == "Step2: Step1: apple"
    
    # Item 1 (banana)
    assert "step1" in agg_results[1]["sub_results"]
    assert "step2" in agg_results[1]["sub_results"]
    assert agg_results[1]["sub_results"]["step1"]["text"] == "Step1: banana"
    assert agg_results[1]["sub_results"]["step2"]["text"] == "Step2: Step1: banana"

@pytest.mark.asyncio
async def test_iteration_with_output_variable(test_db, test_user_id):
    """
    Test that the Iteration node can aggregate a specific variable from sub-results.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    workflow_spec = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {
                "id": "iter", 
                "type": "iteration", 
                "data": {
                    "inputArray": "input_list", 
                    "targetNodeId": "step1",
                    "outputVariable": "step1.text" # Only aggregate this value
                }
            },
            {"id": "step1", "type": "template", "data": {"template": "Value: {{iter.item}}"}}
        ],
        "edges": [
            {"source": "start", "target": "iter"},
            {"source": "iter", "target": "step1"}
        ]
    }
    
    input_variables = {"input_list": ["x", "y"]}
    
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    iter_result = result["results"]["iter"]
    
    # Check that aggregated_results contains just the values
    # Or maybe it should be a separate key in the output
    output = iter_result["output"]
    assert "results" in output
    assert output["results"] == ["Value: x", "Value: y"]

@pytest.mark.asyncio
async def test_iteration_parallel(test_db, test_user_id):
    """
    Test that the Iteration node can execute items in parallel.
    We use a node that sleeps to verify concurrency.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    # We need a way to mock a slow node. 
    # Since we can't easily add a 'sleep' node type now, 
    # we'll just check if the code runs correctly in parallel mode.
    workflow_spec = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {
                "id": "iter", 
                "type": "iteration", 
                "data": {
                    "inputArray": "input_list", 
                    "targetNodeId": "step1",
                    "parallel": True
                }
            },
            {"id": "step1", "type": "template", "data": {"template": "Value: {{iter.item}}"}}
        ],
        "edges": [
            {"source": "start", "target": "iter"},
            {"source": "iter", "target": "step1"}
        ]
    }
    
    input_variables = {"input_list": ["a", "b", "c", "d", "e"]}
    
    import time
    start_time = time.time()
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    end_time = time.time()
    
    assert result["status"] == "completed"
    assert len(result["results"]["iter"]["output"]["aggregated_results"]) == 5
    print(f"Parallel iteration completed in {end_time - start_time:.4f}s")

@pytest.mark.asyncio
async def test_iteration_avoids_double_execution(test_db, test_user_id):
    """
    Test that nodes inside the iteration loop are NOT executed 
    by the main orchestrator loop.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    # Start -> Iter -> Body -> After
    workflow_spec = {
        "nodes": [
            {"id": "start", "type": "start", "data": {}},
            {
                "id": "iter", 
                "type": "iteration", 
                "data": {"inputArray": "input_list", "targetNodeId": "body"}
            },
            {"id": "body", "type": "template", "data": {"template": "Body: {{iter.item}}"}},
            {"id": "after", "type": "template", "data": {"template": "After: {{iter.count}}"}}
        ],
        "edges": [
            {"source": "start", "target": "iter"},
            {"source": "iter", "target": "body"},
            {"source": "body", "target": "after"}
        ]
    }
    
    input_variables = {"input_list": ["a"]}
    
    # We'll use a mock to track how many times _execute_node is called for 'body'
    with patch.object(orchestrator, '_execute_node', wraps=orchestrator._execute_node) as mock_execute:
        result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
        
        assert result["status"] == "completed"
        
        # Check call count for 'body'
        # It should be called ONCE by the IterationExecutor (for 1 item)
        # And ZERO times by the main orchestrator loop.
        # Total = 1.
        
        body_calls = [call for call in mock_execute.call_args_list if call.kwargs.get('node_id') == 'body']
        
        # If this fails with 2, it confirms the bug.
        assert len(body_calls) == 1, f"Body should be called once, but was called {len(body_calls)} times"

@pytest.mark.asyncio
async def test_iteration_empty_list(test_db, test_user_id):
    """
    Test that the Iteration node handles empty lists correctly.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    workflow_spec = {
        "nodes": [
            {"id": "iter", "type": "iteration", "data": {"inputArray": "input_list"}}
        ],
        "edges": []
    }
    
    input_variables = {"input_list": []}
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    assert result["results"]["iter"]["output"]["count"] == 0
    assert result["results"]["iter"]["output"]["items"] == []

@pytest.mark.asyncio
async def test_iteration_large_list(test_db, test_user_id):
    """
    Test that the Iteration node handles large lists correctly.
    """
    orchestrator = WorkflowOrchestrator(test_db, str(test_user_id))
    
    workflow_spec = {
        "nodes": [
            {
                "id": "iter", 
                "type": "iteration", 
                "data": {"inputArray": "input_list", "targetNodeId": "body", "parallel": True}
            },
            {"id": "body", "type": "template", "data": {"template": "Item: {{iter.item}}"}}
        ],
        "edges": [
            {"source": "iter", "target": "body"}
        ]
    }
    
    # 50 items
    input_variables = {"input_list": [f"item_{i}" for i in range(50)]}
    
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    assert result["results"]["iter"]["output"]["count"] == 50
    assert len(result["results"]["iter"]["output"]["aggregated_results"]) == 50
    assert result["results"]["iter"]["output"]["aggregated_results"][49]["item"] == "item_49"
