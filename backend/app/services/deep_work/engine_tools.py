"""Deep Work gateway tools for the engine path (deep-work-on-engine plan, U4, KTD6, KTD7).

The orchestrator drives the research loop; these tools persist its state and make skipping a step an
error result it can read and correct. Run state lives in ``deep_work_runs`` (via
``app.services.deep_work_state``, one tenant-scoped session per call, R17); workspace files live in
``SessionStorage`` under ``workspace/`` of the session ``deep_work_runs.id`` and are replaced by path,
so replaying a call under the same idempotency key leaves one file state and one DB state.

The root conversation id arrives as ``ctx.metadata["conversation_id"]`` (U3), whichever child
conversation (worker, reflector) made the call.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill
from app.config import get_settings
from app.core.database import get_db_for_tenant
from app.internal_api.execution import ToolOutcome
from app.services import deep_work_state as dws
from app.services import session_storage as _session_storage
from app.services.deep_work.models import Learning
from app.services.deep_work.synthesis import build_sources_section, select_learnings

logger = logging.getLogger(__name__)

WORKER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SUB_QUERY_CHARS = 2000
MAX_SECTION_CHARS = 100_000
MAX_REFLECTION_ITEMS = 20
RESERVED_HEADING = "sources"
_FENCE = re.compile(r"^\s*(```|~~~)")


# --- plumbing -----------------------------------------------------------------


def state_factory(tenant_id: Any) -> dws.SessionFactory:
    """A factory of tenant-scoped sessions; each tool call opens its own (R17)."""
    return lambda: get_db_for_tenant(str(tenant_id))


def _storage():
    return _session_storage.get_session_storage()


def _err(message: str, code: str = "rejected", **details: Any) -> ToolOutcome:
    return ToolOutcome(content=message, is_error=True, details={"error": code, **details})


def _ok(payload: Dict[str, Any], workspace: Optional[List[str]] = None, **details: Any) -> ToolOutcome:
    if workspace:
        details["workspace"] = workspace
    return ToolOutcome(content=json.dumps(payload, ensure_ascii=False), is_error=False, details=details)


def _scope(ctx: SkillContext) -> Tuple[Any, Any, dws.SessionFactory]:
    tenant_id = ctx.tenant_id
    conversation_id = ctx.metadata.get("conversation_id")
    if not tenant_id or not conversation_id:
        raise ValueError("Deep Work tools need a tenant and a bound conversation")
    return tenant_id, conversation_id, state_factory(tenant_id)


_NO_RUN = "No Deep Work run exists for this conversation. It is created when the session is started."


def _refusal(run: Optional[dws.RunSnapshot]) -> Optional[ToolOutcome]:
    """The error result for a missing or finished run; ``None`` when the run takes writes."""
    if run is None:
        return _err(_NO_RUN, "no_run")
    if run.status != "running":
        return _err(f"This Deep Work run is {run.status}; it accepts no more changes.", "run_terminal", status=run.status)
    return None


async def _write(run: dws.RunSnapshot, path: str, content: str) -> str:
    await _storage().write(str(run.tenant_id), str(run.id), f"workspace/{path}", content)
    return path


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


async def _teardown_sandbox(run_id: Any, worker_id: str) -> None:
    """Best-effort end of a worker's sandbox (U5, KTD10); never fails the tool call."""
    try:
        from app.services.deep_work import engine_sandbox

        await engine_sandbox.teardown_worker(run_id, worker_id)
    except Exception:
        logger.warning("Sandbox teardown hook failed for %s:%s", run_id, worker_id, exc_info=True)


# --- run-state views ----------------------------------------------------------


def _round_entry(run: dws.RunSnapshot, round_no: int) -> Optional[Dict[str, Any]]:
    return next((r for r in run.rounds if r.get("round") == round_no), None)


def _registered(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(entry.get("registered", []))


def _latest_round(run: dws.RunSnapshot) -> int:
    return max((r.get("round", 0) for r in run.rounds if r.get("registered")), default=0)


def _open_workers(entry: Dict[str, Any]) -> List[str]:
    """Registered workers of a round that neither saved learnings nor were marked failed."""
    done = set(entry.get("workers", {})) | set(entry.get("failed", []))
    return [w["worker_id"] for w in _registered(entry) if w["worker_id"] not in done]


def _find_worker(run: dws.RunSnapshot, worker_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for entry in run.rounds:
        for w in _registered(entry):
            if w["worker_id"] == worker_id:
                return entry, w
    return None


def _all_learnings(run: dws.RunSnapshot, worker_ids: Optional[List[str]] = None) -> List[Learning]:
    """Saved learnings in round order, then registration order, then the worker's own order."""
    out: List[Learning] = []
    for entry in sorted(run.rounds, key=lambda r: r.get("round", 0)):
        saved = entry.get("workers", {})
        order = [w["worker_id"] for w in _registered(entry)]
        order += [w for w in saved if w not in order]
        for wid in order:
            if worker_ids is not None and wid not in worker_ids:
                continue
            out.extend(Learning.model_validate(item) for item in saved.get(wid, []))
    return out


def _plan_dict(run: dws.RunSnapshot) -> Dict[str, Any]:
    return run.plan if isinstance(run.plan, dict) else {}


def _title(run: dws.RunSnapshot) -> str:
    return str(_plan_dict(run).get("title") or "").strip()


def _render_plan(run: dws.RunSnapshot) -> str:
    plan = _plan_dict(run)
    lines = [f"# {_title(run) or 'Research plan'}", "", f"**Query:** {run.enriched_query or run.query}"]
    if plan.get("deliverable"):
        lines += ["", f"**Deliverable:** {plan['deliverable']}" + (
            f" - {plan['deliverable_description']}" if plan.get("deliverable_description") else "")]
    for entry in sorted(run.rounds, key=lambda r: r.get("round", 0)):
        if not entry.get("registered"):
            continue
        lines += ["", f"## Round {entry['round']}"]
        lines += [f"- `{w['worker_id']}`: {w['sub_query']}" for w in _registered(entry)]
    return "\n".join(lines) + "\n"


def _render_todo(run: dws.RunSnapshot) -> str:
    lines = ["# Todo", ""]
    for entry in sorted(run.rounds, key=lambda r: r.get("round", 0)):
        saved, failed = entry.get("workers", {}), set(entry.get("failed", []))
        for w in _registered(entry):
            wid = w["worker_id"]
            mark = "x" if wid in saved else " "
            lines.append(f"- [{mark}] (round {entry['round']}) {wid}: {w['sub_query']}" + (" - failed" if wid in failed and wid not in saved else ""))
    if len(lines) == 2:
        lines.append("- [ ] Register the first research round")
    return "\n".join(lines) + "\n"


# --- report sections ----------------------------------------------------------


def _h2_lines(markdown: str) -> List[Tuple[int, str]]:
    """Level-2 heading lines outside code fences: (line index, heading text)."""
    found, fenced = [], False
    for i, line in enumerate(markdown.splitlines()):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            found.append((i, line[3:].strip()))
    return found


def _parse_sections(report: str) -> List[Tuple[str, str]]:
    lines = report.splitlines()
    marks = _h2_lines(report)
    out = []
    for n, (i, heading) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        out.append((heading, "\n".join(lines[i + 1:end]).strip()))
    return out


def _render_report(title: str, sections: List[Tuple[str, str]]) -> str:
    parts = [f"# {title}\n"] if title else []
    parts += [f"## {heading}\n\n{body}\n" for heading, body in sections]
    return "\n".join(parts)


def _normalize_heading(heading: str) -> str:
    return " ".join(heading.strip().lstrip("#").split())


# --- tools --------------------------------------------------------------------


@skill(
    name="dw_start_round",
    description=(
        "Register a research round before launching its workers. Rounds are numbered consecutively from 1; "
        "each has at most the configured number of workers. Writes plan.md and todo.md. Repeating a round "
        "that is already registered changes nothing."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_start_round(ctx: SkillContext, round: int, workers: List[Dict[str, Any]]) -> ToolOutcome:
    """
    Register the workers of a research round.

    Args:
        round: Round number, 1 for the first round, then consecutive (gap-filling rounds follow reflection).
        workers: Workers of this round, each {"worker_id": short id, "sub_query": the question, "task_type": optional}.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    settings = get_settings()
    run = await dws.load_run(factory, tenant_id, conv_id)
    if (refusal := _refusal(run)) is not None:
        return refusal

    clean: List[Dict[str, Any]] = []
    for w in workers or []:
        wid, query = (w.get("worker_id") if isinstance(w, dict) else None), (w.get("sub_query") if isinstance(w, dict) else None)
        if not isinstance(wid, str) or not WORKER_ID.match(wid):
            return _err(f"Invalid worker_id {wid!r}: use 1-64 letters, digits, '-' or '_'.", "invalid_worker")
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_SUB_QUERY_CHARS:
            return _err(f"Worker {wid} needs a non-empty sub_query of at most {MAX_SUB_QUERY_CHARS} characters.", "invalid_worker")
        item = {"worker_id": wid, "sub_query": query.strip()}
        if w.get("task_type"):
            item["task_type"] = str(w["task_type"])[:40]
        clean.append(item)
    if not clean:
        return _err("A round needs at least one worker.", "invalid_worker")
    ids = [w["worker_id"] for w in clean]
    if len(set(ids)) != len(ids):
        return _err("Worker ids must be unique within a round.", "duplicate_worker")

    existing = _round_entry(run, round)
    if existing is not None and existing.get("registered"):
        files = await _write_plan_files(run)
        return _ok(
            {"round": round, "workers": _registered(existing), "already_registered": True},
            files, replay=True,
        )

    latest = _latest_round(run)
    if round != latest + 1:
        return _err(f"Round {round} is out of order: the next round is {latest + 1}.", "round_order", next_round=latest + 1)
    max_rounds = settings.DEEP_WORK_MAX_REFLECTION_ROUNDS + 1
    if round > max_rounds:
        return _err(
            f"Round {round} exceeds the reflection cap ({settings.DEEP_WORK_MAX_REFLECTION_ROUNDS} reflection rounds, "
            f"{max_rounds} research rounds in all); proceed to synthesis.", "round_cap", max_rounds=max_rounds,
        )
    started = _aware(run.created_at)
    if started and (datetime.now(timezone.utc) - started).total_seconds() > settings.DEEP_WORK_MAX_DURATION_SEC:
        return _err(
            f"The run's time budget ({settings.DEEP_WORK_MAX_DURATION_SEC}s) is spent; proceed to synthesis.", "deadline",
        )
    if len(clean) > settings.DEEP_WORK_MAX_WORKERS:
        return _err(
            f"{len(clean)} workers requested but a round allows at most {settings.DEEP_WORK_MAX_WORKERS}: "
            f"drop or merge {len(clean) - settings.DEEP_WORK_MAX_WORKERS} sub-queries and call again.",
            "worker_cap", max_workers=settings.DEEP_WORK_MAX_WORKERS, requested=len(clean),
        )
    taken = {w["worker_id"] for entry in run.rounds for w in _registered(entry)}
    if reused := sorted(set(ids) & taken):
        return _err(f"Worker ids already used in an earlier round: {', '.join(reused)}. Use new ids.", "duplicate_worker")

    run = await dws.register_round(factory, tenant_id, conv_id, round=round, workers=clean)
    if (refusal := _refusal(run)) is not None:
        return refusal
    files = await _write_plan_files(run)
    return _ok({"round": round, "workers": _registered(_round_entry(run, round) or {})}, files)


async def _write_plan_files(run: dws.RunSnapshot) -> List[str]:
    return [await _write(run, "plan.md", _render_plan(run)), await _write(run, "todo.md", _render_todo(run))]


@skill(
    name="dw_save_learnings",
    description=(
        "Save a research worker's learnings (facts with source URLs). Replaces anything that worker saved "
        "before, so saving twice is safe. Call once, when the worker is done."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_save_learnings(ctx: SkillContext, worker_id: str, learnings: List[Dict[str, Any]]) -> ToolOutcome:
    """
    Save the learnings of one worker.

    Args:
        worker_id: The id this worker was registered with in dw_start_round.
        learnings: Findings, each {"fact", "source_url", "confidence" (0-1), "source_title", "source_type", "quote"}.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    cap = get_settings().DEEP_WORK_MAX_LEARNINGS_PER_WORKER
    run = await dws.load_run(factory, tenant_id, conv_id)
    if (refusal := _refusal(run)) is not None:
        return refusal
    found = _find_worker(run, worker_id)
    if found is None:
        return _err(
            f"Worker {worker_id!r} is not registered. Register its round with dw_start_round first.", "unknown_worker",
        )
    entry, worker = found

    parsed: List[Learning] = []
    for i, item in enumerate(learnings or []):
        try:
            parsed.append(Learning.model_validate({"sub_query": worker["sub_query"], **item}))
        except (ValidationError, TypeError) as exc:
            problem = exc.errors()[0] if isinstance(exc, ValidationError) else {"loc": (), "msg": str(exc)}
            where = ".".join(str(p) for p in problem["loc"])
            return _err(f"Learning {i} is invalid ({where}: {problem['msg']}). Fix it and save again.", "invalid_learning", index=i)
    details: Dict[str, Any] = {}
    if len(parsed) > cap:
        details = {"truncated": True, "original": len(parsed), "kept": cap}
        parsed = parsed[:cap]

    stored = [l.model_dump() for l in parsed]
    path = await _write(
        run, f"learnings/{worker_id}.json",
        json.dumps({"worker_id": worker_id, "round": entry["round"], "learnings": stored}, ensure_ascii=False, indent=2),
    )
    run = await dws.save_worker_learnings(
        factory, tenant_id, conv_id, round=entry["round"], worker_id=worker_id, learnings=stored,
    )
    if (refusal := _refusal(run)) is not None:
        return refusal
    files = [path, await _write(run, "todo.md", _render_todo(run))]
    await _teardown_sandbox(run.id, worker_id)  # U5: the worker's sandbox and proxy token end with its learnings
    remaining = _open_workers(_round_entry(run, entry["round"]) or {})
    payload = {
        "worker_id": worker_id, "round": entry["round"], "learning_count": len(stored),
        "source_count": len({l.source_url for l in parsed if l.source_url}),
        "workers_still_open": remaining,
    }
    if details:
        payload["note"] = f"Only the first {cap} learnings were kept."
    return _ok(payload, files, **details)


@skill(
    name="dw_record_reflection",
    description=(
        "Record the reflection on a finished research round: score, gaps and whether to run another round. "
        "Optionally list workers of the round that failed and will never save learnings. Replaces an earlier "
        "reflection for the same round."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_record_reflection(
    ctx: SkillContext,
    round: int,
    score: int,
    gaps: List[str],
    should_continue: bool,
    gap_tasks: Optional[List[Dict[str, Any]]] = None,
    failed_workers: Optional[List[str]] = None,
) -> ToolOutcome:
    """
    Record a reflection.

    Args:
        round: The research round being reflected on.
        score: Completeness 0-100.
        gaps: What is still missing.
        should_continue: True to run a gap-filling round.
        gap_tasks: Proposed workers for that round, each {"worker_id", "sub_query"}.
        failed_workers: Worker ids of this round that failed without saving learnings.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    cap = get_settings().DEEP_WORK_MAX_REFLECTION_ROUNDS
    run = await dws.load_run(factory, tenant_id, conv_id)
    if (refusal := _refusal(run)) is not None:
        return refusal
    entry = _round_entry(run, round)
    if entry is None or not entry.get("registered"):
        return _err(f"Round {round} is not registered; reflect only on rounds started with dw_start_round.", "unknown_round")
    if round > cap:
        return _err(
            f"Round {round} exceeds the reflection cap ({cap} reflection rounds); no reflection is recorded. "
            "Proceed to synthesis.", "reflection_cap", cap=cap,
        )
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        return _err("score must be an integer from 0 to 100.", "invalid_reflection")
    gaps = [str(g)[:500] for g in (gaps or [])][:MAX_REFLECTION_ITEMS]
    tasks = []
    for t in (gap_tasks or [])[:MAX_REFLECTION_ITEMS]:
        if not isinstance(t, dict) or not isinstance(t.get("sub_query"), str):
            return _err("Each gap task needs a sub_query.", "invalid_reflection")
        tasks.append({k: str(v)[:MAX_SUB_QUERY_CHARS] for k, v in t.items() if k in ("worker_id", "sub_query", "task_type")})
    failed = sorted({str(w) for w in (failed_workers or [])})
    known = {w["worker_id"] for w in _registered(entry)}
    if unknown := [w for w in failed if w not in known]:
        return _err(f"Failed workers not in round {round}: {', '.join(unknown)}.", "unknown_worker")

    if failed:
        run = await dws.mark_workers_failed(factory, tenant_id, conv_id, round=round, worker_ids=failed)
    reflection = {"round": round, "score": score, "gaps": gaps, "should_continue": bool(should_continue), "gap_tasks": tasks}
    run = await dws.record_reflection(factory, tenant_id, conv_id, round=round, reflection=reflection)
    if (refusal := _refusal(run)) is not None:
        return refusal
    can_continue = bool(should_continue) and round < cap + 1
    return _ok(
        {"round": round, "recorded": True, "next": "dw_start_round" if can_continue else "synthesis",
         "workers_still_open": _open_workers(_round_entry(run, round) or {})},
    )


@skill(
    name="dw_write_section",
    description=(
        "Write one section of the report. Replaces the section with the same heading, otherwise appends it, so "
        "repeating a call is safe. Allowed once every worker of the latest round has saved learnings or been "
        "marked failed. Do not write a Sources section; it is built from the saved learnings."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_write_section(ctx: SkillContext, heading: str, markdown: str) -> ToolOutcome:
    """
    Write or replace a report section.

    Args:
        heading: Section heading text (rendered as a level-2 heading).
        markdown: Section body; use level-3 or deeper headings inside it.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    heading = _normalize_heading(heading or "")
    if not heading:
        return _err("A section needs a heading.", "invalid_section")
    if heading.lower() == RESERVED_HEADING:
        return _err("The Sources section is built from the saved learnings by dw_finalize; do not write it.", "reserved_heading")
    if not (markdown or "").strip():
        return _err("A section needs a body.", "invalid_section")
    if len(markdown) > MAX_SECTION_CHARS:
        return _err(f"A section body is limited to {MAX_SECTION_CHARS} characters; split it into sections.", "invalid_section")
    if _h2_lines(markdown):
        return _err("A section body must not contain level-2 headings; use '###' inside a section.", "invalid_section")

    async with dws.run_lock(factory, tenant_id, conv_id) as locked:
        if locked is None or (refusal := _refusal(locked.run)) is not None:
            return refusal if locked is not None else _err(_NO_RUN, "no_run")
        run = locked.run
        latest = _latest_round(run)
        if latest == 0:
            return _err("No research round has been registered. Start with dw_start_round.", "no_research")
        if open_ids := _open_workers(_round_entry(run, latest)):
            return _err(
                f"Round {latest} is still open: workers {', '.join(open_ids)} have not saved learnings. Wait for them, "
                "or report a failed worker through dw_record_reflection (failed_workers), then write sections.",
                "round_open", open_workers=open_ids,
            )
        sections = _parse_sections(await _storage().read(str(run.tenant_id), str(run.id), "workspace/report.md"))
        sections = [(h, b) for h, b in sections if h.lower() != RESERVED_HEADING]
        replaced = any(h == heading for h, _ in sections)
        sections = [(h, markdown.strip() if h == heading else b) for h, b in sections]
        if not replaced:
            sections.append((heading, markdown.strip()))
        path = await _write(run, "report.md", _render_report(_title(run), sections))
        locked.advance(phase="synthesize")
    return _ok({"heading": heading, "replaced": replaced, "sections": [h for h, _ in sections]}, [path])


@skill(
    name="dw_finalize",
    description=(
        "Finish the report: appends the Sources block built from every saved learning (deduplicated), writes "
        "report.md and marks the run completed. Needs at least one section and no open workers. Calling it "
        "again returns the stored result."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_finalize(ctx: SkillContext, failed_workers: Optional[List[str]] = None) -> ToolOutcome:
    """
    Complete the run.

    Args:
        failed_workers: Worker ids of the latest round that failed without saving learnings, if any.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    run = await dws.load_run(factory, tenant_id, conv_id)
    if run is None:
        return _err(_NO_RUN, "no_run")
    if run.status == "completed":
        report = await _storage().read(str(run.tenant_id), str(run.id), "workspace/report.md")
        return _ok({"report_length": len(report), "artifacts": run.artifacts, "already_completed": True}, ["report.md"], replay=True)
    if (refusal := _refusal(run)) is not None:
        return refusal

    latest = _latest_round(run)
    if latest and failed_workers:
        entry = _round_entry(run, latest)
        known = {w["worker_id"] for w in _registered(entry)}
        if unknown := sorted({str(w) for w in failed_workers} - known):
            return _err(f"Failed workers not in round {latest}: {', '.join(unknown)}.", "unknown_worker")
        await dws.mark_workers_failed(factory, tenant_id, conv_id, round=latest, worker_ids=[str(w) for w in failed_workers])

    async with dws.run_lock(factory, tenant_id, conv_id) as locked:
        if locked is None or (refusal := _refusal(locked.run)) is not None:
            return refusal if locked is not None else _err(_NO_RUN, "no_run")
        run = locked.run
        latest = _latest_round(run)
        if latest == 0:
            return _err("No research round has been registered. Start with dw_start_round.", "no_research")
        if open_ids := _open_workers(_round_entry(run, latest)):
            return _err(
                f"Round {latest} is still open: workers {', '.join(open_ids)} have not saved learnings. Wait for them or "
                "pass them in failed_workers.", "round_open", open_workers=open_ids,
            )
        sections = [
            (h, b) for h, b in _parse_sections(await _storage().read(str(run.tenant_id), str(run.id), "workspace/report.md"))
            if h.lower() != RESERVED_HEADING
        ]
        if not sections:
            return _err("The report has no sections. Write them with dw_write_section, then finalize.", "no_sections")
        body = _render_report(_title(run), sections).rstrip()
        report = body + "\n" + build_sources_section(_all_learnings(run)) + "\n"
        path = await _write(run, "report.md", report)
        locked.advance(phase="done", status="completed")
        artifacts = locked.run.artifacts
    await _teardown_sandbox(run.id, "output_agent")  # U5: nothing outlives the run
    return _ok({"report_length": len(report), "artifacts": artifacts}, [path])


@skill(
    name="dw_get_learnings",
    description=(
        "Read the saved learnings, best-supported first, de-duplicated and limited to a token budget. "
        "Optionally only those of the given workers."
    ),
    category="deep_work",
    author="oniva",
)
async def dw_get_learnings(
    ctx: SkillContext, worker_ids: Optional[List[str]] = None, max_tokens: int = 15000,
) -> ToolOutcome:
    """
    Read saved learnings.

    Args:
        worker_ids: Only these workers' learnings; all workers when omitted.
        max_tokens: Approximate token budget for the returned learnings.
    """
    tenant_id, conv_id, factory = _scope(ctx)
    run = await dws.load_run(factory, tenant_id, conv_id)
    if run is None:
        return _err(_NO_RUN, "no_run")
    everything = _all_learnings(run, worker_ids)
    selected = select_learnings(everything, max_tokens=max(1000, min(int(max_tokens), 100_000)))
    return _ok({
        "total": len(everything), "returned": len(selected),
        "learnings": [l.model_dump(exclude_none=True) for l in selected],
    })


@skill(
    name="dw_get_report",
    description="Read the report as written so far (all sections, plus the Sources block once finalized).",
    category="deep_work",
    author="oniva",
)
async def dw_get_report(ctx: SkillContext) -> ToolOutcome:
    """Read report.md."""
    tenant_id, conv_id, factory = _scope(ctx)
    run = await dws.load_run(factory, tenant_id, conv_id)
    if run is None:
        return _err(_NO_RUN, "no_run")
    report = await _storage().read(str(run.tenant_id), str(run.id), "workspace/report.md")
    if not report:
        return _err("No report sections have been written yet.", "no_report")
    return _ok({"report": report, "length": len(report), "completed": run.status == "completed"})
