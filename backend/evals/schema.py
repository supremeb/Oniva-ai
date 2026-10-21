"""Data model for eval tasks, trials and traces (no I/O).

U6/U7 extend `GRADER_TYPES` and add result types additively.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

TASK_KINDS = ("deep_work", "chat", "long_session", "repeat", "memory", "redteam")
TASK_TAGS = ("smoke", "regression", "long_session", "repeat", "memory", "redteam")
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


@dataclass(frozen=True)
class TrialResult:
    trial: Trial
    grader_results: tuple[GraderResult, ...] = ()
    trace: Trace | None = None
    error: str | None = None
    latency_s: float = 0.0

    @property
    def passed(self) -> bool:
        """A trial passes when no error occurred and every gating grader passed."""
        if self.error is not None:
            return False
        return all(r.passed for r in self.grader_results if r.gating)

    @property
    def cost_usd(self) -> float:
        if self.trace is None:
            return 0.0
        return sum(s.usage.cost_usd for s in self.trace.walk())
