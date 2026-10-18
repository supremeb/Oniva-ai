"""U36: the pure gate and percentile code against synthetic numbers (runs in the default suite and CI)."""
import math

import pytest

from tests.load.gate import evaluate_gate, percentile

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
    ok = evaluate_gate(100.0, 119.0, 0, 50, 50)
    assert ok.passed and ok.reasons == [] and ok.chat_p95_regression == pytest.approx(0.19)
    bad = evaluate_gate(100.0, 121.0, 0, 50, 50)
    assert not bad.passed and "regressed" in bad.reasons[0]


def test_gate_boundary_and_improvement():
    assert evaluate_gate(100.0, 120.0, 0, 1, 1).passed  # exactly +20% is allowed
    assert evaluate_gate(100.0, 80.0, 0, 1, 1).passed


def test_gate_fails_on_any_non_terminal_run():
    r = evaluate_gate(100.0, 100.0, 1, 50, 50)
    assert not r.passed and any("not terminal" in x for x in r.reasons)


def test_gate_fails_when_ledger_rows_differ_from_model_requests():
    for rows in (49, 51):
        r = evaluate_gate(100.0, 100.0, 0, rows, 50)
        assert not r.passed and any("ledger rows" in x for x in r.reasons)


def test_gate_reports_every_failure_and_rejects_missing_numbers():
    r = evaluate_gate(100.0, 200.0, 3, 1, 2)
    assert not r.passed and len(r.reasons) == 3
    assert not evaluate_gate(0.0, 10.0, 0, 1, 1).passed
    assert not evaluate_gate(100.0, math.nan, 0, 1, 1).passed
