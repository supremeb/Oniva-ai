"""Fixture-backed ``web_search`` / ``browse_url`` for eval trials (plan 2026-10-21-001, U4, KTD4).

Wraps the two handlers in the backend skill registry (the seam ``tests/e2e/dw_fakes.py`` uses), so
``TOOL_SPECS`` are untouched and taint / ``untrusted_output`` behave as in production.

- ``replay``: a request is served from ``<dir>/<key>.json``. A miss returns
  ``{"error": "fixture_miss", "key": ...}``, appends the key to the miss log, and never reaches the
  network or the real handler.
- ``record``: the real handler runs (network on), the result is stored, and the number of real calls is
  capped by ``EVAL_RECORD_MAX_CALLS``.

Off by default: nothing is wrapped unless ``install`` / ``install_from_env`` is called with an explicit
mode. ``install`` raises when the environment is production.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.tool_policy import _canonical

FIXTURE_MISS = "fixture_miss"
MODES = ("replay", "record")
TOOLS = ("web_search", "browse_url")
DEFAULT_RECORD_MAX_CALLS = 50
_PRODUCTION = ("production", "prod")


class ReplayError(Exception):
    """Misuse of the replay layer (bad mode, production, record cap, unknown tool)."""


def normalize(tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical args for keying: trimmed, lowercased query, defaults filled in."""
    a = dict(args)
    if tool == "web_search":
        a["query"] = " ".join(str(a.get("query", "")).lower().split())
        a["num_results"] = int(a.get("num_results", 5))
    elif tool == "browse_url":
        a["url"] = str(a.get("url", "")).strip()
        a["extract_text"] = bool(a.get("extract_text", True))
    else:
        raise ReplayError(f"replay does not cover tool {tool!r}")
    return a


def request_key(tool: str, args: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({"tool": tool, "args": normalize(tool, args)}).encode()).hexdigest()


def write_fixture(root: Path | str, key: str, tool: str, args: Mapping[str, Any], result: Any,
                  *, name: str | None = None) -> Path:
    """Store a fixture and register it in ``index.json``. Results are stored as given.

    The file is ``<key>.json``, or ``<name>.json`` when ``name`` is given (U34: the memory red team's hand-named
    ``poison_*.json``); the index then records the file name so replay can find it.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    norm = normalize(tool, args)
    path = root / f"{name or key}.json"
    _atomic_write(path, json.dumps({"key": key, "tool": tool, "args": norm, "result": result},
                                   ensure_ascii=False, indent=2))
    index_path = root / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[key] = {"tool": tool, "args": norm, **({"file": path.name} if name else {})}
    _atomic_write(index_path, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return path


def fixture_path(root: Path | str, key: str) -> Path | None:
    """The file holding fixture ``key``: ``<key>.json``, else the file the index names for it."""
    root = Path(root)
    path = root / f"{key}.json"
    if path.is_file():
        return path
    index_path = root / "index.json"
    if index_path.is_file():
        name = (json.loads(index_path.read_text()).get(key) or {}).get("file")
        if isinstance(name, str) and name.endswith(".json") and "/" not in name and name != "index.json":
            alias = root / name
            return alias if alias.is_file() else None
    return None


def read_fixture(root: Path | str, key: str) -> Any | None:
    """The stored result, or ``None`` on a miss."""
    path = fixture_path(root, key)
    return None if path is None else json.loads(path.read_text())["result"]


def _is_error(result: Any) -> bool:
    return isinstance(result, dict) and "error" in result


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _assert_not_production() -> None:
    env = os.environ.get("ENVIRONMENT")
    if env is None:
        from app.config import get_settings

        env = get_settings().ENVIRONMENT
    if str(env).strip().lower() in _PRODUCTION:
        raise ReplayError("eval replay refuses to install when ENVIRONMENT is production")


def install(mode: str, directory: Path | str, *, miss_log: Path | str | None = None,
            max_calls: int | None = None) -> None:
    """Wrap ``web_search`` and ``browse_url`` in the skill registry (call after the skills are loaded)."""
    _assert_not_production()
    if mode not in MODES:
        raise ReplayError(f"unknown replay mode {mode!r}; expected one of {MODES}")
    from app.agents.skills.registry import Skill, _registry

    root = Path(directory)
    miss_path = Path(miss_log) if miss_log else None
    if max_calls is None:
        max_calls = int(os.environ.get("EVAL_RECORD_MAX_CALLS", DEFAULT_RECORD_MAX_CALLS))
    real = {t: _registry[t].handler for t in TOOLS if t in _registry}
    recorded = 0

    async def serve(tool: str, ctx: Any, args: dict[str, Any]) -> Any:
        nonlocal recorded
        key = request_key(tool, args)
        if mode == "replay":
            hit = read_fixture(root, key)
            if hit is not None:
                return copy.deepcopy(hit)
            if miss_path is not None:
                miss_path.parent.mkdir(parents=True, exist_ok=True)
                with miss_path.open("a") as f:
                    f.write(json.dumps({"tool": tool, "key": key, "args": normalize(tool, args)}) + "\n")
            return {"error": FIXTURE_MISS, "key": key}
        if tool not in real:
            raise ReplayError(f"record mode needs the real {tool} handler registered")
        if recorded >= max_calls:
            raise ReplayError(f"EVAL_RECORD_MAX_CALLS ({max_calls}) reached")
        recorded += 1
        return await _call(real[tool], ctx, args)

    async def web_search(ctx, query: str, num_results: int = 5):
        """Search the web.

        Args:
            query: Search query string
            num_results: Number of results to return (default: 5)
        """
        args = {"query": query, "num_results": num_results}
        result = await serve("web_search", ctx, args)
        if mode == "record" and not _is_error(result):
            write_fixture(root, request_key("web_search", args), "web_search", args, result)
        return result

    async def browse_url(ctx, url: str, extract_text: bool = True):
        """Fetch webpage content.

        Args:
            url: URL to fetch
            extract_text: If True, extract text content; if False, return raw HTML
        """
        args = {"url": url, "extract_text": extract_text}
        result = await serve("browse_url", ctx, args)
        if mode == "record" and not _is_error(result):
            write_fixture(root, request_key("browse_url", args), "browse_url", args, result)
        return result

    async def _call(handler, ctx: Any, args: dict[str, Any]):
        import inspect

        out = handler(ctx, **args)
        return await out if inspect.isawaitable(out) else out

    for name, fn in (("web_search", web_search), ("browse_url", browse_url)):
        old = _registry.get(name)
        _registry[name] = Skill(
            name=name,
            description=old.description if old else fn.__doc__.splitlines()[0],
            category=old.category if old else "web",
            handler=fn,
        )


def install_from_env(env: Mapping[str, str] | None = None) -> bool:
    """Install when ``EVAL_REPLAY_MODE`` and ``EVAL_REPLAY_DIR`` are both set; otherwise do nothing."""
    env = os.environ if env is None else env
    mode, directory = env.get("EVAL_REPLAY_MODE"), env.get("EVAL_REPLAY_DIR")
    if not mode or not directory:
        return False
    install(mode, directory, miss_log=env.get("EVAL_REPLAY_MISS_LOG") or None)
    return True


def fixture_urls(root: Path | str) -> list[str]:
    """Every URL a fixture in ``root`` carries (search result URLs and fetched page URLs), sorted and unique.

    What the `url_resolves` grader checks learnings' source URLs against.
    """
    root = Path(root)
    urls: set[str] = set()
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        result = json.loads(path.read_text()).get("result")
        if not isinstance(result, dict):
            continue
        if isinstance(result.get("url"), str):
            urls.add(result["url"])
        for item in result.get("results") or []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                urls.add(item["url"])
    return sorted(urls)
