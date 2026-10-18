"""U36: the smoke profile (5 workflow runs, 1 Fleet run) end to end, so the load driver does not rot.

Marked ``load`` and skipped unless ONIVA_E2E=1 (it needs Postgres and an engine worker); run with ``-m load``.
"""
import pytest

from tests.load import automation_load as drv

pytestmark = [pytest.mark.load, pytest.mark.e2e]


def test_smoke_profile_runs_end_to_end(world):
    result = drv.run_profile(world, drv.PROFILES["smoke"], world.start_worker(1))
    drv.print_report(result)
    assert result["workload"]["workflow_runs"] == 5 and result["workload"]["fleet_runs"] == 1
    assert result["after_drain"]["non_terminal_runs"] == 0, result["after_drain"]
    assert result["after_drain"]["failed_runs"] == 0, result["after_drain"]
    assert result["ledger"]["rows"] == result["ledger"]["model_requests"], result["ledger"]
    # The driver must work; the latency verdict at smoke scale is informational (5 samples are too noisy to gate).
    assert result["chat_first_token"]["baseline"]["n"] == drv.PROFILES["smoke"].baseline_chat_samples
