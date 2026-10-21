"""Trial metrics (KTD5). Pure functions, no I/O."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

REGRESSION_FLOOR = 0.05
COST_REGRESSION_RATIO = 0.15


def _check(n: int, c: int, k: int) -> None:
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= c <= n:
        raise ValueError("c must satisfy 0 <= c <= n")
    if k <= 0:
        raise ValueError("k must be positive")
    if k > n:
        raise ValueError(f"k ({k}) must not exceed n ({n})")


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate that at least one of k sampled trials passes: 1 - C(n-c, k) / C(n, k)."""
    _check(n, c, k)
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate that all k sampled trials pass: C(c, k) / C(n, k)."""
    _check(n, c, k)
    return math.comb(c, k) / math.comb(n, k)


def standard_error(samples: Sequence[float]) -> float:
    """Standard error of the mean (sample standard deviation / sqrt(n)); 0 for fewer than 2 samples."""
    n = len(samples)
    if n < 2:
        return 0.0
    mean = sum(samples) / n
    var = sum((x - mean) ** 2 for x in samples) / (n - 1)
    return math.sqrt(var) / math.sqrt(n)


@dataclass(frozen=True)
class Comparison:
    baseline_rate: float
    current_rate: float
    pass_delta: float
    threshold: float
    pass_regressed: bool
    cost_regressed: bool

    @property
    def regressed(self) -> bool:
        return self.pass_regressed or self.cost_regressed


def compare(
    baseline: Sequence[float],
    current: Sequence[float],
    *,
    baseline_cost_per_pass: float | None = None,
    current_cost_per_pass: float | None = None,
) -> Comparison:
    """Paired verdict against a baseline.

    `baseline` and `current` are per-trial outcomes (1.0 pass, 0.0 fail). A pass regression is a drop
    larger than max(0.05, 2 x baseline standard error); a cost regression is cost per passing trial up
    by more than 15%.
    """
    if not baseline or not current:
        raise ValueError("baseline and current must be non-empty")
    base_rate = sum(baseline) / len(baseline)
    cur_rate = sum(current) / len(current)
    delta = cur_rate - base_rate
    threshold = max(REGRESSION_FLOOR, 2 * standard_error(baseline))
    cost_regressed = (
        baseline_cost_per_pass is not None
        and current_cost_per_pass is not None
        and baseline_cost_per_pass > 0
        and current_cost_per_pass > baseline_cost_per_pass * (1 + COST_REGRESSION_RATIO) + 1e-12
    )
    return Comparison(
        baseline_rate=base_rate,
        current_rate=cur_rate,
        pass_delta=delta,
        threshold=threshold,
        pass_regressed=-delta > threshold,
        cost_regressed=bool(cost_regressed),
    )
