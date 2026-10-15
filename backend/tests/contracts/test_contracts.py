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


@pytest.mark.parametrize("scenario", ["text", "tool_call", "error"])
async def test_chat_sse_contract(scenario):
    live = await streams.run_chat_scenario(scenario)
    check_or_record(f"chat_{scenario}", live)


@pytest.mark.parametrize("scenario", ["success", "failure"])
async def test_deep_work_engine_contract(scenario):
    live = await streams.run_deep_work_scenario(scenario)
    check_or_record(f"deep_work_{scenario}", live)


@pytest.mark.parametrize("scenario", ["completed", "failed", "cancelled"])
async def test_fleet_background_agent_contract(scenario):
    live = await streams.run_fleet_scenario(scenario)
    check_or_record(f"fleet_{scenario}", live)


@pytest.mark.parametrize("scenario", ["success", "unknown_node"])
async def test_workflow_progress_contract(scenario):
    live = await streams.run_workflow_scenario(scenario)
    check_or_record(f"workflow_{scenario}", live)
