"""Calibrate a judge against its human-labelled set (KTD6): TPR and TNR, each at least 0.85, or it may not gate.

`evals/calibration/<judge>.jsonl` has one JSON object per line: `{"id": ..., "label": "yes"|"no", "input": {...}}`
where `input` holds the judge's fields (see `JUDGE_FIELDS`). "yes" is the positive class:

- TPR = share of `yes` examples the judge answers `yes`;
- TNR = share of `no` examples the judge answers `no`.

A judge `error` is a wrong answer for either class. A passing run writes `<judge>.record.json` carrying the prompt hash
and the model, so a later prompt or model change voids it (`graders.judge.gating_status`).

`python -m evals.calibrate claim_supported [more judges | --all]` spends money (the judge model, capped by
`EVAL_MAX_USD`) and needs `EVAL_JUDGE_MODEL` plus a key; run it with `uv run --env-file .env ...`.
Exit codes: 0 all calibrated, 1 a judge is below the threshold, 2 cannot run (config, missing labels).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .budget import BudgetConfigError, BudgetMeter
from .graders.judge import (
    CALIBRATION_DIR,
    JUDGE_FIELDS,
    MIN_RATE,
    JudgeCache,
    JudgeConfigError,
    JudgeRunner,
    JudgeTransport,
    prompt_hash,
    record_path,
)

MIN_PER_CLASS = 20


class LabelError(Exception):
    """The labelled set is missing, malformed, or too small."""


@dataclass(frozen=True)
class Example:
    id: str
    label: str
    input: dict[str, Any]
    reviewed: bool = True  # a row with `"reviewed": false` is a draft label (see calibration/README.md)


@dataclass(frozen=True)
class Disagreement:
    id: str
    label: str
    got: str
    reason: str


@dataclass(frozen=True)
class Calibration:
    judge: str
    model: str
    positives: int
    negatives: int
    tpr: float
    tnr: float
    threshold: float
    min_per_class: int
    disagreements: tuple[Disagreement, ...] = ()
    cost_usd: float = 0.0
    problems: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return not self.problems and self.tpr >= self.threshold and self.tnr >= self.threshold

    def summary(self) -> str:
        lines = [
            f"{self.judge} [{self.model}]: TPR {self.tpr:.3f} ({self.positives} yes), "
            f"TNR {self.tnr:.3f} ({self.negatives} no), threshold {self.threshold:.2f}, spent ${self.cost_usd:.4f}"
        ]
        lines += [f"  problem: {p}" for p in self.problems]
        lines += [f"  disagree {d.id}: label {d.label}, judge {d.got} ({d.reason})" for d in self.disagreements]
        lines.append(f"  => {'CALIBRATED' if self.passed else 'NOT CALIBRATED'}")
        return "\n".join(lines)


def load_examples(judge: str, calibration_dir: Path = CALIBRATION_DIR) -> list[Example]:
    path = calibration_dir / f"{judge}.jsonl"
    if not path.is_file():
        raise LabelError(f"no labelled set for '{judge}': {path} is missing")
    out: list[Example] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            label, inp = row["label"], row["input"]
            if label not in ("yes", "no") or not isinstance(inp, dict):
                raise ValueError("label must be yes/no and input an object")
            missing = [f for f in JUDGE_FIELDS[judge] if not str(inp.get(f, "")).strip()]
            if missing:
                raise ValueError(f"input is missing {', '.join(missing)}")
            out.append(Example(str(row.get("id", f"line{n}")), label, inp, reviewed=row.get("reviewed", True) is not False))
        except (ValueError, KeyError, TypeError) as e:
            raise LabelError(f"{path.name} line {n}: {e}") from None
    if len({e.id for e in out}) != len(out):
        raise LabelError(f"{path.name}: duplicate example ids")
    return out


async def calibrate(
    runner: JudgeRunner,
    judge: str,
    examples: Sequence[Example],
    *,
    threshold: float = MIN_RATE,
    min_per_class: int = MIN_PER_CLASS,
) -> Calibration:
    """Run `judge` over `examples`; the result carries TPR, TNR, the disagreements and any reasons it cannot pass."""
    if threshold < MIN_RATE:
        raise ValueError(f"the threshold may not be below {MIN_RATE}")
    pos = [e for e in examples if e.label == "yes"]
    neg = [e for e in examples if e.label == "no"]
    hit_pos = hit_neg = 0
    miss: list[Disagreement] = []
    cost = 0.0
    for e in examples:
        v = await runner.judge(judge, e.input)
        cost += v.cost_usd
        right = v.verdict == e.label
        hit_pos += right and e.label == "yes"
        hit_neg += right and e.label == "no"
        if not right:
            miss.append(Disagreement(e.id, e.label, v.verdict, v.reason))
    problems = []
    if len(pos) < min_per_class or len(neg) < min_per_class:
        problems.append(f"needs at least {min_per_class} yes and {min_per_class} no examples, has {len(pos)} and {len(neg)}")
    return Calibration(
        judge=judge,
        model=runner.model,
        positives=len(pos),
        negatives=len(neg),
        tpr=hit_pos / len(pos) if pos else 0.0,
        tnr=hit_neg / len(neg) if neg else 0.0,
        threshold=threshold,
        min_per_class=min_per_class,
        disagreements=tuple(miss),
        cost_usd=cost,
        problems=tuple(problems),
    )


def write_record(cal: Calibration, runner: JudgeRunner, calibration_dir: Path = CALIBRATION_DIR) -> Path:
    """Persist the outcome with the prompt hash and model it was measured with."""
    path = record_path(cal.judge, calibration_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "judge": cal.judge,
                "model": cal.model,
                "prompt_sha256": prompt_hash(cal.judge, runner.prompts_dir),
                "tpr": round(cal.tpr, 4),
                "tnr": round(cal.tnr, 4),
                "threshold": cal.threshold,
                "positives": cal.positives,
                "negatives": cal.negatives,
                "passed": cal.passed,
                "calibrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n"
    )
    return path


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: JudgeTransport | None = None,
    calibration_dir: Path = CALIBRATION_DIR,
    meter: BudgetMeter | None = None,
    model: str | None = None,
    cache: JudgeCache | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.calibrate", description=__doc__.splitlines()[0])
    parser.add_argument("judges", nargs="*", help=f"judge names ({', '.join(JUDGE_FIELDS)})")
    parser.add_argument("--all", action="store_true", help="every judge that has a labelled set")
    parser.add_argument("--threshold", type=float, default=MIN_RATE)
    parser.add_argument("--min-per-class", type=int, default=MIN_PER_CLASS)
    parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="calibrate on draft labels (reviewed=false); the record is a dry run, never evidence for gating",
    )
    args = parser.parse_args(argv)

    names = list(args.judges)
    if args.all:
        names += [j for j in JUDGE_FIELDS if (calibration_dir / f"{j}.jsonl").is_file() and j not in names]
    if not names:
        print("no judges selected", file=sys.stderr)
        return 2
    unknown = [n for n in names if n not in JUDGE_FIELDS]
    if unknown:
        print(f"unknown judge(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    drafts: dict[str, int] = {}
    try:
        meter = meter or BudgetMeter.from_env(required=True)  # the cap is mandatory: this spends money
        extra: dict[str, Any] = {"calibration_dir": calibration_dir, **({"cache": cache} if cache else {})}
        if model is not None:
            runner = JudgeRunner(transport, model, meter=meter, **extra)  # type: ignore[arg-type]
        else:
            runner = JudgeRunner.from_env(transport, meter=meter, **extra)
        sets = {n: load_examples(n, calibration_dir) for n in names}
        drafts = {n: sum(not e.reviewed for e in ex) for n, ex in sets.items() if any(not e.reviewed for e in ex)}
        if drafts and not args.allow_unreviewed:
            detail = ", ".join(f"{n} ({k} unreviewed)" for n, k in drafts.items())
            raise LabelError(f"labels need human review before calibration counts: {detail}; see calibration/README.md")
    except (JudgeConfigError, BudgetConfigError, LabelError) as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2

    async def run() -> list[Calibration]:
        out = []
        for n in names:
            cal = await calibrate(
                runner, n, sets[n], threshold=args.threshold, min_per_class=args.min_per_class
            )
            if n not in drafts:  # a dry run on draft labels never writes a record that could enable gating
                write_record(cal, runner, calibration_dir)
            print(cal.summary())
            out.append(cal)
        return out

    results = asyncio.run(run())
    print(f"spent ${meter.spent:.4f}" + (f" of ${meter.max_usd:.2f}" if meter.max_usd is not None else ""))
    return 0 if all(c.passed for c in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
