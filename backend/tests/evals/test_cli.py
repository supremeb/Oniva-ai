"""The eval CLI: selection, gates, dry run, baseline diff and exit codes. The engine run itself is patched out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import cli, report as rpt, runner
from evals.schema import GraderResult, Span, Trace, Trial, TrialResult, Usage

CORPUS = Path(runner.__file__).parent / "tasks"
KEY_VAR = "EVAL_OPENROUTER_API_KEY"


def _fake_run(passing: bool):
    async def run_suite(tasks, *, mode, k, meter, timeout_s=None, replay_dir=None, judges=None):
        run_suite.calls.append((mode, k, [t.id for t in tasks]))
        run_suite.judges.append(judges)
        trace = Trace(
            root=Span(kind="subagent", name="root"), usage_by_role={"root": Usage(input_tokens=10, cost_usd=0.01)}
        )
        meter.record(0.01 * len(tasks) * k)
        return [
            TrialResult(trial=Trial(t.id, i, mode), grader_results=(GraderResult("g", passing),), trace=trace)
            for t in tasks
            for i in range(k)
        ]

    run_suite.calls = []
    run_suite.judges = []
    return run_suite


@pytest.fixture
def dirs(tmp_path):
    return ["--results-dir", str(tmp_path / "results"), "--baselines-dir", str(tmp_path / "baselines")]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(KEY_VAR, raising=False)
    monkeypatch.delenv("EVAL_MAX_USD", raising=False)


def test_run_selects_by_tag_and_writes_json_and_markdown(monkeypatch, dirs, tmp_path, capsys):
    fake = _fake_run(True)
    monkeypatch.setattr(runner, "run_suite", fake)
    assert cli.main(["run", "--tag", "smoke", "-k", "2", *dirs], env={}) == 0
    mode, k, ids = fake.calls[0]
    assert (mode, k) == ("fake", 2) and ids
    out = sorted((tmp_path / "results").glob("*-smoke-fake.json"))
    assert len(out) == 1 and out[0].with_suffix(".md").exists()
    assert rpt.load_report(out[0])["k"] == 2
    assert "report:" in capsys.readouterr().out


def test_failures_without_a_baseline_exit_1_and_unknown_task_exits_2(monkeypatch, dirs):
    monkeypatch.setattr(runner, "run_suite", _fake_run(False))
    assert cli.main(["run", "--task", "chat-fake-browse", *dirs], env={}) == 1
    assert cli.main(["run", "--task", "no-such-task", *dirs], env={}) == 2


def test_dry_run_estimates_and_calls_no_engine(monkeypatch, dirs, tmp_path, capsys):
    fake = _fake_run(True)
    monkeypatch.setattr(runner, "run_suite", fake)
    assert cli.main(["run", "--task", "chat-fake-browse", "-k", "3", "--dry-run", *dirs], env={}) == 0
    assert "estimated cost" in capsys.readouterr().out and not fake.calls
    assert not (tmp_path / "results").exists()


def test_dry_run_uses_the_last_reports_per_task_cost(monkeypatch, dirs, tmp_path, capsys):
    monkeypatch.setattr(runner, "run_suite", _fake_run(True))
    assert cli.main(["run", "--task", "chat-fake-browse", *dirs], env={}) == 0  # 0.01 per trial
    capsys.readouterr()
    assert cli.main(["run", "--task", "chat-fake-browse", "-k", "5", "--dry-run", *dirs], env={}) == 0
    assert "estimated cost: $0.0500" in capsys.readouterr().out


def test_live_refuses_without_a_cap_or_key_before_running(monkeypatch, dirs, capsys):
    fake = _fake_run(True)
    monkeypatch.setattr(runner, "run_suite", fake)
    assert cli.main(["run", "--live", "--task", "chat-fake-browse", *dirs], env={}) == 2
    assert "EVAL_MAX_USD" in capsys.readouterr().err
    assert cli.main(["run", "--mode", "live", "--max-usd", "0", "--task", "chat-fake-browse", *dirs], env={}) == 2
    assert not fake.calls


def test_live_refuses_without_a_key_with_the_real_gate(monkeypatch, dirs, capsys):
    def no_stack(*a, **k):
        raise AssertionError("a stack was booted")

    monkeypatch.setattr(runner, "eval_stack", no_stack)
    assert cli.main(["run", "--live", "--max-usd", "1", "--task", "chat-fake-browse", *dirs], env={}) == 2
    assert KEY_VAR in capsys.readouterr().err


def test_baseline_diff_sets_the_exit_code_and_promote_gates(monkeypatch, dirs, tmp_path, capsys):
    monkeypatch.setattr(runner, "run_suite", _fake_run(True))
    good = tmp_path / "good.json"
    assert cli.main(["run", "--task", "chat-fake-browse", "-k", "10", "--out", str(good), *dirs], env={}) == 0
    assert cli.main(["compare", str(good), "--promote", "--name", "base", *dirs[2:]], env={}) == 0
    assert (tmp_path / "baselines" / "base.json").exists()

    monkeypatch.setattr(runner, "run_suite", _fake_run(False))
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    assert cli.main(["run", "--task", "chat-fake-browse", "-k", "10", "--out", str(bad), "--baseline", "base", *dirs], env={}) == 1
    assert "RESULT: REGRESSION" in capsys.readouterr().out
    assert cli.main(["compare", str(bad), "base", "--promote", "--name", "worse", *dirs[2:]], env={}) == 1
    assert not (tmp_path / "baselines" / "worse.json").exists()
    assert cli.main(["compare", str(good), "base", *dirs[2:]], env={}) == 0
    assert cli.main(["compare", str(good), *dirs[2:]], env={}) == 2


def test_report_renders_a_stored_result(monkeypatch, dirs, tmp_path, capsys):
    monkeypatch.setattr(runner, "run_suite", _fake_run(True))
    out = tmp_path / "r.json"
    cli.main(["run", "--task", "chat-fake-browse", "--out", str(out), *dirs], env={})
    capsys.readouterr()
    assert cli.main(["report", str(out)], env={}) == 0
    assert "# Eval report" in capsys.readouterr().out
    assert cli.main(["report", str(tmp_path / "missing.json")], env={}) == 2


def test_braintrust_push_is_off_unless_asked(monkeypatch, dirs):
    import evals.braintrust_push as bp

    pushed = []
    monkeypatch.setattr(bp, "push_report", lambda rep, **kw: pushed.append(rep) or bp.PushResult(False, "no key"))
    monkeypatch.setattr(runner, "run_suite", _fake_run(True))
    cli.main(["run", "--task", "chat-fake-browse", *dirs], env={})
    assert not pushed
    cli.main(["run", "--task", "chat-fake-browse", "--push-braintrust", *dirs], env={})
    assert len(pushed) == 1


def test_stored_json_is_plain(monkeypatch, dirs, tmp_path):
    monkeypatch.setattr(runner, "run_suite", _fake_run(True))
    out = tmp_path / "r.json"
    cli.main(["run", "--task", "chat-fake-browse", "--out", str(out), *dirs], env={})
    assert json.loads(out.read_text())["tasks"]["chat-fake-browse"]["outcomes"] == [1.0]
