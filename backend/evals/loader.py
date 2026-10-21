"""YAML to `Task`, validation and tag selection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from evals.schema import (
    GRADER_TYPES,
    TASK_KINDS,
    TASK_TAGS,
    WEB_MODES,
    Budget,
    Environment,
    GraderSpec,
    Task,
)


class TaskLoadError(ValueError):
    """A task file is invalid. The message names the file and the problem."""


def _fail(path: Path, msg: str) -> TaskLoadError:
    return TaskLoadError(f"{path}: {msg}")


def _mapping(path: Path, value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(path, f"'{name}' must be a mapping")
    return value


def _parse_graders(path: Path, raw: Any) -> tuple[GraderSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _fail(path, "'graders' must be a list")
    graders: list[GraderSpec] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _fail(path, "each grader must be a mapping")
        gid = item.get("id")
        gtype = item.get("type")
        if not gid or not isinstance(gid, str):
            raise _fail(path, "grader is missing 'id'")
        if gid in seen:
            raise _fail(path, f"duplicate grader id '{gid}'")
        seen.add(gid)
        if gtype not in GRADER_TYPES:
            raise _fail(path, f"grader '{gid}' has unknown type '{gtype}' (known: {', '.join(GRADER_TYPES)})")
        gating = item.get("gating", True)
        if not isinstance(gating, bool):
            raise _fail(path, f"grader '{gid}': 'gating' must be a boolean")
        graders.append(
            GraderSpec(id=gid, type=gtype, gating=gating, params=_mapping(path, item.get("params"), "params"))
        )
    return tuple(graders)


def load_task(path: Path | str) -> Task:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise _fail(path, f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise _fail(path, "top level must be a mapping")

    tid = data.get("id")
    if not tid or not isinstance(tid, str):
        raise _fail(path, "missing 'id'")
    kind = data.get("kind")
    if kind not in TASK_KINDS:
        raise _fail(path, f"task '{tid}': unknown kind '{kind}' (known: {', '.join(TASK_KINDS)})")

    tags_raw = data.get("tags") or []
    if not isinstance(tags_raw, list):
        raise _fail(path, f"task '{tid}': 'tags' must be a list")
    for tag in tags_raw:
        if tag not in TASK_TAGS:
            raise _fail(path, f"task '{tid}': unknown tag '{tag}' (known: {', '.join(TASK_TAGS)})")

    env_raw = _mapping(path, data.get("environment"), "environment")
    web = env_raw.get("web", "replay")
    if web not in WEB_MODES:
        raise _fail(path, f"task '{tid}': environment.web must be one of {', '.join(WEB_MODES)}, got '{web}'")
    environment = Environment(
        web=web,
        connectors=_mapping(path, env_raw.get("connectors"), "environment.connectors"),
        tenant_settings=_mapping(path, env_raw.get("tenant_settings"), "environment.tenant_settings"),
    )

    budget_raw = _mapping(path, data.get("budget"), "budget")
    budget = Budget(max_usd=budget_raw.get("max_usd"), max_rounds=budget_raw.get("max_rounds"))

    return Task(
        id=tid,
        kind=kind,
        tags=tuple(tags_raw),
        input=_mapping(path, data.get("input"), "input"),
        environment=environment,
        fake=_mapping(path, data.get("fake"), "fake"),
        graders=_parse_graders(path, data.get("graders")),
        budget=budget,
    )


def load_tasks(root: Path | str) -> list[Task]:
    """Load every `*.yaml` / `*.yml` under `root`, sorted by task id. Duplicate ids are an error."""
    root = Path(root)
    files = sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")])
    tasks: dict[str, Task] = {}
    origin: dict[str, Path] = {}
    for f in files:
        task = load_task(f)
        if task.id in tasks:
            raise TaskLoadError(f"duplicate task id '{task.id}' in {origin[task.id]} and {f}")
        tasks[task.id] = task
        origin[task.id] = f
    return sorted(tasks.values(), key=lambda t: t.id)


def select_by_tags(tasks: Iterable[Task], tags: Iterable[str]) -> list[Task]:
    """Tasks carrying at least one of `tags`, in input order. Empty `tags` selects everything."""
    wanted = set(tags)
    return [t for t in tasks if not wanted or wanted.intersection(t.tags)]
