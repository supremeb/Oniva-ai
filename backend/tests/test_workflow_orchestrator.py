"""Integration tests for workflow orchestrator"""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.workflow_orchestrator import WorkflowOrchestrator
from app.node_executors import NodeExecutionResult


@pytest.mark.asyncio
async def test_topological_sort(test_db, test_user_id):
    """Test workflow execution order"""
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)

    nodes = [
        {"id": "1", "type": "http", "data": {}},
        {"id": "2", "type": "llm", "data": {}},
        {"id": "3", "type": "compliance", "data": {}},
    ]

    edges = [
        {"source": "1", "target": "2"},
        {"source": "2", "target": "3"},
    ]

    order = orchestrator._topological_sort(nodes, edges)

    assert order == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_workflow_execution_single_node(test_db, test_user_id):
    """Test execution of single-node workflow"""
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)

    spec = {
        "nodes": [
            {
                "id": "1",
                "type": "llm",
                "data": {"message": "Test analysis"},
                "position": {"x": 0, "y": 0}
            }
        ],
        "edges": []
    }

    # Mock agent execution
    with patch.object(orchestrator, '_execute_node', new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = NodeExecutionResult(
            node_id="1",
            status="success",
            output={"ticker": "AAPL", "recommendation": "buy"}
        )

        result = await orchestrator.execute_workflow(spec)

        assert result["status"] == "completed"
        assert result["executed_nodes"] == 1
        assert result["total_nodes"] == 1


@pytest.mark.asyncio
async def test_workflow_execution_sequential(test_db, test_user_id):
    """Test execution of sequential workflow"""
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)

    spec = {
        "nodes": [
            {
                "id": "1",
                "type": "http",
                "data": {"message": "Fetch data"},
                "position": {"x": 0, "y": 0}
            },
            {
                "id": "2",
                "type": "llm",
                "data": {"message": "Analyze data"},
                "position": {"x": 300, "y": 0}
            }
        ],
        "edges": [
            {"id": "e1-2", "source": "1", "target": "2"}
        ]
    }

    # Mock agent execution
    with patch.object(orchestrator, '_execute_node', new_callable=AsyncMock) as mock_execute:
        mock_execute.side_effect = [
            NodeExecutionResult(node_id="1", status="success", output={"data": "test"}),
            NodeExecutionResult(node_id="2", status="success", output={"recommendation": "buy"})
        ]

        result = await orchestrator.execute_workflow(spec)

        assert result["status"] == "completed"
        assert result["executed_nodes"] == 2
        assert mock_execute.call_count == 2


@pytest.mark.asyncio
async def test_workflow_circular_dependency_error(test_db, test_user_id):
    """Test that circular dependencies raise error"""
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)

    nodes = [
        {"id": "1", "type": "http", "data": {}},
        {"id": "2", "type": "llm", "data": {}},
    ]

    edges = [
        {"source": "1", "target": "2"},
        {"source": "2", "target": "1"},  # Circular!
    ]

    with pytest.raises(ValueError, match="cycle"):
        orchestrator._topological_sort(nodes, edges)


@pytest.mark.asyncio
async def test_get_node_inputs(test_db, test_user_id):
    """Test gathering inputs from predecessor nodes"""
    orchestrator = WorkflowOrchestrator(test_db, test_user_id)

    edges = [
        {"source": "1", "target": "2"},
        {"source": "3", "target": "2"},
    ]

    results = {
        "1": {"status": "completed", "result": "data1"},
        "3": {"status": "completed", "result": "data3"},
    }

    inputs = orchestrator._get_node_inputs("2", edges, results)

    assert "1" in inputs
    assert "3" in inputs
    assert inputs["1"]["result"] == "data1"
