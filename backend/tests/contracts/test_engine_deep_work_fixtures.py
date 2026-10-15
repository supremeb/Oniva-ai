"""Engine-path Deep Work fixtures (plan 2026-10-15-002, U11) and the guard that every legacy one has a counterpart.

``fixtures/deep_work_engine_<scenario>.json`` holds what the real engine produced for a scripted Deep Work run
(an orchestrator configured by ``compose_execute_config``, one worker per round, a reflection that adds ``gap-1``,
a second that stops, two sections, finalize): ``{legacy, points, frames}``, where ``points`` are conversation
views at named moments and ``frames`` is the complete watch stream (snapshot first), ids normalized. The source
is ``engine/test/fixtures/deep-work/deep_work_views.json``, written by
``engine/test/watch/deep-work-fixtures.test.ts``; this test keeps the backend copies in sync with it and checks
each view still carries what the legacy fixture persisted.

    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts/test_engine_deep_work_fixtures.py   # re-record
"""
import json
from pathlib import Path

import pytest

from .recorder import check_or_record

FIXTURES = Path(__file__).parent / "fixtures"
ENGINE_VIEWS = Path(__file__).resolve().parents[3] / "engine/test/fixtures/deep-work/deep_work_views.json"
SCENARIOS = ["success", "failure", "cancel"]
LEGACY = {"success": "deep_work_success", "failure": "deep_work_failure", "cancel": None}


def legacy_deep_work_fixtures() -> list[str]:
    return sorted(p.stem.removeprefix("deep_work_") for p in FIXTURES.glob("deep_work_*.json")
                  if not p.stem.startswith("deep_work_engine_"))


def engine_views() -> dict:
    if not ENGINE_VIEWS.exists():
        pytest.skip("engine fixtures not checked out")
    return json.loads(ENGINE_VIEWS.read_text())


def tool_names(view: dict) -> list[str]:
    return [e["payload"]["toolName"] for e in view["entries"] if e["kind"] == "tool_result"]


def test_every_legacy_deep_work_fixture_has_an_engine_counterpart():
    missing = [s for s in legacy_deep_work_fixtures() if not (FIXTURES / f"deep_work_engine_{s}.json").exists()]
    assert not missing, f"legacy deep_work fixtures without a deep_work_engine_* counterpart: {missing}"
    assert {f"deep_work_{s}" for s in legacy_deep_work_fixtures()} <= set(LEGACY.values())  # drive new scenarios here


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_engine_view_fixture_is_current(scenario):
    recorded = engine_views()[scenario]
    assert recorded["legacy"] == LEGACY[scenario]
    check_or_record(f"deep_work_engine_{scenario}", recorded)


def test_success_matches_the_legacy_run():
    engine = json.loads((FIXTURES / "deep_work_engine_success.json").read_text())
    legacy = json.loads((FIXTURES / "deep_work_success.json").read_text())
    final = engine["points"][-1]["view"]
    events = [e["event"] for e in legacy["events"]]
    names = tool_names(final)
    # Same shape as the legacy run: two research rounds (the second from a gap), a reflection after each.
    assert names.count("research_worker") == events.count("deep_work_engine_worker_started") == 2
    assert names.count("reflector") == events.count("deep_work_engine_reflection") == 2
    assert names.count("dw_start_round") == 2
    assert names[-1] == "dw_finalize" and names.count("dw_write_section") == 2
    assert not final["run"]["active"]
    assert sorted(c["status"] for c in final["subagents"].values()) == ["done"] * 4
    # Deliberate difference (KTD9): the legacy report has four model-written sections plus Sources; the
    # scripted engine run writes two, and Sources is built by dw_finalize in the backend, not by the model.
    assert "## Sources" in legacy["report"]
    # The frame stream rebuilds the final view: the first frame is a snapshot.
    assert "snapshot" in engine["frames"][0]
    assert engine["frames"][-1]["cursor"] >= engine["frames"][0]["cursor"]


def test_failure_keeps_the_worker_fault_and_ends_in_run_error():
    engine = json.loads((FIXTURES / "deep_work_engine_failure.json").read_text())
    faulted, failed = (p["view"] for p in engine["points"])
    # The worker failed, yet the run continued to a reflection.
    assert sorted(c["status"] for c in faulted["subagents"].values()) == ["failed", "running"]
    assert faulted["run"]["active"]
    result = next(e for e in faulted["entries"] if e["kind"] == "tool_result" and e["payload"]["toolName"] == "research_worker")
    assert result["payload"]["isError"] and "did not answer" in result["payload"]["content"]
    # Legacy: the orchestrator failure surfaced as an error and no report. Engine: a run_error entry.
    assert not failed["run"]["active"]
    assert [e["kind"] for e in failed["entries"] if e["kind"] == "run_error"] == ["run_error"]
    assert "dw_finalize" not in tool_names(failed)


def test_cancel_ends_with_no_running_worker():
    engine = json.loads((FIXTURES / "deep_work_engine_cancel.json").read_text())
    running, cancelled = (p["view"] for p in engine["points"])
    assert [c["status"] for c in running["subagents"].values()] == ["running"]
    assert not cancelled["run"]["active"]
    assert [c["status"] for c in cancelled["subagents"].values()] == ["aborted"]
    assert "dw_save_learnings" not in tool_names(cancelled)
