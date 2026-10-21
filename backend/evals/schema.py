"""Data model for eval tasks, trials and traces (no I/O).

U6/U7 extend `GRADER_TYPES` and add result types additively.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

TASK_KINDS = ("deep_work", "chat", "long_session", "repeat", "memory", "redteam")
TASK_TAGS = ("smoke", "regression", "long_session", "repeat", "memory", "redteam", "dw_subagents")
WEB_MODES = ("replay", "record", "off")
GRADER_TYPES = (
    "tool_order",
    "no_failed_workers",
    "citation_map",
    "url_resolves",
    "budget",
    "rounds",
    "cache_hit_ratio",
    "trajectory_match",
    "judge",
    # U6 (additive)
    "no_run_error",
    "no_compaction_failure",
    "no_limit_exceeded",
    "tool_error_rate",
    "learning_sources",
    # U17 (additive)
    "context_tokens",
    "probe_recall",
    "reread_after_clear",
    "no_verbatim_oversize",
    # U24 (additive): subagent contract
    "worker_failure_rate",
    "malformed_worker_answers",
    "cross_worker_url_overlap",
    "broken_citations",
    "uncited_sections",
    "steer_applied",
)
SPAN_KINDS = ("model", "tool", "subagent", "compaction")

TaskKind = Literal["deep_work", "chat", "long_session", "repeat", "memory", "redteam"]
WebMode = Literal["replay", "record", "off"]
SpanKind = Literal["model", "tool", "subagent", "compaction"]


@dataclass(frozen=True)
class GraderSpec:
    id: str
    type: str
    gating: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Environment:
    web: WebMode = "replay"
    connectors: dict[str, Any] = field(default_factory=dict)
    tenant_settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Budget:
    max_usd: float | None = None
    max_rounds: int | None = None


@dataclass(frozen=True)
class Task:
    id: str
    kind: TaskKind
    tags: tuple[str, ...] = ()
    input: dict[str, Any] = field(default_factory=dict)
    environment: Environment = field(default_factory=Environment)
    fake: dict[str, Any] = field(default_factory=dict)
    graders: tuple[GraderSpec, ...] = ()
    budget: Budget = field(default_factory=Budget)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Span:
    kind: SpanKind
    name: str
    status: str = "ok"
    args_hash: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    usage: Usage = field(default_factory=Usage)
    children: tuple[Span, ...] = ()


@dataclass(frozen=True)
class Trace:
    root: Span
    taint: bool = False
    usage_by_role: dict[str, Usage] = field(default_factory=dict)
    compactions: tuple[dict[str, Any], ...] = ()

    def walk(self) -> Iterator[Span]:
        """Depth-first, parents before children, in recorded order."""
        stack = [self.root]
        while stack:
            span = stack.pop()
            yield span
            stack.extend(reversed(span.children))


@dataclass(frozen=True)
class GraderResult:
    grader_id: str
    passed: bool
    gating: bool = True
    detail: str = ""


@dataclass(frozen=True)
class Trial:
    task_id: str
    index: int
    mode: Literal["fake", "live"] = "fake"


TRIAL_STATUSES = ("ok", "run_error", "timeout", "fixture_miss", "error", "skipped_budget")


@dataclass(frozen=True)
class TrialResult:
    """One trial's outcome.

    `status`: `ok` (the run finished), `run_error` (the engine ended the run with a `run_error`; `error_code` is the
    error kind), `timeout`, `fixture_miss` (a web call had no fixture), `error` (the driver failed), or
    `skipped_budget` (never run: the `EVAL_MAX_USD` cap would have been crossed). Only `ok` trials can pass; reports
    leave `skipped_budget` trials out of pass rates.
    """

    trial: Trial
    grader_results: tuple[GraderResult, ...] = ()
    trace: Trace | None = None
    error: str | None = None
    latency_s: float = 0.0
    status: str = "ok"
    error_code: str | None = None
    raw: dict[str, Any] | None = None
    phases: dict[str, float] = field(default_factory=dict)
    fixture_misses: tuple[str, ...] = ()
    ledger: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None  # per-trial context metrics (long sessions; `graders.context.trial_metrics`)

    @property
    def passed(self) -> bool:
        """A trial passes when it ended `ok` and every gating grader passed."""
        if self.error is not None or self.status != "ok":
            return False
        return all(r.passed for r in self.grader_results if r.gating)

    @property
    def cost_usd(self) -> float:
        """Whole-run cost, compaction requests included (the runner puts the backend ledger's dollars on the trace)."""
        if self.trace is None:
            return 0.0
        if self.trace.usage_by_role:
            return sum(u.cost_usd for u in self.trace.usage_by_role.values())
        return sum(s.usage.cost_usd for s in self.trace.walk())
