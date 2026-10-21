"""The e2e stack, configured per eval mode.

Wraps `tests.e2e.harness.stack` (Postgres for the engine, the backend of `tests/e2e/backend_app.py`, one worker):

- `fake`: the worker is `npm run worker:test` with a rule-scripted model; the script file is rewritten per task
  (`write_script`). No provider key is used and nothing is spent.
- `live`: the worker is the production one (`npm run worker`, `ENGINE_POLICY=backend`) and the backend's tenant has
  one OpenRouter provider whose key comes from `EVAL_OPENROUTER_API_KEY`. The key goes only to the backend
  process (stored encrypted); the worker gets it blanked, and nothing logs it.

Web tools are served from the fixture directory (`evals.replay`), installed when the backend boots, so there is one
stack per web mode.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.e2e.harness import SERVICE_TOKEN, World, stack

from .engine_api import EngineApi

MODES = ("fake", "live")
KEY_VAR = "EVAL_OPENROUTER_API_KEY"
DEFAULT_LIVE_MODEL = "deepseek/deepseek-v4.1-flash"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "web"
WEB_TO_REPLAY = {"replay": "replay", "record": "record", "off": "replay"}


class LiveModeError(Exception):
    """Live mode cannot start (no key). Raised before anything is booted or spent."""


@dataclass
class EvalWorld:
    world: World
    mode: str
    web: str
    replay_dir: Path
    miss_log: Path
    script_path: Path

    def engine_api(self) -> EngineApi:
        return EngineApi(
            self.world.engine_url,
            SERVICE_TOKEN,
            tenant_id=str(self.world.tenant),
            project_id=str(self.world.project),
        )

    def write_script(self, script: dict[str, Any]) -> None:
        """Replace the scripted model's rules (the worker re-reads the file on every request). Fake mode only."""
        if self.mode != "fake":
            raise RuntimeError("only fake mode has a scripted model")
        tmp = self.script_path.with_name(self.script_path.name + ".new")
        tmp.write_text(json.dumps(script))
        tmp.replace(self.script_path)

    def misses_since(self, offset: int) -> tuple[list[str], int]:
        """Replay-miss keys logged after line `offset`, and the new offset."""
        lines = self.miss_log.read_text().splitlines() if self.miss_log.exists() else []
        return [json.loads(line).get("key", "") for line in lines[offset:] if line.strip()], len(lines)


def require_live_key(env: Mapping[str, str] | None = None) -> None:
    """Fail fast, before anything boots, when live mode has no key. Checks presence only."""
    if not (os.environ if env is None else env).get(KEY_VAR, "").strip():
        raise LiveModeError(f"live mode needs {KEY_VAR} (a separate, credit-limited OpenRouter key); it is not set")


@contextlib.contextmanager
def eval_stack(
    mode: str, *, web: str = "replay", replay_dir: Path | None = None, backend_env: Mapping[str, str] | None = None
) -> Iterator[EvalWorld]:
    """Boot the stack for `mode` and `web` (`replay`, `record` or `off`) with one worker running.

    `backend_env` is added to the backend's environment (the context-tier arms set `ENGINE_CONTEXT_TIERS` here).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if web not in WEB_TO_REPLAY:
        raise ValueError(f"unknown web mode {web!r}; expected one of {tuple(WEB_TO_REPLAY)}")
    if mode == "live":
        require_live_key()
    with tempfile.TemporaryDirectory(prefix="oniva-eval-") as scratch:
        scratch_dir = Path(scratch)
        if web == "off":  # every web call misses
            directory = scratch_dir / "no-web"
            directory.mkdir()
        else:
            directory = Path(replay_dir) if replay_dir else FIXTURE_DIR
        miss_log = scratch_dir / "misses.jsonl"
        extra_env = {
            "EVAL_REPLAY_MODE": WEB_TO_REPLAY[web],
            "EVAL_REPLAY_DIR": str(directory),
            "EVAL_REPLAY_MISS_LOG": str(miss_log),
            **(backend_env or {}),
        }
        worker_command = None
        worker_env: dict[str, str] = {}
        if mode == "live":
            extra_env.update(
                {
                    "E2E_LIVE_PROVIDER": "openrouter",
                    KEY_VAR: os.environ[KEY_VAR],
                    "EVAL_LIVE_MODEL": os.environ.get("EVAL_LIVE_MODEL", DEFAULT_LIVE_MODEL),
                }
            )
            worker_command = ["npm", "run", "--silent", "worker"]
            worker_env = {KEY_VAR: "", "ENGINE_POLICY": "backend"}
        with stack(extra_env=extra_env, worker_command=worker_command, worker_env=worker_env) as world:
            world.start_worker(1)
            yield EvalWorld(
                world=world,
                mode=mode,
                web=web,
                replay_dir=directory,
                miss_log=miss_log,
                script_path=world.script,
            )
