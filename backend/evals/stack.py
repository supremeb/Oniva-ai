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


ARMS = ("baseline", "v2")
ARM_VAR = "EVAL_ARM"
# Subagent contract v2 (U22). The fake arm leaves `fork` off: the scripted model keys on the last message, and a
# forked child would see the parent's history. Live runs use the production defaults (all four sub-switches).
V2_SWITCHES = {"fake": "typed_results,verifier,steer_worker", "live": "typed_results,verifier,steer_worker,fork"}


class LiveModeError(Exception):
    """Live mode cannot start (no key). Raised before anything is booted or spent."""


def resolve_arm(arm: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """`arm`, else `EVAL_ARM`, else `baseline`; anything but `baseline` or `v2` is an error."""
    chosen = (arm or (os.environ if env is None else env).get(ARM_VAR, "") or "baseline").strip()
    if chosen not in ARMS:
        raise ValueError(f"unknown arm {chosen!r}; expected one of {ARMS}")
    return chosen


def arm_env(arm: str, mode: str) -> tuple[dict[str, str], dict[str, str]]:
    """(backend env, worker env) that select an arm. `baseline` changes nothing: contract v2 stays off."""
    if arm != "v2":
        return {}, {}
    backend = {"ENABLE_ENGINE_SUBAGENT_V2": "true", "ENGINE_SUBAGENT_V2_SWITCHES": V2_SWITCHES[mode]}
    return backend, {"ENGINE_COMPLETE_TASK": "1", "ENGINE_STEER_WORKER": "1"}


@dataclass
class EvalWorld:
    world: World
    mode: str
    web: str
    replay_dir: Path
    miss_log: Path
    script_path: Path
    arm: str = "baseline"

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
    mode: str,
    *,
    web: str = "replay",
    replay_dir: Path | None = None,
    backend_env: Mapping[str, str] | None = None,
    arm: str | None = None,
) -> Iterator[EvalWorld]:
    """Boot the stack for `mode` and `web` (`replay`, `record` or `off`) with one worker running.

    `backend_env` is added to the backend's environment (the context-tier arms set `ENGINE_CONTEXT_TIERS` here); it is
    merged with the subagent arm's env, not replaced by it.
    `arm` selects the subagent contract (`baseline`: v2 off, as shipped; `v2`: `ENABLE_ENGINE_SUBAGENT_V2` on and the
    engine's `complete_task` / `steer_worker` tools registered). Default: the `EVAL_ARM` environment variable.
    """
    arm = resolve_arm(arm)
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
        arm_backend, arm_worker = arm_env(arm, mode)
        extra_env.update(arm_backend)
        worker_env.update(arm_worker)
        with stack(extra_env=extra_env, worker_command=worker_command, worker_env=worker_env) as world:
            world.start_worker(1)
            yield EvalWorld(
                world=world,
                mode=mode,
                web=web,
                replay_dir=directory,
                miss_log=miss_log,
                script_path=world.script,
                arm=arm,
            )
