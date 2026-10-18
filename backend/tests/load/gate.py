"""U36 load gate: pure percentile and gate evaluation (no I/O, runs in CI against synthetic numbers).

Percentile method: linear interpolation between closest ranks (the "R-7" definition, numpy's default).
For a sorted sample ``x[0..n-1]`` and ``q`` in [0, 100], the rank is ``r = (n - 1) * q / 100`` and the value is
``x[floor(r)] + (r - floor(r)) * (x[floor(r) + 1] - x[floor(r)])``. So p50 of 1..10 is 5.5 and p95 is 9.55.

Gate (plan 003 U36): chat first-token p95 under automation load may not regress more than ``max_regression``
(20%) against the no-load baseline, no run may be left non-terminal after the drain, and the usage ledger rows
must equal the model requests made. A gate that can pass on nothing is no gate, so it also fails on any failed run,
on any chat turn that errored or timed out (such a turn is not dropped from the sample: it fails the gate), on fewer
than ``min_loaded_samples`` loaded chat samples, and when the counting HTTP target saw other than one started and
one finished call per workflow run.
"""
import math
from dataclasses import dataclass, field
from typing import Iterable


def percentile(values: Iterable[float], q: float) -> float:
    """The ``q``-th percentile (0..100) of ``values`` by linear interpolation; ``nan`` for an empty sample."""
    if not 0 <= q <= 100:
        raise ValueError(f"q must be within 0..100, got {q}")
    xs = sorted(values)
    if not xs:
        return math.nan
    rank = (len(xs) - 1) * q / 100.0
    low = math.floor(rank)
    high = min(low + 1, len(xs) - 1)
    return xs[low] + (rank - low) * (xs[high] - xs[low])


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    chat_p95_regression: float | None = None  # (loaded - baseline) / baseline

    def as_dict(self) -> dict:
        return {"passed": self.passed, "reasons": list(self.reasons), "chat_p95_regression": self.chat_p95_regression}


def evaluate_gate(baseline_chat_p95_ms: float, loaded_chat_p95_ms: float, non_terminal_runs: int,
                  ledger_rows: int, model_requests: int, *, failed_runs: int, chat_errors: int,
                  loaded_samples: int, min_loaded_samples: int, http_started: int, http_done: int,
                  expected_http_calls: int, max_regression: float = 0.20) -> GateResult:
    """Pass only when every condition holds; every failed condition adds a reason."""
    reasons: list[str] = []
    regression: float | None = None
    if not (baseline_chat_p95_ms and baseline_chat_p95_ms > 0) or math.isnan(loaded_chat_p95_ms):
        reasons.append("chat p95 baseline or loaded value is missing or not positive")
    else:
        regression = (loaded_chat_p95_ms - baseline_chat_p95_ms) / baseline_chat_p95_ms
        if regression > max_regression + 1e-9:
            reasons.append(f"chat first-token p95 regressed {regression:.1%} (limit {max_regression:.0%}): "
                           f"{baseline_chat_p95_ms:.1f} ms -> {loaded_chat_p95_ms:.1f} ms")
    if non_terminal_runs != 0:
        reasons.append(f"{non_terminal_runs} run(s) not terminal after the drain")
    if failed_runs != 0:
        reasons.append(f"{failed_runs} run(s) ended failed or cancelled")
    if chat_errors != 0:
        reasons.append(f"{chat_errors} chat turn(s) errored or timed out under load")
    if loaded_samples < min_loaded_samples:
        reasons.append(f"only {loaded_samples} loaded chat sample(s), need at least {min_loaded_samples}")
    if http_started != expected_http_calls or http_done != expected_http_calls:
        reasons.append(f"http calls started/done ({http_started}/{http_done}) != expected ({expected_http_calls})")
    if ledger_rows != model_requests:
        reasons.append(f"ledger rows ({ledger_rows}) != model requests ({model_requests})")
    return GateResult(passed=not reasons, reasons=reasons, chat_p95_regression=regression)
