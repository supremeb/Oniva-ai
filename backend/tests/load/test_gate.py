"""U36: the pure gate and percentile code against synthetic numbers (runs in the default suite and CI)."""
import math

import pytest

from tests.load import gate as gate_mod
from tests.load.gate import percentile

GOOD = dict(failed_runs=0, chat_errors=0, loaded_samples=60, min_loaded_samples=60,
            http_started=5, http_done=5, expected_http_calls=5)


def gate(*args, **overrides):
    return gate_mod.evaluate_gate(*args, **{**GOOD, **overrides})


SAMPLE = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_percentile_matches_known_values_on_a_fixed_sample():
    assert percentile(SAMPLE, 0) == 10
    assert percentile(SAMPLE, 50) == pytest.approx(55.0)
    assert percentile(SAMPLE, 95) == pytest.approx(95.5)
    assert percentile(SAMPLE, 99) == pytest.approx(99.1)
    assert percentile(SAMPLE, 100) == 100
    assert percentile(reversed(SAMPLE), 50) == pytest.approx(55.0)  # order independent
    assert percentile([7], 99) == 7
    assert math.isnan(percentile([], 50))
    with pytest.raises(ValueError):
        percentile(SAMPLE, 101)


def test_gate_passes_at_plus_19_percent_and_fails_at_plus_21():
    ok = gate(100.0, 119.0, 0, 50, 50)
    assert ok.passed and ok.reasons == [] and ok.chat_p95_regression == pytest.approx(0.19)
    bad = gate(100.0, 121.0, 0, 50, 50)
    assert not bad.passed and "regressed" in bad.reasons[0]


def test_gate_boundary_and_improvement():
    assert gate(100.0, 120.0, 0, 1, 1).passed  # exactly +20% is allowed
    assert gate(100.0, 80.0, 0, 1, 1).passed


def test_gate_fails_on_any_non_terminal_run():
    r = gate(100.0, 100.0, 1, 50, 50)
    assert not r.passed and any("not terminal" in x for x in r.reasons)


def test_gate_fails_when_ledger_rows_differ_from_model_requests():
    for rows in (49, 51):
        r = gate(100.0, 100.0, 0, rows, 50)
        assert not r.passed and any("ledger rows" in x for x in r.reasons)


def test_gate_reports_every_failure_and_rejects_missing_numbers():
    r = gate(100.0, 200.0, 3, 1, 2)
    assert not r.passed and len(r.reasons) == 3
    assert not gate(0.0, 10.0, 0, 1, 1).passed
    assert not gate(100.0, math.nan, 0, 1, 1).passed


def test_gate_fails_when_every_run_failed():
    r = gate(100.0, 100.0, 0, 50, 50, failed_runs=205)
    assert not r.passed and any("failed" in x for x in r.reasons)


def test_gate_fails_on_chat_errors_or_timeouts():
    r = gate(100.0, 100.0, 0, 50, 50, chat_errors=1)
    assert not r.passed and any("errored or timed out" in x for x in r.reasons)


def test_gate_fails_on_http_count_mismatch():
    for started, done in ((4, 5), (5, 4), (6, 6), (0, 0)):
        r = gate(100.0, 100.0, 0, 50, 50, http_started=started, http_done=done)
        assert not r.passed and any("http calls" in x for x in r.reasons)


def test_gate_fails_on_too_few_loaded_samples():
    r = gate(100.0, 100.0, 0, 50, 50, loaded_samples=59)
    assert not r.passed and any("loaded chat sample" in x for x in r.reasons)
    assert gate(100.0, 100.0, 0, 50, 50, loaded_samples=60).passed


def test_profile_overrides_and_outputs(tmp_path, monkeypatch):
    from tests.load import automation_load as drv

    monkeypatch.setattr(drv, "format_report", lambda r: f"summary {r['profile']}")
    base = drv.PROFILES["smoke"]
    assert drv.profile_with_overrides(base) == base
    custom = drv.profile_with_overrides(base, workflow_runs=2, fleet_runs=None, chat_interval_s=0.1)
    assert (custom.workflow_runs, custom.fleet_runs, custom.chat_interval_s) == (2, base.fleet_runs, 0.1)
    assert custom.name == "smoke+custom"
    result = {"profile": "p", "gate": {"passed": False, "reasons": ["x"]}}
    drv.write_outputs(result, str(tmp_path / "r.json"), str(tmp_path / "r.md"))
    import json
    assert json.loads((tmp_path / "r.json").read_text())["gate"]["passed"] is False
    assert (tmp_path / "r.md").read_text() == "summary p\n"
    drv.write_outputs(result)  # no paths: nothing written


# --- the opt-in production-like backend (ONIVA_E2E_BACKEND_DB / ONIVA_E2E_BACKEND_WORKERS) -----------------


def test_backend_mode_defaults_to_the_single_sqlite_process(monkeypatch):
    from tests.e2e.harness import backend_mode

    monkeypatch.delenv("ONIVA_E2E_BACKEND_DB", raising=False)
    monkeypatch.delenv("ONIVA_E2E_BACKEND_WORKERS", raising=False)
    assert backend_mode() == (False, 1)


def test_backend_mode_reads_postgres_and_workers(monkeypatch):
    from tests.e2e.harness import backend_mode

    monkeypatch.setenv("ONIVA_E2E_BACKEND_DB", "postgres")
    monkeypatch.setenv("ONIVA_E2E_BACKEND_WORKERS", "4")
    assert backend_mode() == (True, 4)


@pytest.mark.parametrize("db,workers", [("mysql", "1"), ("sqlite", "3"), ("postgres", "0")])
def test_backend_mode_rejects_nonsense(monkeypatch, db, workers):
    from tests.e2e.harness import backend_mode

    monkeypatch.setenv("ONIVA_E2E_BACKEND_DB", db)
    monkeypatch.setenv("ONIVA_E2E_BACKEND_WORKERS", workers)
    with pytest.raises(ValueError):
        backend_mode()


def test_internal_pool_is_off_by_default(monkeypatch):
    from tests.e2e.harness import internal_pool_workers

    monkeypatch.delenv("ONIVA_E2E_BACKEND_INTERNAL_WORKERS", raising=False)
    assert internal_pool_workers(shards=1, pg=False) == 0


def test_internal_pool_reads_workers(monkeypatch):
    from tests.e2e.harness import internal_pool_workers

    monkeypatch.setenv("ONIVA_E2E_BACKEND_INTERNAL_WORKERS", "2")
    assert internal_pool_workers(shards=2, pg=True) == 2


@pytest.mark.parametrize("n,shards,pg", [("-1", 2, True), ("2", 1, True), ("2", 2, False)])
def test_internal_pool_rejects_nonsense(monkeypatch, n, shards, pg):
    from tests.e2e.harness import internal_pool_workers

    monkeypatch.setenv("ONIVA_E2E_BACKEND_INTERNAL_WORKERS", n)
    with pytest.raises(ValueError):
        internal_pool_workers(shards=shards, pg=pg)


def test_info_ready_waits_for_every_worker(tmp_path):
    from tests.e2e.harness import _info_ready

    info = tmp_path / "info.json"
    assert not _info_ready(info, 0)
    info.write_text("{}")
    assert _info_ready(info, 0)  # SQLite mode: the file alone
    assert not _info_ready(info, 2)
    (tmp_path / "info.json.101").write_text("ready")
    assert not _info_ready(info, 2)
    (tmp_path / "info.json.102").write_text("ready")
    assert _info_ready(info, 2)


# --- the chat probe in its own process (tests/load/probe.py) -------------------------------------------------


def _fake_measure(arg):
    """Runs in the probe process: returns a measure that yields 1.0, 2.0, ... (or raises/dies after a count)."""
    import os
    import time

    state = {"n": 0}

    def measure() -> float:
        state["n"] += 1
        if arg.get("fail_after") is not None and state["n"] > arg["fail_after"]:
            raise RuntimeError("boom")
        if arg.get("die_after") is not None and state["n"] > arg["die_after"]:
            os._exit(3)
        time.sleep(arg.get("sleep", 0))
        return float(os.getpid()) if arg.get("pid") else float(state["n"])

    return measure


def test_probe_runs_in_another_process_and_returns_baseline_samples():
    import os

    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {"pid": True}) as p:
        got = p.baseline(3)
    assert len(got) == 3 and got[0] == got[1] == got[2] != float(os.getpid())  # one child, not this process


def test_probe_baseline_keeps_the_requested_sample_count_and_order():
    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {}) as p:
        assert p.baseline(5) == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_probe_loaded_phase_samples_on_the_interval_with_monotonic_starts():
    import time

    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {"sleep": 0.01}) as p:
        t0 = time.monotonic()
        p.start_load(0.05)
        time.sleep(0.5)
        r = p.stop_load()
        t1 = time.monotonic()
    assert r.errors == [] and r.submitted == len(r.samples) >= 5
    starts = [s for s, _ in r.samples]
    assert starts == sorted(starts) and t0 <= starts[0] and starts[-1] <= t1  # the parent's clock bounds the child's
    assert all(b - a >= 0.04 for a, b in zip(starts, starts[1:]))  # the schedule: one turn per interval
    assert [ms for _, ms in r.samples] == [float(i) for i in range(1, len(r.samples) + 1)]


def test_probe_loaded_errors_are_collected_not_raised():
    import time

    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {"fail_after": 2}) as p:
        p.start_load(0.01)
        time.sleep(0.3)
        r = p.stop_load()
    assert len(r.samples) == 2 and r.errors and "boom" in r.errors[0] and r.submitted == len(r.samples) + len(r.errors)


def test_probe_baseline_failure_is_raised_in_the_parent():
    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {"fail_after": 1}) as p:
        with pytest.raises(RuntimeError, match="boom"):
            p.baseline(3)


def test_probe_child_death_is_reported_not_hung():
    from tests.load.probe import ChatProbe

    with ChatProbe(_fake_measure, {"die_after": 0}, reply_timeout=30) as p:
        with pytest.raises(RuntimeError, match="died|closed"):
            p.baseline(1)


def test_probe_close_stops_the_child():
    from tests.load.probe import ChatProbe

    p = ChatProbe(_fake_measure, {})
    proc = p._proc
    p.close()
    assert not proc.is_alive()


def test_driver_hands_the_probe_plain_picklable_data():
    import pickle

    from tests.load import automation_load as drv

    pickle.dumps((drv.make_chat_measure, {"backend_url": "http://x", "project": "p"}))
