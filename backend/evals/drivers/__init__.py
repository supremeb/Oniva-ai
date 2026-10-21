"""Drivers: run one trial of a task through the backend's browser routes and collect what graders need.

A driver talks only to what a user's browser talks to (`/api/v1/engine/conversations`, `/api/v1/deep-work`), so
the real backend routes, the `/internal` gateway and policy, and a real worker are all exercised. It returns a
`DriverOutput`: the root conversation id, the merged raw trace (`GradeContext.raw`), and the workspace files graders
read (`GradeContext.files`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from evals.engine_api import EngineApi
from evals.schema import Task
from evals.stack import EvalWorld

AUTH = {"Authorization": "Bearer e2e-user-jwt"}  # the e2e backend authenticates a fixed seeded user
POLL_S = 0.4


class DriverError(Exception):
    """The trial could not be driven (a route refused, the run never settled). `code` is machine readable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class TrialTimeout(DriverError):
    def __init__(self, what: str, timeout_s: float) -> None:
        super().__init__("timeout", f"{what} did not finish within {timeout_s:g}s")


@dataclass
class DriverContext:
    ew: EvalWorld
    task: Task
    trial_index: int
    timeout_s: float
    http: httpx.AsyncClient
    api: EngineApi
    deadline: float = 0.0
    phases: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.timeout_s

    @property
    def backend(self) -> str:
        return self.ew.world.backend_url

    @property
    def project_id(self) -> str:
        return str(self.ew.world.project)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def lap(self, name: str, started: float) -> None:
        self.phases[name] = round(self.phases.get(name, 0.0) + time.monotonic() - started, 3)


@dataclass
class DriverOutput:
    conversation_id: str
    raw: dict[str, Any] | None = None
    files: dict[str, Any] = field(default_factory=dict)
    phases: dict[str, float] = field(default_factory=dict)
    timed_out: bool = False
    note: str | None = None


async def drive(ctx: DriverContext) -> DriverOutput:
    """Run the task's driver. Kinds without a dedicated driver (U24/U34 add theirs) are chat-like."""
    from . import chat, deep_work, long_session

    if ctx.task.kind == "deep_work":
        return await deep_work.run(ctx)
    if ctx.task.kind == "long_session":
        return await long_session.run(ctx)
    return await chat.run(ctx)
