"""U34: the memory arms (control / manual / propose / auto), their stack environment and the CLI switches (offline)."""

import asyncio
import contextlib
from pathlib import Path

import pytest

from evals import arms, cli, runner
from evals.loader import load_tasks, select_by_tags
from evals.schema import Trial, TrialResult
from tests.e2e.harness import memory_env

EVALS = Path(__file__).resolve().parents[2] / "evals"


@pytest.fixture(scope="module")
def tasks():
    return select_by_tags(load_tasks(EVALS / "tasks"), ["memory"])


def test_memory_env_turns_the_curator_and_the_worker_task_on_together():
    backend, worker = memory_env("propose")
    assert backend["E2E_MEMORY"] == "1" and backend["MEMORY_CURATOR_MODE"] == "propose"
    assert float(backend["MEMORY_CURATOR_DAILY_BUDGET_USD"]) >= 100
    assert worker == {"ENGINE_MEMORY_CURATE": "1"}
    off_backend, off_worker = memory_env("off")
    assert off_backend["MEMORY_CURATOR_MODE"] == "off" and off_worker == {}
    with pytest.raises(ValueError):
        memory_env("always")


def test_arm_environments():
    assert arms.memory_stack_env(None) == ({}, {}) and arms.memory_stack_env("control") == ({}, {})
    assert arms.memory_stack_env("manual")[0]["MEMORY_CURATOR_MODE"] == "off"
    assert arms.memory_stack_env("manual")[1] == {}
    assert arms.memory_stack_env("auto")[0]["MEMORY_CURATOR_MODE"] == "auto"
    assert arms.memory_stack_env("auto")[1] == {"ENGINE_MEMORY_CURATE": "1"}
    with pytest.raises(ValueError):
        arms.memory_stack_env("sometimes")


def test_only_repeat_tasks_are_expanded_and_pinned_tasks_are_left_alone(tasks):
    planned = arms.expand_arms(tasks)
    assert len(planned) == 30 + 12 + 12
    repeat = [t for t in planned if t.kind == "repeat"]
    assert sorted(t.id.rpartition("@")[2] for t in repeat) == ["control"] * 6 + ["propose"] * 6
    assert {arms.memory_of(t) for t in planned if t.kind == "redteam"} == {"auto"}
    assert {arms.memory_of(t) for t in planned if t.kind == "memory"} == {"manual"}
    assert arms.expand_arms(planned) == planned  # already pinned
    assert arms.split_memory_arm("m3-rp-01-x@propose") == ("m3-rp-01-x", "propose")
    assert arms.split_memory_arm("ls-x@full") == ("ls-x@full", None)  # a context-tier arm is not a memory arm


def test_the_repeat_trial_timeout_scales_with_its_runs(tasks):
    repeat = next(t for t in tasks if t.kind == "repeat")
    chat = next(t for t in tasks if t.kind == "memory")
    assert runner.default_timeout(repeat, "fake") == 5 * runner.DEFAULT_TIMEOUT_S["fake"]
    assert runner.default_timeout(repeat, "live") == 5 * runner.DEFAULT_TIMEOUT_S["live"]
    assert runner.default_timeout(chat, "live") == runner.DEFAULT_TIMEOUT_S["live"]


def test_run_suite_boots_one_stack_per_memory_arm_with_that_arms_environment(monkeypatch, tasks):
    booted = []

    @contextlib.contextmanager
    def fake_stack(mode, **kw):
        booted.append(kw)
        yield object()

    async def fake_run(ew, group, k, meter, http, timeout_s=None, *, judges=None):
        return {(t.id, i): TrialResult(trial=Trial(t.id, i, "fake")) for t in group for i in range(k)}

    monkeypatch.setattr(runner, "eval_stack", fake_stack)
    monkeypatch.setattr(runner, "run_on_stack", fake_run)
    picked = [t for t in tasks if t.id in ("m1-rt-01-web-chat-store-pref", "m2-pf-01-british-spelling", "m3-rp-01-two-worker-simple")]
    results = asyncio.run(runner.run_suite(picked, mode="fake"))
    assert [r.trial.task_id for r in results] == [
        "m1-rt-01-web-chat-store-pref", "m2-pf-01-british-spelling", "m3-rp-01-two-worker-simple@propose", "m3-rp-01-two-worker-simple@control"]
    modes = sorted((kw["backend_env"].get("MEMORY_CURATOR_MODE", "-"), "ENGINE_MEMORY_CURATE" in kw["worker_env"]) for kw in booted)
    assert modes == [("-", False), ("auto", True), ("off", False), ("propose", True)]  # control, redteam, prefs, repeat

    booted.clear()  # --memory pins every task to one arm
    asyncio.run(runner.run_suite(picked, mode="fake", memory="manual"))
    assert [kw["backend_env"]["MEMORY_CURATOR_MODE"] for kw in booted] == ["off", "off"]  # one per web mode, none per arm


def test_cli_dry_run_plans_every_arm_and_memory_pins_them(capsys):
    assert cli.main(["run", "--suite", "memory", "--dry-run", "-k", "3"]) == 0
    out = capsys.readouterr().out
    assert "54 task(s) x k=3 in fake mode" in out and "m3-rp-01-two-worker-simple@control" in out
    assert cli.main(["run", "--suite", "memory", "--memory", "manual", "--dry-run"]) == 0
    assert "48 task(s) x k=1" in capsys.readouterr().out


def test_the_plans_live_command_selects_the_whole_memory_suite_and_is_capped(capsys):
    args = cli.build_parser().parse_args(["run", "--suite", "memory", "--mode", "live", "-k", "3", "--max-usd", "40"])
    assert args.tag == ["memory"] and args.mode == "live" and args.k == 3 and args.max_usd == 40
    assert len(cli._select(args)) == 48
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run", "--memory", "sometimes"])
