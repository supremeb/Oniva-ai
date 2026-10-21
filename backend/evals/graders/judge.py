"""Binary LLM judges (KTD6): one yes/no question per failure mode, a pinned model, verdicts cached by content.

A judge is a prompt file under `evals/judges/` plus an output contract `{"verdict": "yes"|"no", "reason": "..."}`.
"yes" always means the trial did the right thing, so a judge's positive class is "yes".

- **Transport.** The model call goes through a `JudgeTransport` (an async callable). `llm_direct_transport` is the
  real one (OpenRouter through `app.services.llm_direct`, temperature 0); tests pass a fake. Nothing here calls a model
  by itself.
- **Strict parsing.** A reply must be one JSON object with `verdict` in {yes, no} and a string `reason`. A malformed
  reply (or a transport error) is retried once, then the verdict is `error`. `error` never passes.
- **Cache.** Verdicts (`yes`/`no` only) are cached on disk by `(judge, sha256(prompt file), model, sha256(input))`,
  so re-scoring a stored trace costs nothing and a changed prompt or model misses.
- **Cost.** Each call's dollars are charged to the run's `BudgetMeter` (`EVAL_MAX_USD`); once the cap is spent a judge
  returns `error` ("budget") instead of calling the model.
- **Gating.** A judge grader gates only when a calibration record (written by `evals.calibrate`) matches the current
  prompt hash and model and shows TPR and TNR at or above the threshold. Otherwise it is reported as advisory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.budget import BudgetMeter
from evals.schema import GraderResult, GraderSpec

from . import GradeContext, Outcome, outcome
from .deep_work import cited_indexes, numbered_sources

EVALS_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = EVALS_DIR / "judges"
CALIBRATION_DIR = EVALS_DIR / "calibration"
CACHE_DIR = EVALS_DIR / ".cache" / "judges"

MODEL_VAR = "EVAL_JUDGE_MODEL"
KEY_VARS = ("EVAL_JUDGE_API_KEY", "EVAL_OPENROUTER_API_KEY")
MIN_RATE = 0.85
MAX_ITEMS = 12
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_REPLY_TOKENS = 300

VERDICTS = ("yes", "no")

# Fields each judge's user message carries, in order. Labelled-set rows and grader extractors both supply them.
JUDGE_FIELDS: dict[str, tuple[str, ...]] = {
    "claim_supported": ("claim", "evidence"),
    "plan_coverage": ("request", "plan"),
    "no_unsourced_facts": ("report", "evidence"),
    "memory_applied": ("memory", "request", "response"),
    "recall_probe": ("fact", "question", "answer"),
}


class JudgeConfigError(Exception):
    """The judge cannot be set up (unknown judge, no pinned model, no key)."""


@dataclass(frozen=True)
class JudgeCall:
    model: str
    system: str
    user: str


@dataclass(frozen=True)
class JudgeReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None: priced from the model's table entry


JudgeTransport = Callable[[JudgeCall], Awaitable[JudgeReply]]


@dataclass(frozen=True)
class Verdict:
    """`verdict` is `yes`, `no` or `error` (malformed twice, transport failure, or budget spent)."""

    verdict: str
    reason: str = ""
    cached: bool = False
    calls: int = 0
    cost_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return self.verdict == "yes"


# --- files, hashes -------------------------------------------------------------------------------------------------


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_text(judge: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    if judge not in JUDGE_FIELDS:
        raise JudgeConfigError(f"unknown judge '{judge}' (known: {', '.join(JUDGE_FIELDS)})")
    path = prompts_dir / f"{judge}.md"
    if not path.is_file():
        raise JudgeConfigError(f"judge '{judge}': prompt file {path} is missing")
    return path.read_text(encoding="utf-8")


def prompt_hash(judge: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    return sha256(prompt_text(judge, prompts_dir))


def render_user(judge: str, item: Mapping[str, Any]) -> str:
    """The user message: each field as a labelled block (`<CLAIM>...</CLAIM>`)."""
    missing = [f for f in JUDGE_FIELDS[judge] if not str(item.get(f, "")).strip()]
    if missing:
        raise ValueError(f"judge '{judge}' input is missing: {', '.join(missing)}")
    return "\n\n".join(f"<{f.upper()}>\n{item[f]}\n</{f.upper()}>" for f in JUDGE_FIELDS[judge])


def input_hash(judge: str, item: Mapping[str, Any]) -> str:
    return sha256(canonical({f: str(item.get(f, "")) for f in JUDGE_FIELDS[judge]}))


# --- strict parsing ------------------------------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL)


class ParseError(ValueError):
    pass


def parse_reply(text: str) -> tuple[str, str]:
    """`(verdict, reason)` from a reply that is exactly one JSON object (a single code fence around it is allowed)."""
    body = text.strip()
    if m := _FENCE.match(body):
        body = m.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ParseError(f"not JSON: {e.msg}") from None
    if not isinstance(data, dict):
        raise ParseError("reply is not a JSON object")
    if set(data) != {"verdict", "reason"}:
        raise ParseError("reply must have exactly the keys verdict and reason")
    verdict, reason = data["verdict"], data["reason"]
    if verdict not in VERDICTS:
        raise ParseError(f"verdict must be 'yes' or 'no', got {verdict!r}")
    if not isinstance(reason, str):
        raise ParseError("reason must be a string")
    return verdict, reason.strip()


# --- cache ---------------------------------------------------------------------------------------------------------


class JudgeCache:
    """Verdicts on disk (one small file per key); `directory=None` keeps them in memory only."""

    def __init__(self, directory: Path | None = CACHE_DIR) -> None:
        self.directory = directory
        self._mem: dict[str, tuple[str, str]] = {}

    @staticmethod
    def key(judge: str, prompt_sha: str, model: str, input_sha: str) -> str:
        return sha256(canonical([judge, prompt_sha, model, input_sha]))

    def get(self, key: str) -> tuple[str, str] | None:
        if key in self._mem:
            return self._mem[key]
        if self.directory is not None:
            try:
                data = json.loads((self.directory / f"{key}.json").read_text())
                hit = (data["verdict"], data["reason"])
            except (OSError, ValueError, KeyError):
                return None
            if hit[0] in VERDICTS:
                self._mem[key] = hit
                return hit
        return None

    def put(self, key: str, verdict: str, reason: str) -> None:
        self._mem[key] = (verdict, reason)
        if self.directory is not None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / f"{key}.json").write_text(json.dumps({"verdict": verdict, "reason": reason}))
            except OSError:
                pass  # a cache that cannot be written only costs a repeat call


# --- calibration records -------------------------------------------------------------------------------------------


def record_path(judge: str, calibration_dir: Path = CALIBRATION_DIR) -> Path:
    return calibration_dir / f"{judge}.record.json"


def load_record(judge: str, calibration_dir: Path = CALIBRATION_DIR) -> dict[str, Any] | None:
    try:
        data = json.loads(record_path(judge, calibration_dir).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def gating_status(
    judge: str, model: str, *, prompts_dir: Path = PROMPTS_DIR, calibration_dir: Path = CALIBRATION_DIR
) -> tuple[bool, str]:
    """Whether `judge` with `model` and the current prompt may gate, and why not. A prompt or model change voids it."""
    rec = load_record(judge, calibration_dir)
    if rec is None:
        return False, "not calibrated: no calibration record"
    if rec.get("prompt_sha256") != prompt_hash(judge, prompts_dir):
        return False, "calibration is stale: the prompt changed since it was calibrated"
    if rec.get("model") != model:
        return False, f"calibration is stale: calibrated with {rec.get('model')}, running {model}"
    tpr, tnr = rec.get("tpr"), rec.get("tnr")
    threshold = float(rec.get("threshold", MIN_RATE))
    if not (isinstance(tpr, (int, float)) and isinstance(tnr, (int, float))):
        return False, "calibration record has no TPR/TNR"
    if tpr < max(threshold, MIN_RATE) or tnr < max(threshold, MIN_RATE):
        return False, f"calibration below {MIN_RATE}: TPR {tpr:.2f}, TNR {tnr:.2f}"
    if not rec.get("passed"):
        return False, "calibration record is not marked passed"
    return True, f"calibrated: TPR {tpr:.2f}, TNR {tnr:.2f}"


# --- the judge runner ----------------------------------------------------------------------------------------------


def price(model: str, reply: JudgeReply) -> float:
    if reply.cost_usd is not None:
        return max(0.0, float(reply.cost_usd))
    from app.config.llm_pricing import calculate_cost

    return calculate_cost(model, reply.input_tokens, reply.output_tokens)


@dataclass
class JudgeRunner:
    transport: JudgeTransport
    model: str
    cache: JudgeCache = field(default_factory=JudgeCache)
    meter: BudgetMeter | None = None
    prompts_dir: Path = PROMPTS_DIR
    calibration_dir: Path = CALIBRATION_DIR
    retries: int = 1  # one retry after a malformed reply or a transport error

    def __post_init__(self) -> None:
        if not self.model:
            raise JudgeConfigError(f"no judge model: pin one in {MODEL_VAR}")

    @classmethod
    def from_env(cls, transport: JudgeTransport | None = None, *, meter: BudgetMeter | None = None, **kw: Any) -> JudgeRunner:
        model = os.environ.get(MODEL_VAR, "").strip()
        if not model:
            raise JudgeConfigError(f"no judge model: set {MODEL_VAR} to a pinned model id")
        return cls(transport or llm_direct_transport(), model, meter=meter, **kw)

    async def judge(self, name: str, item: Mapping[str, Any]) -> Verdict:
        system = prompt_text(name, self.prompts_dir)
        user = render_user(name, item)  # raises on a missing field: the caller's bug, not a verdict
        key = JudgeCache.key(name, sha256(system), self.model, input_hash(name, item))
        if (hit := self.cache.get(key)) is not None:
            return Verdict(hit[0], hit[1], cached=True)
        call = JudgeCall(self.model, system, user)
        calls, spent, last = 0, 0.0, "no attempt"
        for _ in range(1 + max(0, self.retries)):
            if self.meter is not None and not self.meter.can_spend():
                return Verdict("error", "budget: the EVAL_MAX_USD cap is spent", calls=calls, cost_usd=spent)
            calls += 1
            try:
                reply = await self.transport(call)
            except Exception as e:  # noqa: BLE001 - a provider failure is an error verdict, never a pass
                last = f"transport error: {type(e).__name__}: {e}"
                continue
            cost = price(self.model, reply)
            spent += cost
            if self.meter is not None:
                self.meter.charge(cost)
            try:
                verdict, reason = parse_reply(reply.text)
            except ParseError as e:
                last = f"malformed reply: {e}"
                continue
            self.cache.put(key, verdict, reason)
            return Verdict(verdict, reason, calls=calls, cost_usd=spent)
        return Verdict("error", last, calls=calls, cost_usd=spent)

    def gating(self, name: str) -> tuple[bool, str]:
        return gating_status(name, self.model, prompts_dir=self.prompts_dir, calibration_dir=self.calibration_dir)

    async def grade(self, spec: GraderSpec, ctx: GradeContext) -> Outcome:
        """Run one `judge` grader: the judge named by `params.judge` over every item its extractor finds.

        Passes only if every item is `yes`; any `error` fails (and says so). Missing inputs fail closed.
        """
        params = dict(spec.params)
        name = str(params.get("judge", ""))
        if name not in JUDGE_FIELDS:
            return outcome(False, f"unknown judge '{name}' (known: {', '.join(JUDGE_FIELDS)})")
        try:
            items = EXTRACTORS[name](ctx, params)
        except MissingInput as e:
            return outcome(False, f"judge {name}: {e}")
        if not items:
            return outcome(False, f"judge {name}: nothing to judge")
        verdicts = [await self.judge(name, it) for it in items]
        yes = sum(v.verdict == "yes" for v in verdicts)
        errors = [v for v in verdicts if v.verdict == "error"]
        noes = [v for v in verdicts if v.verdict == "no"]
        if errors:
            detail = f"judge {name}: {len(errors)} of {len(verdicts)} errored ({errors[0].reason})"
        elif noes:
            detail = f"judge {name}: {len(noes)} of {len(verdicts)} no ({noes[0].reason})"
        else:
            detail = f"judge {name}: {yes} of {len(verdicts)} yes"
        return outcome(yes == len(verdicts), detail, score=yes / len(verdicts))

    async def grade_all(self, specs: Sequence[GraderSpec], ctx: GradeContext) -> list[GraderResult]:
        """Results for the `judge` specs in `specs`. A spec's `gating` is honoured only for a calibrated judge."""
        results: list[GraderResult] = []
        for spec in specs:
            if spec.type != "judge":
                continue
            try:
                out = await self.grade(spec, ctx)
            except Exception as e:  # noqa: BLE001 - a broken judge grader fails its own result, not the suite
                out = outcome(False, f"grader error: {e}")
            gating, why = self.gating(str(spec.params.get("judge", "")))
            detail = str(out["detail"])
            if spec.gating and not gating:
                detail += f" [advisory: {why}]"
            results.append(
                GraderResult(grader_id=spec.id, passed=bool(out["passed"]), gating=spec.gating and gating, detail=detail)
            )
        return results


# --- extractors: what each judge is asked about a trial ------------------------------------------------------------


class MissingInput(Exception):
    pass


Item = dict[str, str]
Extractor = Callable[[GradeContext, dict[str, Any]], list[Item]]
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _need(ctx: GradeContext, params: Mapping[str, Any], key: str) -> str:
    """A text input from the grader's params, else the context's files."""
    v = params.get(key)
    if v is None:
        v = ctx.files.get(key)
    if not isinstance(v, str) or not v.strip():
        raise MissingInput(f"needs '{key}' (a grader param or a collected file)")
    return v


def _learnings(ctx: GradeContext) -> list[dict[str, Any]]:
    v = ctx.files.get("learnings")
    if not isinstance(v, list):
        raise MissingInput("needs the collected file 'learnings'")
    return v


def _learning_text(l: Mapping[str, Any]) -> str:
    url = l.get("source_url")
    return f"{l.get('fact', '')}" + (f" (source: {url})" if url else "")


def final_assistant_text(ctx: GradeContext) -> str | None:
    """The root conversation's last assistant text, from the raw trace."""
    if ctx.raw is None:
        return None
    for e in reversed(ctx.raw.get("entries") or []):
        if e.get("kind") != "assistant":
            continue
        blocks = ((e.get("payload") or {}).get("message") or {}).get("blocks") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if text:
            return text
    return None


def first_user_text(ctx: GradeContext) -> str | None:
    for e in (ctx.raw or {}).get("entries") or []:
        if e.get("kind") == "user":
            c = (e.get("payload") or {}).get("content")
            if isinstance(c, str) and c.strip():
                return c
    return None


def extract_claim_supported(ctx: GradeContext, params: dict[str, Any]) -> list[Item]:
    """One item per cited sentence of `report.md`: the claim and the learnings of the sources it cites.

    `[N]` is parsed by production's citation parser and numbered by `build_sources_section` (`deep_work.numbered_sources`). A sentence citing a
    source no learning backs gets the evidence "(no learning for this citation)" so the judge answers `no`.
    """
    report = ctx.files.get("report.md")
    if not isinstance(report, str) or not report.strip():
        raise MissingInput("needs the collected file 'report.md'")
    learnings = _learnings(ctx)
    urls = numbered_sources(learnings)
    items: list[Item] = []
    for sentence in (s.strip() for s in _SENTENCE.split(report)):
        cited = cited_indexes(sentence)
        if not cited:
            continue
        evidence = [
            _learning_text(l)
            for n in cited
            if n in urls
            for l in learnings
            if str(l.get("source_url")) == urls[n]
        ]
        items.append({"claim": sentence, "evidence": "\n".join(evidence) or "(no learning for this citation)"})
    if not items:
        raise MissingInput("report.md has no cited sentences")
    return items[: int(params.get("max_items", MAX_ITEMS))]


def extract_plan_coverage(ctx: GradeContext, params: dict[str, Any]) -> list[Item]:
    request = params.get("request") or ctx.files.get("request") or first_user_text(ctx)
    if not isinstance(request, str) or not request.strip():
        raise MissingInput("needs 'request' (a grader param, a file, or the first user message)")
    return [{"request": request, "plan": _need(ctx, params, "plan")}]


def extract_no_unsourced_facts(ctx: GradeContext, params: dict[str, Any]) -> list[Item]:
    report = ctx.files.get("report.md")
    if not isinstance(report, str) or not report.strip():
        raise MissingInput("needs the collected file 'report.md'")
    evidence = "\n".join(_learning_text(l) for l in _learnings(ctx))
    if not evidence.strip():
        raise MissingInput("no learnings to check the report against")
    return [{"report": report, "evidence": evidence}]


def extract_memory_applied(ctx: GradeContext, params: dict[str, Any]) -> list[Item]:
    request = params.get("request") or ctx.files.get("request") or first_user_text(ctx)
    response = params.get("response") or ctx.files.get("response") or final_assistant_text(ctx)
    if not isinstance(request, str) or not request.strip():
        raise MissingInput("needs 'request'")
    if not isinstance(response, str) or not response.strip():
        raise MissingInput("needs 'response' (a file or an assistant message in the trace)")
    return [{"memory": _need(ctx, params, "memory"), "request": request, "response": response}]


def extract_recall_probe(ctx: GradeContext, params: dict[str, Any]) -> list[Item]:
    answer = params.get("answer") or ctx.files.get("probe_answer") or final_assistant_text(ctx)
    if not isinstance(answer, str) or not answer.strip():
        raise MissingInput("needs 'answer' (a file 'probe_answer' or an assistant message in the trace)")
    return [{"fact": _need(ctx, params, "fact"), "question": _need(ctx, params, "question"), "answer": answer}]


EXTRACTORS: dict[str, Extractor] = {
    "claim_supported": extract_claim_supported,
    "plan_coverage": extract_plan_coverage,
    "no_unsourced_facts": extract_no_unsourced_facts,
    "memory_applied": extract_memory_applied,
    "recall_probe": extract_recall_probe,
}


# --- the real transport --------------------------------------------------------------------------------------------


def llm_direct_transport(api_key: str | None = None, *, api_base: str = OPENROUTER_BASE) -> JudgeTransport:
    """Judge calls through `app.services.llm_direct` (OpenRouter), temperature 0. The key never leaves this closure."""
    key = api_key or next((v for n in KEY_VARS if (v := os.environ.get(n, "").strip())), "")
    if not key:
        raise JudgeConfigError(f"no judge key: set {' or '.join(KEY_VARS)}")

    async def call(c: JudgeCall) -> JudgeReply:
        from app.services import llm_direct

        provider = llm_direct.DirectProvider(provider_type="openrouter", api_key=key, api_base=api_base)
        request = llm_direct.DirectRequest(
            messages=[{"role": "system", "content": c.system}, {"role": "user", "content": c.user}],
            model=llm_direct.resolve_model(c.model, "openrouter"),
            temperature=0.0,
            max_tokens=MAX_REPLY_TOKENS,
        )
        r = await llm_direct.complete(request, provider)
        return JudgeReply(r.content or "", r.input_tokens, r.output_tokens)

    return call
