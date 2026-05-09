"""
Tests for C4 fix: Make durability default

Verifies that:
- USE_DURABLE_WORKFLOWS=True uses DurableWorkflowEngine
- USE_DURABLE_WORKFLOWS=False uses WorkflowOrchestrator
- /execute-durable endpoint unchanged (backward compatibility)
"""

import pytest
from unittest.mock import patch, AsyncMock
from uuid import uuid4


class TestDurabilityFlagRouting:
    """Test that the /execute endpoint respects USE_DURABLE_WORKFLOWS flag."""

    @pytest.fixture
    def mock_workflow_spec(self):
        """Basic workflow spec for testing."""
        return {
            "nodes": [
                {"id": "1", "type": "start", "data": {}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "llm", "data": {"model": "gpt-4o-mini"}, "position": {"x": 200, "y": 0}},
                {"id": "3", "type": "output", "data": {}, "position": {"x": 400, "y": 0}}
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3"}
            ]
        }

    @pytest.fixture
    def mock_execution_result(self):
        """Mock result from workflow execution."""
        return {
            "status": "completed",
            "executed_nodes": 3,
            "total_nodes": 3,
            "results": {
                "1": {"status": "success", "output": {}},
                "2": {"status": "success", "output": {"text": "Hello"}},
                "3": {"status": "success", "output": {"message": "Hello"}}
            }
        }

    @pytest.mark.asyncio
    async def test_execute_uses_durable_engine_when_flag_enabled(
        self, mock_workflow_spec, mock_execution_result
    ):
        """Verify /execute uses DurableWorkflowEngine when USE_DURABLE_WORKFLOWS=True."""
        from app.api.v1.workflows import execute_workflow
        from app.api.v1.workflows import ExecuteWorkflowRequest
        from app.api.v1.workflows import WorkflowNode, WorkflowEdge

        # Create request
        nodes = [WorkflowNode(**n) for n in mock_workflow_spec["nodes"]]
        edges = [WorkflowEdge(**e) for e in mock_workflow_spec["edges"]]
        request = ExecuteWorkflowRequest(nodes=nodes, edges=edges, inputs={})

        # Mock dependencies
        mock_db = AsyncMock()
        mock_user_id = uuid4()
        mock_tenant_id = uuid4()

        with patch('app.api.v1.workflows.settings') as mock_settings:
            mock_settings.USE_DURABLE_WORKFLOWS = True

            with patch('app.api.v1.workflows.DurableWorkflowEngine') as MockEngine:
                mock_engine = AsyncMock()
                mock_engine.execute_workflow.return_value = mock_execution_result
                MockEngine.return_value = mock_engine

                result = await execute_workflow(
                    request=request,
                    db=mock_db,
                    user_id=mock_user_id,
                    tenant_id=mock_tenant_id
                )

                # Verify DurableWorkflowEngine was instantiated
                MockEngine.assert_called_once()
                assert result.status == "completed"
                assert result.executed_nodes == 3

    @pytest.mark.asyncio
    async def test_execute_uses_orchestrator_when_flag_disabled(
        self, mock_workflow_spec, mock_execution_result
    ):
        """Verify /execute uses WorkflowOrchestrator when USE_DURABLE_WORKFLOWS=False."""
        from app.api.v1.workflows import execute_workflow
        from app.api.v1.workflows import ExecuteWorkflowRequest
        from app.api.v1.workflows import WorkflowNode, WorkflowEdge

        # Create request
        nodes = [WorkflowNode(**n) for n in mock_workflow_spec["nodes"]]
        edges = [WorkflowEdge(**e) for e in mock_workflow_spec["edges"]]
        request = ExecuteWorkflowRequest(nodes=nodes, edges=edges, inputs={})

        # Mock dependencies
        mock_db = AsyncMock()
        mock_user_id = uuid4()
        mock_tenant_id = uuid4()

        with patch('app.api.v1.workflows.settings') as mock_settings:
            mock_settings.USE_DURABLE_WORKFLOWS = False

            with patch('app.api.v1.workflows.WorkflowOrchestrator') as MockOrchestrator:
                mock_orchestrator = AsyncMock()
                mock_orchestrator.execute_workflow.return_value = mock_execution_result
                MockOrchestrator.return_value = mock_orchestrator

                result = await execute_workflow(
                    request=request,
                    db=mock_db,
                    user_id=mock_user_id,
                    tenant_id=mock_tenant_id
                )

                # Verify WorkflowOrchestrator was used
                MockOrchestrator.assert_called_once()
                assert result.status == "completed"


class TestDurableEngineDirectEndpoint:
    """Test that /execute-durable endpoint still works directly."""

    @pytest.mark.asyncio
    async def test_execute_durable_endpoint_always_uses_engine(self):
        """Verify /execute-durable always uses DurableWorkflowEngine regardless of flag."""
        from app.api.v1.workflows import execute_durable_workflow
        from app.api.v1.workflows import ExecuteWorkflowRequest
        from app.api.v1.workflows import WorkflowNode

        mock_workflow_spec = {
            "nodes": [
                {"id": "1", "type": "start", "data": {}, "position": {"x": 0, "y": 0}}
            ],
            "edges": []
        }

        nodes = [WorkflowNode(**n) for n in mock_workflow_spec["nodes"]]
        edges = []
        request = ExecuteWorkflowRequest(nodes=nodes, edges=edges, inputs={})

        mock_db = AsyncMock()
        mock_user_id = uuid4()
        mock_tenant_id = uuid4()

        mock_result = {
            "status": "completed",
            "executed_nodes": 1,
            "total_nodes": 1,
            "results": {},
            "dbos_workflow_uuid": str(uuid4()),
            "durable": True,
            "checkpoints": 1
        }

        # Even with flag disabled, /execute-durable should use DurableWorkflowEngine
        with patch('app.api.v1.workflows.settings') as mock_settings:
            mock_settings.USE_DURABLE_WORKFLOWS = False

            with patch('app.api.v1.workflows.DurableWorkflowEngine') as MockEngine:
                mock_engine = AsyncMock()
                mock_engine.execute_workflow.return_value = mock_result
                MockEngine.return_value = mock_engine

                result = await execute_durable_workflow(
                    request=request,
                    db=mock_db,
                    user_id=mock_user_id,
                    tenant_id=mock_tenant_id
                )

                # Verify DurableWorkflowEngine was used
                MockEngine.assert_called_once()
                assert result.durable == True


class TestDurabilityWithInputs:
    """Test input variable handling with both execution paths."""

    @pytest.mark.asyncio
    async def test_sys_query_preserved_with_durable_engine(self):
        """Verify sys.query input is properly passed to durable engine."""
        from app.api.v1.workflows import execute_workflow
        from app.api.v1.workflows import WorkflowNode
        from app.api.v1.workflows import ExecuteWorkflowRequest

        nodes = [WorkflowNode(id="1", type="start", data={}, position={"x": 0, "y": 0})]
        request = ExecuteWorkflowRequest(
            nodes=nodes,
            edges=[],
            inputs={"query": "What is the market cap of AAPL?"}
        )

        mock_db = AsyncMock()
        mock_user_id = uuid4()
        mock_tenant_id = uuid4()

        with patch('app.api.v1.workflows.settings') as mock_settings:
            mock_settings.USE_DURABLE_WORKFLOWS = True

            with patch('app.api.v1.workflows.DurableWorkflowEngine') as MockEngine:
                mock_engine = AsyncMock()
                mock_engine.execute_workflow.return_value = {
                    "status": "completed",
                    "executed_nodes": 1,
                    "total_nodes": 1,
                    "results": {}
                }
                MockEngine.return_value = mock_engine

                await execute_workflow(
                    request=request,
                    db=mock_db,
                    user_id=mock_user_id,
                    tenant_id=mock_tenant_id
                )

                # Verify input_variables were passed correctly
                call_args = mock_engine.execute_workflow.call_args
                input_vars = call_args.kwargs.get('input_variables', call_args[1].get('input_variables', {}))

                assert input_vars.get("query") == "What is the market cap of AAPL?"
                assert input_vars.get("sys.query") == "What is the market cap of AAPL?"
