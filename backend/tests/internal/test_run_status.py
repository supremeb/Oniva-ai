"""Plan 003 U2 / KTD10: the single legacy -> run status mapping (shared fixture with the frontend)."""
import json
import logging
from pathlib import Path

import pytest

from app.services import run_status

FIXTURE = Path(__file__).parent.parent / "fixtures" / "run_status_map.json"
MAP = json.loads(FIXTURE.read_text())


def test_fixture_round_trips():
    assert json.loads(json.dumps(MAP)) == MAP
    assert list(run_status.RUN_STATUSES) == MAP["statuses"]


@pytest.mark.parametrize("legacy,expected", sorted(MAP["legacy"].items()))
def test_every_legacy_value_maps_to_exactly_one_enum_value(legacy, expected):
    assert expected in MAP["statuses"]
    assert run_status.normalize(legacy, "job") == expected
    assert run_status.normalize(legacy.upper(), "execution") == expected


def test_all_legacy_job_and_execution_strings_are_covered():
    for s in ("pending", "running", "completed", "failed", "cancelled", "error", "success"):
        assert s in MAP["legacy"]


@pytest.mark.parametrize("bad", ["weird", "", None, 7])
def test_unknown_maps_to_failed_and_logs_never_raises(bad, caplog):
    with caplog.at_level(logging.WARNING):
        assert run_status.normalize(bad, "job") == "failed"
    assert any("unknown run status" in r.message for r in caplog.records)


@pytest.mark.parametrize("status,expected", sorted(MAP["engine_task"].items()))
def test_from_engine_task_live_states(status, expected):
    assert run_status.from_engine_task(status) == expected


def test_from_engine_task_waiting_and_resumed():
    assert run_status.from_engine_task("waiting", wake="deadline") == "waiting"
    assert run_status.from_engine_task("running", resumed=True) == "resuming"
    assert run_status.from_engine_task("pending", resumed=True) == "queued"


@pytest.mark.parametrize("outcome,expected", sorted(MAP["engine_outcome"].items()))
def test_from_engine_task_terminal_outcomes(outcome, expected):
    assert run_status.from_engine_task("terminal", outcome=outcome) == expected
    assert run_status.from_engine_task("terminal", outcome={"type": outcome}) == expected


def test_from_engine_task_unknown_is_failed():
    assert run_status.from_engine_task("terminal") == "failed"
    assert run_status.from_engine_task("bogus") == "failed"
