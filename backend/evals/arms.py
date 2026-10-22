"""Context-tier arms (plan 2026-10-21-001, U17): one stack per `ENGINE_CONTEXT_TIERS` value.

The tier is read when a conversation's agent config is built, from the backend's environment (or a tenant flag), so an
arm is a backend process started with `ENGINE_CONTEXT_TIERS=<tier>`. A task selects its arm with
`environment.tenant_settings.context_tiers`; the runner groups tasks by that value and boots one stack per group.

`long_session` tasks are written arm-free and expanded here into one task per arm (`<id>@<tier>`), so a single
`run --suite long_session` measures every arm on the same tasks.

Fake mode shrinks the Tier 1 thresholds (the scripted model's declared usage is small), so clearing engages within a
short session. Live mode never overrides a threshold: the gate measures the production defaults.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .schema import Task

TIERS = ("off", "offload", "offload+clear", "full")
ARMS = ("off", "offload+clear", "full")
BASELINE_ARM = "off"
TIERS_KEY = "context_tiers"
ARM_SEP = "@"

# Fake mode only: the clear trigger is a fraction of the context window (default 0.5), and the scripted model's
# declared usage ramps to tens of thousands of tokens, not hundreds of thousands.
FAKE_CLEAR_ENV = {
    "ENGINE_CONTEXT_CLEAR_FRACTION": "0.02",
    "ENGINE_CONTEXT_KEEP_TOOL_RESULTS": "3",
    "ENGINE_CONTEXT_CLEAR_AT_LEAST_TOKENS": "3000",
}


def stack_env(tiers: str | None, mode: str) -> dict[str, str]:
    """Extra backend environment for an arm; empty when the task pins no tier."""
    if tiers is None:
        return {}
    if tiers not in TIERS:
        raise ValueError(f"unknown context tier {tiers!r}; expected one of {TIERS}")
    env = {"ENGINE_CONTEXT_TIERS": tiers}
    if mode == "fake" and tiers in ("offload+clear", "full"):
        env.update(FAKE_CLEAR_ENV)
    return env


def tiers_of(task: Task) -> str | None:
    value = task.environment.tenant_settings.get(TIERS_KEY)
    return None if value is None else str(value)


def with_tiers(task: Task, tiers: str, *, suffix: bool = False) -> Task:
    """`task` pinned to `tiers` (its id gets `@tiers` when `suffix`)."""
    if tiers not in TIERS:
        raise ValueError(f"unknown context tier {tiers!r}; expected one of {TIERS}")
    env = replace(task.environment, tenant_settings={**task.environment.tenant_settings, TIERS_KEY: tiers})
    return replace(task, id=f"{task.id}{ARM_SEP}{tiers}" if suffix else task.id, environment=env)


def arms_of(task: Task) -> tuple[str, ...]:
    arms = tuple(task.input.get("arms") or ARMS)
    bad = [a for a in arms if a not in TIERS]
    if bad:
        raise ValueError(f"task '{task.id}': unknown arm(s) {bad}; expected from {TIERS}")
    return arms


def expand_arms(tasks: Iterable[Task]) -> list[Task]:
    """Each unpinned `long_session` task becomes one task per tier arm, each unpinned `repeat` task one per memory arm."""
    out: list[Task] = []
    for t in tasks:
        if t.kind == "long_session" and tiers_of(t) is None:
            out.extend(with_tiers(t, arm, suffix=True) for arm in arms_of(t))
        else:
            out.append(t)
    return expand_memory_arms(out)


def split_arm(task_id: str) -> tuple[str, str | None]:
    """`('ls-x', 'full')` from `ls-x@full`; `(id, None)` for a task with no arm."""
    base, sep, arm = task_id.rpartition(ARM_SEP)
    return (base, arm) if sep and arm in TIERS else (task_id, None)


# --- project memory arms (plan 2026-10-21-001, U34) ---------------------------------------------------------------------
#
# A task pins its memory arm with `environment.tenant_settings.memory`; the runner groups tasks by it and boots one stack
# per group (`tests.e2e.harness.memory_env`). Arms:
#   control   memory off everywhere (no tenant flag, no project setting): the no-memory baseline of the lift criterion
#   manual    memory tools and injection on, curator off (the first rollout step)
#   propose   curator on, every result pending (second step)
#   auto      curator on, untainted results active (third step)
# `repeat` tasks are written arm-free and expanded into `<id>@propose` and `<id>@control`, as long sessions are into tier
# arms, so one `run --suite memory` measures the lift against its control.

MEMORY_KEY = "memory"
MEMORY_ARMS = ("control", "manual", "propose", "auto")
MEMORY_CONTROL = "control"
MEMORY_TREATMENT = "propose"
REPEAT_ARMS = (MEMORY_TREATMENT, MEMORY_CONTROL)
CURATOR_OF_ARM = {"control": "off", "manual": "off", "propose": "propose", "auto": "auto"}


def memory_of(task: Task) -> str | None:
    value = task.environment.tenant_settings.get(MEMORY_KEY)
    return None if value is None else str(value)


def memory_stack_env(arm: str | None) -> tuple[dict[str, str], dict[str, str]]:
    """(backend env, worker env) for a memory arm; both empty for no arm and for `control`."""
    if arm is None or arm == MEMORY_CONTROL:
        return {}, {}
    if arm not in MEMORY_ARMS:
        raise ValueError(f"unknown memory arm {arm!r}; expected one of {MEMORY_ARMS}")
    from tests.e2e.harness import memory_env

    return memory_env(CURATOR_OF_ARM[arm])


def with_memory(task: Task, arm: str, *, suffix: bool = False) -> Task:
    if arm not in MEMORY_ARMS:
        raise ValueError(f"unknown memory arm {arm!r}; expected one of {MEMORY_ARMS}")
    env = replace(task.environment, tenant_settings={**task.environment.tenant_settings, MEMORY_KEY: arm})
    return replace(task, id=f"{task.id}{ARM_SEP}{arm}" if suffix else task.id, environment=env)


def expand_memory_arms(tasks: Iterable[Task]) -> list[Task]:
    """Each unpinned `repeat` task becomes one task per memory arm (`REPEAT_ARMS`); every other task is returned as is."""
    out: list[Task] = []
    for t in tasks:
        if t.kind == "repeat" and memory_of(t) is None:
            out.extend(with_memory(t, arm, suffix=True) for arm in REPEAT_ARMS)
        else:
            out.append(t)
    return out


def split_memory_arm(task_id: str) -> tuple[str, str | None]:
    base, sep, arm = task_id.rpartition(ARM_SEP)
    return (base, arm) if sep and arm in MEMORY_ARMS else (task_id, None)
