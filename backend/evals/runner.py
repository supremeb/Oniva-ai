"""Run tasks for k trials against the real engine and grade each trace.

One stack per (mode, web mode) group of tasks, shared by that group's trials (a fresh conversation and, for Deep Work,
a fresh run per trial). A trial never raises: a refused route, a run that ended in `run_error`, a timeout, a web
call with no fixture or an exhausted budget each give a `TrialResult` with a `status`.

Judges: a trial gets LLM judge verdicts only when a `JudgeRunner` is supplied and the trial ended `ok`. Without one
(fake mode, `--no-judges`) or after a failed trial each judge grader yields a passing, non-gating `skipped:` result,
so a skipped judge never gates and never fakes a verdict. Judges run after the code graders.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterable, Sequence
from typing import Any

import httpx

from . import arms as arms_mod
from . import long_session
from .budget import ENV_VAR, BudgetConfigError, BudgetMeter
from .drivers import AUTH, DriverContext, DriverError, DriverOutput, drive
from .fake_scripts import ScriptError, build_script
from .graders import GradeContext, grade, to_grader_result
from .graders.context import trial_metrics
from .graders.judge import JudgeConfigError, JudgeRunner
from .ledger import settle_cost, with_ledger_cost
from .schema import GraderResult, Task, Trial, TrialResult
from .stack import FIXTURE_DIR, EvalWorld, LiveModeError, eval_stack, require_live_key
from .trace import TraceTruncated, flatten

DEFAULT_TIMEOUT_S = {"fake": 180.0, "live": 900.0}


def default_timeout(task: Task, mode: str) -> float:
    """Seconds a trial may take: a flat default, more for a long session (it scales with its turns)."""
    base = DEFAULT_TIMEOUT_S[mode]
    return max(base, long_session.timeout_s(task, mode)) if task.kind == "long_session" else base


def run_error_kind(raw: dict[str, Any] | None) -> str | None:
    """The kind of the root conversation's last `run_error` (`provider`, `aborted`, ...), else None."""
    kind = None
    for e in (raw or {}).get("entries") or []:
        if e.get("kind") == "run_error":
            err = (e.get("payload") or {}).get("error")
            kind = str(err.get("kind") if isinstance(err, dict) else err or "run_error")
    return kind


def grade_trace(task: Task, ctx: GradeContext) -> tuple[GraderResult, ...]:
    """Run the task's code graders; judges are skipped. A broken grader fails closed with its message."""
    out: list[GraderResult] = []
    for spec in task.graders:
        if spec.type == "judge":
            continue
        try:
            out.append(to_grader_result(spec, grade(spec, ctx)))
        except Exception as e:  # noqa: BLE001 - a misconfigured grader must fail its trial, not the suite
            out.append(GraderResult(grader_id=spec.id, passed=False, gating=spec.gating, detail=f"grader error: {e}"))
    return tuple(out)


def judge_specs(task: Task) -> list:
    return [g for g in task.graders if g.type == "judge"]


def skipped_judges(task: Task, reason: str) -> tuple[GraderResult, ...]:
    """One passing, non-gating result per judge grader, saying why it did not run."""
    return tuple(
        GraderResult(grader_id=g.id, passed=True, gating=False, detail=f"skipped: {reason}") for g in judge_specs(task)
    )


async def judge_trace(
    task: Task, ctx: GradeContext, judges: JudgeRunner | None, *, why_not: str = "judges are off"
) -> tuple[GraderResult, ...]:
    """The task's judge graders: real verdicts from `judges` (cached, metered), else `skipped` results."""
    if judges is None:
        return skipped_judges(task, why_not)
    return tuple(await judges.grade_all(task.graders, ctx))


def _failed(trial: Trial, status: str, code: str, message: str, started: float, phases: dict[str, float]) -> TrialResult:
    return TrialResult(
        trial=trial,
        error=f"{code}: {message}" if message else code,
        error_code=code,
        status=status,
        latency_s=round(time.monotonic() - started, 3),
        phases=phases,
    )


def skipped(task: Task, index: int, mode: str) -> TrialResult:
    return TrialResult(
        trial=Trial(task.id, index, mode),  # type: ignore[arg-type]
        status="skipped_budget",
        error="skipped_budget: the EVAL_MAX_USD cap would be crossed",
        error_code="skipped_budget",
    )


async def run_trial(
    ew: EvalWorld, task: Task, index: int, http: httpx.AsyncClient,
    *,
    timeout_s: float | None = None,
    judges: JudgeRunner | None = None,
) -> TrialResult:
    """One trial of `task` on a running stack."""
    trial = Trial(task.id, index, ew.mode)  # type: ignore[arg-type]
    started = time.monotonic()
    if ew.mode == "fake":
        try:
            ew.write_script(build_script(task))
        except ScriptError as e:
            return _failed(trial, "error", "bad_task", str(e), started, {})
    _, miss_offset = ew.misses_since(0)
    api = ew.engine_api()
    ctx = DriverContext(
        ew=ew,
        task=task,
        trial_index=index,
        timeout_s=timeout_s or default_timeout(task, ew.mode),
        http=http,
        api=api,
    )
    try:
        out: DriverOutput = await drive(ctx)
    except DriverError as e:
        return _failed(trial, "error", e.code, e.message, started, dict(ctx.phases))
    except Exception as e:  # noqa: BLE001 - never let one trial end the suite
        return _failed(trial, "error", "driver_crashed", f"{type(e).__name__}: {e}", started, dict(ctx.phases))
    finally:
        await api.aclose()
    misses = ew.misses_since(0)[0][miss_offset:]

    try:
        trace = flatten(out.raw or {})
    except TraceTruncated as e:
        return _failed(trial, "error", "trace_truncated", str(e), started, out.phases)
    # The engine's usage documents have no dollars (the worker has no pricing table); the backend ledger does.
    ledger = await settle_cost(http, ew.world.backend_url, AUTH, out.conversation_id, trace)
    trace = with_ledger_cost(trace, ledger["totalUsd"])
    gctx = GradeContext(trace=trace, raw=out.raw, files=out.files)
    graders = grade_trace(task, gctx)
    status, code, error = "ok", None, None
    if out.timed_out:
        status, code, error = "timeout", "timeout", f"timeout: no result within {ctx.timeout_s:g}s"
    elif misses:
        status, code = "fixture_miss", "fixture_miss"
        error = f"fixture_miss: {len(misses)} web call(s) had no fixture ({', '.join(m[:12] for m in misses)})"
    elif (kind := run_error_kind(out.raw)) is not None:
        status, code, error = "run_error", kind, f"run_error: the run ended with {kind}"
    if status == "ok":
        graders += await judge_trace(task, gctx, judges, why_not="judges are off (fake mode or --no-judges)")
    else:
        graders += skipped_judges(task, f"the trial ended {status}")
    return TrialResult(
        trial=trial,
        metrics=trial_metrics(out.raw),
        grader_results=graders,
        trace=trace,
        error=error,
        latency_s=round(time.monotonic() - started, 3),
        status=status,
        error_code=code,
        raw=out.raw,
        phases=out.phases,
        fixture_misses=tuple(misses),
        ledger=ledger,
    )


def _live_judges(tasks: Sequence[Task], meter: BudgetMeter) -> JudgeRunner | bool:
    """A judge runner from the environment if any task declares a judge; raises JudgeConfigError before anything boots."""
    if not any(judge_specs(t) for t in tasks):
        return False
    return JudgeRunner.from_env(meter=meter)


async def run_suite(
    tasks: Sequence[Task],
    *,
    mode: str = "fake",
    k: int = 1,
    meter: BudgetMeter | None = None,
    timeout_s: float | None = None,
    replay_dir: Any = None,
    judges: JudgeRunner | bool | None = None,
    tiers: str | None = None,
) -> list[TrialResult]:
    """Run every task k times; results are in task order, then trial order.

    `judges`: a `JudgeRunner` to use; `None` (default) builds one from the environment in live mode and none in fake
    mode (fake stays offline and deterministic: judge graders are `skipped`); `False` never judges.

    Context tiers (U17): `tiers` pins every task to one `ENGINE_CONTEXT_TIERS` value. Without it, each `long_session`
    task that pins no tier is expanded into one task per arm (`<id>@<tier>`, see `evals.arms`). A stack is booted per
    (web mode, tier) group, so the returned results may name more tasks than were passed.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if mode == "live":  # before anything boots: no key or no cap means nothing is spent
        require_live_key()
        if meter is None or meter.max_usd is None or meter.max_usd <= 0:
            raise BudgetConfigError(f"live mode needs a spend cap: set {ENV_VAR} (USD) before running")
    meter = meter or BudgetMeter(None)
    if tiers is not None:
        tasks = [arms_mod.with_tiers(t, tiers) for t in tasks]
    else:
        tasks = arms_mod.expand_arms(tasks)
    if judges is None:
        judges = _live_judges(tasks, meter) if mode == "live" else False
    elif isinstance(judges, JudgeRunner) and judges.meter is None:
        judges.meter = meter
    judge_runner = judges if isinstance(judges, JudgeRunner) else None
    groups: dict[tuple[str, str | None], list[Task]] = {}
    for t in tasks:
        groups.setdefault((t.environment.web, arms_mod.tiers_of(t)), []).append(t)
    results: dict[tuple[str, int], TrialResult] = {}
    async with httpx.AsyncClient(timeout=60) as http:
        for (web, tier), group in groups.items():
            env = arms_mod.stack_env(tier, mode)
            generated = web != "off" and any(t.kind == "long_session" for t in group)
            base = replay_dir or FIXTURE_DIR
            with contextlib.ExitStack() as stack:
                directory = stack.enter_context(long_session.replay_dir_for(group, base)) if generated else replay_dir
                ew = stack.enter_context(eval_stack(mode, web=web, replay_dir=directory, backend_env=env))
                results.update(await run_on_stack(ew, group, k, meter, http, timeout_s, judges=judge_runner))
    return [results[(t.id, i)] for t in tasks for i in range(k)]


async def run_on_stack(
    ew: EvalWorld,
    tasks: Sequence[Task],
    k: int,
    meter: BudgetMeter,
    http: httpx.AsyncClient,
    timeout_s: float | None = None,
    *,
    judges: JudgeRunner | None = None,
) -> dict[tuple[str, int], TrialResult]:
    """Run `tasks` k times each on a stack that is already up; trials the budget cannot cover are `skipped_budget`."""
    results: dict[tuple[str, int], TrialResult] = {}
    for task in tasks:
        for i in range(k):
            if not meter.can_start(task.budget.max_usd):
                results[(task.id, i)] = skipped(task, i, ew.mode)
                continue
            res = await run_trial(ew, task, i, http, timeout_s=timeout_s, judges=judges)
            meter.record(res.cost_usd)
            results[(task.id, i)] = res
    return results


def entry_kinds(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    """Entry kinds per conversation (root first, then children by role and order): the determinism fingerprint."""
    raw = raw or {}
    out = {"root": [e["kind"] for e in raw.get("entries") or []]}
    kids = sorted(
        (c.get("role", ""), [e["kind"] for e in c.get("entries") or []]) for c in (raw.get("children") or {}).values()
    )
    for n, (role, kinds) in enumerate(kids):
        out[f"{n}:{role}"] = kinds
    return out


def summarize(results: Iterable[TrialResult]) -> str:
    """One line per trial (the CLI of U8 prints richer reports)."""
    lines = []
    for r in results:
        verdict = "PASS" if r.passed else r.status.upper() if r.status != "ok" else "FAIL"
        failed = [g.grader_id for g in r.grader_results if g.gating and not g.passed]
        lines.append(f"{r.trial.task_id}#{r.trial.index}: {verdict} ${r.cost_usd:.4f} {r.latency_s:.1f}s {failed or ''}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m evals.runner`: run tasks in fake mode (default) or, with an explicit `--live`, against OpenRouter.

    Live mode spends money: it needs `EVAL_OPENROUTER_API_KEY` and a positive `EVAL_MAX_USD`, and refuses to start
    without either. Run it with `uv run --env-file .env ...` so the key never appears on a command line.
    """
    import argparse
    import asyncio
    from pathlib import Path

    from .loader import load_tasks, select_by_tags

    parser = argparse.ArgumentParser(prog="python -m evals.runner", description=main.__doc__)
    parser.add_argument("--tasks-dir", default=str(Path(__file__).resolve().parent / "tasks"))
    parser.add_argument("--suite", action="append", default=[], help="run tasks with this tag (repeatable)")
    parser.add_argument("--task", action="append", default=[], help="run this task id (repeatable)")
    parser.add_argument("-k", type=int, default=1, help="trials per task")
    parser.add_argument("--timeout", type=float, default=None, help="seconds per trial")
    parser.add_argument("--live", action="store_true", help="use the production worker and a paid OpenRouter key")
    parser.add_argument("--no-judges", action="store_true", help="skip the LLM judge graders")
    args = parser.parse_args(argv)

    tasks = select_by_tags(load_tasks(args.tasks_dir), args.suite)
    if args.task:
        tasks = [t for t in tasks if t.id in set(args.task)]
    if not tasks:
        print("no tasks selected")
        return 2
    mode = "live" if args.live else "fake"
    try:
        meter = BudgetMeter.from_env(required=args.live)
        results = asyncio.run(
            run_suite(
                tasks, mode=mode, k=args.k, meter=meter, timeout_s=args.timeout, judges=False if args.no_judges else None
            )
        )
    except (LiveModeError, BudgetConfigError, JudgeConfigError) as e:
        print(f"refusing to run: {e}")
        return 2
    print(summarize(results))
    print(f"spent ${meter.spent:.4f}" + (f" of ${meter.max_usd:.2f}" if meter.max_usd is not None else ""))
    return 0 if all(r.passed for r in results if r.status != "skipped_budget") else 1


if __name__ == "__main__":
    raise SystemExit(main())
