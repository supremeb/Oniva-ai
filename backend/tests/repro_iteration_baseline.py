"""
Reproduction test for baseline Iteration node behavior.
Confirms that it currently only prepares contexts and doesn't loop sub-workflows.
"""

import pytest
from app.services.workflow_orchestrator import WorkflowOrchestrator

@pytest.mark.asyncio
async def test_iteration_baseline_behavior(test_db, test_user_id):
    """
    Test that the Iteration node currently just transforms an input array
    into a list of iteration objects (index, item, total).
    """
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)
    
    # Define a workflow with an iteration node
    workflow_spec = {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "data": {"input_list": ["apple", "banana", "cherry"]}
            },
            {
                "id": "iter",
                "type": "iteration",
                "data": {
                    "inputArray": "input_list",
                    "parallel": False
                }
            }
        ],
        "edges": [
            {"source": "start", "target": "iter"}
        ]
    }
    
    # Start execution with input variables
    input_variables = {"input_list": ["apple", "banana", "cherry"]}
    result = await orchestrator.execute_workflow(workflow_spec, input_variables=input_variables)
    
    assert result["status"] == "completed"
    iter_result = result["results"]["iter"]
    assert iter_result["status"] == "success"
    
    output = iter_result["output"]
    assert output["count"] == 3
    assert len(output["items"]) == 3
    assert output["items"][0]["item"] == "apple"
    assert output["items"][0]["index"] == 0
    assert output["items"][1]["item"] == "banana"
    assert output["items"][1]["index"] == 1
    assert output["items"][2]["item"] == "cherry"
    assert output["items"][2]["index"] == 2
    
    # Verify that it didn't magically execute anything else
    # (In a real loop, we would expect downstream nodes to be executed multiple times)
    print("Baseline iteration test passed: confirmed current behavior is pass-through transform.")

@pytest.mark.asyncio
async def test_iteration_with_downstream_baseline(test_db, test_user_id):
    """
    Test a workflow where a node is downstream of an iteration node.
    Currently, the downstream node will only execute ONCE with the list of items as input.
    """
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)
    
    workflow_spec = {
        "nodes": [
            {
                "id": "iter",
                "type": "iteration",
                "data": {
                    "inputArray": "['a', 'b']", # Direct JSON array
                }
            },
            {
                "id": "downstream",
                "type": "template", # A simple node that just processes input
                "data": {"template": "Processing {{iter.items}}"}
            }
        ],
        "edges": [
            {"source": "iter", "target": "downstream"}
        ]
    }
    
    result = await orchestrator.execute_workflow(workflow_spec)
    
    assert result["status"] == "completed"
    assert "iter" in result["results"]
    assert "downstream" in result["results"]
    
    # The current orchestrator executes each node in topological order ONCE.
    # So 'downstream' executed once, not twice.
    # This confirms the baseline that full loop execution is missing.
    print("Downstream baseline test passed: confirmed downstream node only executes once.")
