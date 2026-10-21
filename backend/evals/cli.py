"""`python -m evals`: run a suite, write the report, diff against a baseline.

    run      run tasks (fake by default; `--live`/`--mode live` spends money), store JSON + markdown, optional diff
    report   render a stored result as markdown
    compare  diff a stored result against a baseline (KTD5); exits 1 on a regression; `--promote` saves a baseline

Exit codes: 0 ok, 1 a regression (or, without a baseline, a failed trial), 2 refused or bad usage.
Live mode keeps the runner's gates: it needs `EVAL_OPENROUTER_API_KEY` and a positive `EVAL_MAX_USD` (or `--max-usd`)
and refuses before anything boots. Run it with `uv run --env-file .env ...` so the key never reaches a command line.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import report as rpt
from . import runner
from .budget import ENV_VAR, BudgetConfigError, BudgetMeter
from .loader import TaskLoadError, load_tasks, select_by_tags
from .schema import Task
from .stack import LiveModeError

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_TASKS_DIR = EVALS_DIR / "tasks"
DEFAULT_RESULTS_DIR = EVALS_DIR / "results"
DEFAULT_BASELINES_DIR = EVALS_DIR / "baselines"


def resolve_report(ref: str, baselines_dir: Path) -> Path:
    """A path, or the name of a promoted baseline (`<baselines>/<ref>[.json]`)."""
    direct = Path(ref)
    if direct.is_file():
        return direct
    for cand in (baselines_dir / ref, baselines_dir / f"{ref}.json"):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"no report or baseline named {ref!r}")


def _select(args: argparse.Namespace) -> list[Task]:
    tasks = select_by_tags(load_tasks(args.tasks_dir), args.tag)
    if args.task:
        tasks = [t for t in tasks if t.id in set(args.task)]
    return tasks


def estimate_cost(tasks: Sequence[Task], k: int, previous: dict | None) -> tuple[float, int]:
    """(USD, tasks with no figure): per-trial cost from a previous report, else the task's own budget cap."""
    total, unknown = 0.0, 0
    for t in tasks:
        prev = (previous or {}).get("tasks", {}).get(t.id)
        if prev and prev.get("trials"):
            per_trial = prev["cost_usd"] / prev["trials"]
        elif t.budget.max_usd is not None:
            per_trial = t.budget.max_usd
        else:
            per_trial, unknown = 0.0, unknown + 1
        total += per_trial * k
    return total, unknown


def _latest_result(results_dir: Path) -> dict | None:
    for p in sorted(results_dir.glob("*.json"), reverse=True) if results_dir.is_dir() else ():
        try:
            return rpt.load_report(p)
        except (ValueError, OSError):
            continue
    return None


def _print_diff(diff: rpt.Diff, baseline: dict, current: dict) -> None:
    print(rpt.render_diff_markdown(diff, baseline_name=baseline["name"], current_name=current["name"]))
    print("RESULT: " + ("REGRESSION" if diff.regressed else "no regression"))


def cmd_run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    mode = "live" if args.live or args.mode == "live" else "fake"
    try:
        tasks = _select(args)
    except TaskLoadError as e:
        print(f"bad task file: {e}", file=sys.stderr)
        return 2
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        return 2
    if args.k < 1:
        print("-k must be at least 1", file=sys.stderr)
        return 2

    run_env = dict(env)
    if args.max_usd is not None:
        run_env[ENV_VAR] = str(args.max_usd)

    baseline = None
    if args.baseline:
        try:
            baseline = rpt.load_report(resolve_report(args.baseline, Path(args.baselines_dir)))
        except (OSError, ValueError) as e:
            print(f"cannot read baseline: {e}", file=sys.stderr)
            return 2

    results_dir = Path(args.results_dir)
    if args.dry_run:  # no gates, no stack, no engine call: estimate only
        previous = baseline or _latest_result(results_dir)
        est, unknown = estimate_cost(tasks, args.k, previous)
        print(f"dry run: {len(tasks)} task(s) x k={args.k} in {mode} mode: " + ", ".join(t.id for t in tasks))
        print(f"estimated cost: ${est:.4f}" + (f" ({unknown} task(s) with no figure)" if unknown else ""))
        return 0

    try:
        meter = BudgetMeter.from_env(run_env, required=mode == "live")
        results = asyncio.run(
            runner.run_suite(tasks, mode=mode, k=args.k, meter=meter, timeout_s=args.timeout)
        )
    except (LiveModeError, BudgetConfigError) as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2

    suite = list(args.tag)
    out_json = Path(args.out) if args.out else rpt.result_path(results_dir, suite, mode)
    traces = rpt.write_traces(results, out_json.with_suffix(".traces"))
    report = rpt.build_report(
        results,
        name=out_json.stem,
        suite=suite,
        mode=mode,
        k=args.k,
        spent_usd=meter.spent,
        max_usd=meter.max_usd,
        trace_files=traces,
    )
    rpt.write_report(report, out_json)
    out_md = out_json.with_suffix(".md")
    out_md.write_text(rpt.render_markdown(report))
    print(runner.summarize(results))
    print(f"report: {out_json}\nmarkdown: {out_md}")

    if args.push_braintrust:
        from .braintrust_push import push_report

        pushed = push_report(report)
        print("braintrust: pushed" if pushed.pushed else f"braintrust push skipped: {pushed.reason}", file=sys.stderr)

    if baseline is not None:
        diff = rpt.diff_reports(baseline, report)
        _print_diff(diff, baseline, report)
        return 1 if diff.regressed else 0
    counted = [r for r in results if r.status != "skipped_budget"]
    return 0 if all(r.passed for r in counted) else 1


def cmd_report(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        print(rpt.render_markdown(rpt.load_report(args.path)), end="")
    except (OSError, ValueError) as e:
        print(f"cannot read report: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_compare(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    baselines_dir = Path(args.baselines_dir)
    try:
        current = rpt.load_report(args.current)
        baseline = rpt.load_report(resolve_report(args.baseline, baselines_dir)) if args.baseline else None
    except (OSError, ValueError) as e:
        print(f"cannot read report: {e}", file=sys.stderr)
        return 2
    if baseline is None and not args.promote:
        print("compare needs a BASELINE (or --promote to save CURRENT as one)", file=sys.stderr)
        return 2
    regressed = False
    if baseline is not None:
        diff = rpt.diff_reports(baseline, current)
        _print_diff(diff, baseline, current)
        regressed = diff.regressed
    if args.promote:
        if regressed:
            print("not promoting: the comparison shows a regression", file=sys.stderr)
        else:
            baselines_dir.mkdir(parents=True, exist_ok=True)
            target = baselines_dir / f"{args.name or current['name']}.json"
            shutil.copyfile(args.current, target)
            print(f"promoted baseline: {target}")
    return 1 if regressed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run tasks and write a report")
    run.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    run.add_argument("--tag", "--suite", dest="tag", action="append", default=[], help="select tasks by tag (repeatable)")
    run.add_argument("--task", action="append", default=[], help="select a task id (repeatable)")
    run.add_argument("-k", type=int, default=1, help="trials per task")
    run.add_argument("--timeout", type=float, default=None, help="seconds per trial")
    run.add_argument("--mode", choices=("fake", "live"), default="fake")
    run.add_argument("--live", action="store_true", help="same as --mode live")
    run.add_argument("--max-usd", type=float, default=None, help=f"spend cap for this run (overrides {ENV_VAR})")
    run.add_argument("--dry-run", action="store_true", help="print the estimated cost and run nothing")
    run.add_argument("--baseline", help="baseline name or path: diff after the run, exit 1 on a regression")
    run.add_argument("--out", help="result file (default: results/<date>-<suite>-<mode>.json)")
    run.add_argument("--push-braintrust", action="store_true", help="also log the report to Braintrust (needs its key)")
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="render a stored result as markdown")
    rep.add_argument("path")
    rep.set_defaults(func=cmd_report)

    cmp_ = sub.add_parser("compare", help="diff CURRENT against BASELINE; exit 1 on a regression")
    cmp_.add_argument("current")
    cmp_.add_argument("baseline", nargs="?")
    cmp_.add_argument("--promote", action="store_true", help="save CURRENT as a baseline (only without a regression)")
    cmp_.add_argument("--name", help="baseline name when promoting (default: the result's name)")
    cmp_.set_defaults(func=cmd_compare)

    for sp in (run, cmp_):
        sp.add_argument("--baselines-dir", default=str(DEFAULT_BASELINES_DIR))
    run.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    return p


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, os.environ if env is None else env)


if __name__ == "__main__":
    raise SystemExit(main())
