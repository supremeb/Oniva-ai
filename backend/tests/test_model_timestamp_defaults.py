"""Python-side timestamp defaults on timestamptz columns must be tz-aware UTC.

A naive ``datetime.utcnow()`` is read by Postgres as server-local time, which put ``created_at`` hours
ahead of ``started_at`` (which was set tz-aware) on workflow, batch and schedule rows.
"""
import pytest

from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution
from app.models.workflow import WorkflowExecution, WorkflowVersion
from app.models.workflow_batch import WorkflowBatch, WorkflowBatchItem

MODELS = [ScheduledWorkflow, ScheduledWorkflowExecution, WorkflowExecution, WorkflowVersion, WorkflowBatch, WorkflowBatchItem]


@pytest.mark.parametrize("model", MODELS)
def test_python_defaults_are_timezone_aware(model):
    for col in model.__table__.columns:
        if getattr(col.type, "timezone", False) and col.default is not None:
            value = col.default.arg(None)
            assert value.tzinfo is not None, f"{model.__name__}.{col.name} default is naive"
