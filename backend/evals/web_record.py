"""Record `web_search` / `browse_url` fixtures for replay (`python -m evals record-web`).

Each request is keyed by `replay.request_key` and stored with `replay.write_fixture` in `evals/fixtures/web/`, the
format `replay.install("replay", ...)` serves. This is the only eval path that touches the network, so:

- an existing fixture is never overwritten unless `force` is set, and the whole batch is checked before the first
  call: a refused batch makes no request at all;
- real calls are capped by `EVAL_RECORD_MAX_CALLS` (default 50), checked before the first call too;
- a result that is an error is not stored (a broken fixture would replay as a broken answer).

Requests come from `--search QUERY`, `--url URL` or a JSONL file of `{"tool": ..., "args": {...}}` objects.
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .replay import DEFAULT_RECORD_MAX_CALLS, TOOLS, ReplayError, normalize, read_fixture, request_key, write_fixture

DEFAULT_DIR = Path(__file__).resolve().parent / "fixtures" / "web"

# `call(tool, args) -> result`; the real one runs the registered skill handler.
WebCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


class RecordError(Exception):
    """The batch is refused before any request is made (bad request, would overwrite, over the call cap)."""


@dataclass(frozen=True)
class Request:
    tool: str
    args: dict[str, Any]

    @property
    def key(self) -> str:
        return request_key(self.tool, self.args)


@dataclass
class RecordReport:
    recorded: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # key -> error

    @property
    def ok(self) -> bool:
        return not self.failed


def parse_requests(lines: Sequence[str]) -> list[Request]:
    out: list[Request] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            tool, args = row["tool"], row["args"]
            if tool not in TOOLS or not isinstance(args, dict):
                raise ValueError(f"tool must be one of {TOOLS} and args an object")
            normalize(tool, args)
        except (ValueError, KeyError, TypeError, ReplayError) as e:
            raise RecordError(f"request line {n}: {e}") from None
        out.append(Request(tool, args))
    return out


def dedupe(requests: Sequence[Request]) -> list[Request]:
    seen: dict[str, Request] = {}
    for r in requests:
        seen.setdefault(r.key, r)
    return list(seen.values())


def plan(requests: Sequence[Request], directory: Path, *, force: bool, max_calls: int) -> list[Request]:
    """The requests to make, or `RecordError`. Nothing is called here."""
    todo = dedupe(requests)
    if len(todo) > max_calls:
        raise RecordError(f"{len(todo)} requests exceed EVAL_RECORD_MAX_CALLS ({max_calls})")
    existing = [r.key for r in todo if read_fixture(directory, r.key) is not None]
    if existing and not force:
        raise RecordError(
            f"{len(existing)} fixture(s) already exist ({', '.join(k[:12] for k in existing)}); pass --force to overwrite"
        )
    return todo


async def record(
    requests: Sequence[Request], directory: Path | str, call: WebCall, *, force: bool = False, max_calls: int | None = None
) -> RecordReport:
    directory = Path(directory)
    if max_calls is None:
        max_calls = int(os.environ.get("EVAL_RECORD_MAX_CALLS", DEFAULT_RECORD_MAX_CALLS))
    todo = plan(requests, directory, force=force, max_calls=max_calls)
    report = RecordReport()
    for r in todo:
        replacing = read_fixture(directory, r.key) is not None
        try:
            result = await call(r.tool, normalize(r.tool, r.args))
        except Exception as e:  # noqa: BLE001 - one bad request must not lose the others
            report.failed[r.key] = f"{type(e).__name__}: {e}"
            continue
        if isinstance(result, dict) and "error" in result:
            report.failed[r.key] = f"the tool returned an error: {result['error']}"
            continue
        write_fixture(directory, r.key, r.tool, r.args, result)
        (report.overwritten if replacing else report.recorded).append(r.key)
    return report


def registry_call() -> WebCall:
    """Run the real skill handlers (network, provider keys from the environment)."""
    from app.agents.skills.context import SkillContext
    from app.agents.skills.registry import _registry
    from app.skills.browse_url import tool as _browse  # noqa: F401 - registers the skill
    from app.skills.web_search import tool as _search  # noqa: F401

    ctx = SkillContext(user_id="eval-recorder")

    async def call(tool: str, args: dict[str, Any]) -> Any:
        skill = _registry.get(tool)
        if skill is None:
            raise ReplayError(f"the {tool} skill is not registered")
        out = skill.handler(ctx, **args)
        return await out if inspect.isawaitable(out) else out

    return call


def requests_from_args(searches: Sequence[str], urls: Sequence[str], files: Sequence[str]) -> list[Request]:
    out = [Request("web_search", {"query": q}) for q in searches] + [Request("browse_url", {"url": u}) for u in urls]
    for f in files:
        try:
            out += parse_requests(Path(f).read_text(encoding="utf-8").splitlines())
        except OSError as e:
            raise RecordError(f"cannot read {f}: {e}") from None
    return out
