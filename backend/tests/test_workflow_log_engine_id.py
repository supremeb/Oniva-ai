"""Plan 003 U26: the workflow log summary and detail carry the engine conversation the UI follows while a run is live."""
import uuid
from datetime import datetime, timezone

from app.api.v1.workflows import WorkflowLogDetail, WorkflowLogSummary
from app.models.workflow import WorkflowExecution


def row(**over) -> WorkflowExecution:
    base = dict(id=uuid.uuid4(), workflow_id=uuid.uuid4(), status="running", trigger="manual", nodes_executed=1,
                total_nodes=4, total_tokens=0, error=None, execution_trace=[], input_data={}, results={},
                started_at=datetime.now(timezone.utc), completed_at=None, duration_ms=None)
    base.update(over)
    return WorkflowExecution(**base)


def test_engine_run_summary_and_detail_expose_the_conversation_id():
    conversation = uuid.uuid4()
    ex = row(engine_conversation_id=conversation)
    assert WorkflowLogSummary(**ex.to_log_summary()).engine_conversation_id == str(conversation)
    assert WorkflowLogDetail(**ex.to_log_detail()).engine_conversation_id == str(conversation)


def test_legacy_run_has_no_conversation_id():
    ex = row(engine_conversation_id=None)
    assert WorkflowLogSummary(**ex.to_log_summary()).engine_conversation_id is None
