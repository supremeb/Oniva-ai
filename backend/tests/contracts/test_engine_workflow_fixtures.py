"""Engine-path workflow fixtures (plan 2026-10-17-003, U16).

``fixtures/workflow_engine_<scenario>.json`` holds the settled ``workflow_executions`` row after the run task's
``finalize`` (the read model the logs and history endpoints serve), next to the legacy ``workflow_<scenario>.json``.

    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts/test_engine_workflow_fixtures.py   # re-record
"""
import pytest

from tests.contracts.recorder import check_or_record
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)
from tests.internal.test_workflow_nodes import counts, wf  # noqa: F401  (fixtures)
from tests.test_workflow_run_sync import add_usage, bind, finalize, row, step, tenants, trace  # noqa: F401


@pytest.mark.parametrize("scenario", ["success", "failure", "branch"])
async def test_workflow_engine_read_model_contract(scenario, wf):  # noqa: F811
    await bind(wf)
    await add_usage(wf, 30, 12)
    if scenario == "success":
        await trace(wf, step("start-1"), step("tmpl-1", output={"output": "Hello Ada"}), step("out-1"))
        await finalize(wf, "completed", {"start-1": "success", "tmpl-1": "success", "out-1": "success"})
    elif scenario == "failure":
        await trace(wf, step("start-1"), step("n2", "failed", error="boom"))
        await finalize(wf, "failed", {"start-1": "success", "n2": "failed", "n3": "skipped"},
                       {"nodeId": "n2", "message": "boom"})
    else:
        await trace(wf, step("start-1"), step("a"))
        await finalize(wf, "completed", {"start-1": "success", "a": "success", "b": "skipped"})
    got = await row(wf)
    live = {"status": got.status, "results": got.results, "error": got.error, "nodes_executed": got.nodes_executed,
            "total_tokens": got.total_tokens, "completed_at": got.completed_at.isoformat(),
            "duration_ms": got.duration_ms, "execution_trace": got.execution_trace}
    check_or_record(f"workflow_engine_{scenario}", live)
