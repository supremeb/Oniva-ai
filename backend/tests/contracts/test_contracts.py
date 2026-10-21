"""Golden event-contract tests (U12, R13).

Each test drives one production stream with a fake model, normalizes ids /
timestamps / durations, and compares against tests/contracts/fixtures/*.json.

    uv run pytest tests/contracts                       # compare
    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts   # re-record

Fixtures record the CURRENT contract, quirks included (KTD5). See README.md.
"""
import pytest

from .recorder import check_or_record
from . import streams
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)
from tests.test_workflow_engine_start import w  # noqa: F401  (fixture)


@pytest.mark.parametrize("scenario", ["text", "tool_call", "error"])
async def test_chat_sse_contract(scenario):
    live = await streams.run_chat_scenario(scenario)
    check_or_record(f"chat_{scenario}", live)


@pytest.mark.parametrize("scenario", ["completed", "failed", "cancelled"])
async def test_fleet_background_agent_contract(scenario):
    live = await streams.run_fleet_scenario(scenario)
    check_or_record(f"fleet_{scenario}", live)


@pytest.mark.parametrize("scenario", ["success", "unknown_node"])
async def test_workflow_progress_contract(scenario):
    live = await streams.run_workflow_scenario(scenario)
    check_or_record(f"workflow_{scenario}", live)


@pytest.mark.parametrize("scenario", ["success", "failure", "skipped"])
async def test_workflow_engine_stream_contract(scenario, w):
    """Plan 003 U17: scripted engine watch views -> the legacy event stream (re-recorded deliberately)."""
    from app.services import workflow_stream_adapter as wsa
    from tests.test_workflow_stream_adapter import started

    spec = streams.ENGINE_STREAM_SCENARIOS[scenario]
    eid = await started(w, spec["trace"])
    engine = streams.ScriptedEngine(streams.ScriptedWatch(streams.snapshot_frames(spec["views"])))
    live = [e async for e in wsa.watch_events(w.factory, eid, "task-1", engine=engine)]
    check_or_record(f"workflow_stream_engine_{scenario}", {"events": live})


async def test_engine_stream_matches_the_legacy_event_order(w):
    from app.services import workflow_stream_adapter as wsa
    from tests.test_workflow_stream_adapter import started
    import json
    from .recorder import FIXTURE_DIR

    spec = streams.ENGINE_STREAM_SCENARIOS["success"]
    eid = await started(w, spec["trace"])
    engine = streams.ScriptedEngine(streams.ScriptedWatch(streams.snapshot_frames(spec["views"])))
    live = [e async for e in wsa.watch_events(w.factory, eid, "task-1", engine=engine)]
    legacy = json.loads((FIXTURE_DIR / "workflow_success.json").read_text())["orchestrator_events"]
    keep = {"progress", "node_complete", "complete"}
    assert [(e["type"], e.get("node_id")) for e in live] == [
        (e["type"], e.get("node_id")) for e in legacy if e["type"] in keep]
