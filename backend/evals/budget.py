"""The `EVAL_MAX_USD` meter (R29): a hard cap on one invocation's spend.

The runner records each finished trial's cost (from the usage documents) and asks `can_start` before the next one.
The next trial is assumed to cost as much as the most expensive one so far, or the task's own `budget.max_usd` if it
is larger, so the cap is a ceiling the run is stopped short of, not one it overshoots by a whole trial.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ENV_VAR = "EVAL_MAX_USD"


class BudgetConfigError(Exception):
    """`EVAL_MAX_USD` is missing or invalid where a cap is mandatory (live mode)."""


class BudgetMeter:
    def __init__(self, max_usd: float | None) -> None:
        if max_usd is not None and max_usd < 0:
            raise BudgetConfigError(f"{ENV_VAR} must not be negative")
        self.max_usd = max_usd
        self.spent = 0.0
        self.largest_trial = 0.0
        self.trials = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, required: bool = False) -> BudgetMeter:
        """Read the cap. `required` (live mode) refuses to run without a positive one."""
        raw = (os.environ if env is None else env).get(ENV_VAR, "").strip()
        if not raw:
            if required:
                raise BudgetConfigError(f"live mode needs a spend cap: set {ENV_VAR} (USD) before running")
            return cls(None)
        try:
            cap = float(raw)
        except ValueError:
            raise BudgetConfigError(f"{ENV_VAR} must be a number of USD, got {raw!r}") from None
        if required and cap <= 0:
            raise BudgetConfigError(f"{ENV_VAR} must be greater than 0 in live mode")
        return cls(cap)

    @property
    def remaining(self) -> float | None:
        return None if self.max_usd is None else max(0.0, self.max_usd - self.spent)

    def record(self, cost_usd: float) -> None:
        self.spent += max(0.0, cost_usd)
        self.largest_trial = max(self.largest_trial, cost_usd)
        self.trials += 1

    def charge(self, cost_usd: float) -> None:
        """Spend that is not a trial (a judge call): counts toward the cap, not toward the trial statistics."""
        self.spent += max(0.0, cost_usd)

    def can_spend(self, estimate_usd: float = 0.0) -> bool:
        """Whether a small non-trial charge (a judge call) fits; an exhausted cap refuses everything."""
        if self.max_usd is None:
            return True
        return self.spent < self.max_usd and self.spent + max(0.0, estimate_usd) <= self.max_usd

    def can_start(self, task_max_usd: float | None = None) -> bool:
        """Whether one more trial fits under the cap (an exhausted cap stops everything)."""
        if self.max_usd is None:
            return True
        expected = max(self.largest_trial, task_max_usd or 0.0)
        return self.spent + expected <= self.max_usd and self.spent < self.max_usd
