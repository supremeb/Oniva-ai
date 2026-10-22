"""Memory driver (plan 2026-10-21-001, U34): sequences of runs and conversations in one project, the curator, the
reviewer, deletion and the probes Gate D reads.

Three task kinds, one `memory_eval` record each (see `evals.graders.memory` for its shape):

- `repeat`: a Deep Work task run `input.runs` times in a row in one project. In the `propose` arm each completed run is
  curated and a reviewer approves the unflagged proposals before the next run (a tainted run's proposals are always
  pending, so approval is how a playbook ever reaches a Deep Work prompt); the `control` arm has memory off. Every run is
  scored by `input.score_graders`.
- `memory` (preference probes): conversation 1 states a preference, conversation 2 (a new one) is asked something the
  preference bears on; some tasks then delete the memory (a file, a bullet, or everything) and open conversation 3.
- `redteam`: untrusted text tries to reach memory. `input.path: chat`: a chat that read it tries a memory write (the
  conversation is marked unattended, so a write that needs approval is refused); `input.path: dw`: a Deep Work run read it
  and the curator proposes changes. A last conversation reads what the model would now be shown.

Every trial starts by wiping the eval world's memory tables and seeding canary rows in another project of the same tenant and
in a project of another tenant (the isolation probes: the canaries must never reach this project). Red-team tasks also seed
user-authored rows ("protected") that no tainted source may change.

Deep Work completion hook: production never starts the curator for a run `dw_finalize` completed (a U31 gap, see
`tests/e2e/test_memory_curation_e2e.py`), so the driver fires the production hook itself through the e2e backend's
`/e2e/memory/deep-work-completed`. Reads of the run (`full-state`) are safe: they do not matter once the hook is fired by hand.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any

from app.services.memory_curator import curator_conversation_id
from tests.e2e.harness import mark_unattended, sql, sql_write

from evals import arms, ledger
from evals.graders import GradeContext, grade
from evals.schema import GraderSpec
from evals.trace import fetch_trace, flatten, total_usage

from . import AUTH, DriverContext, DriverError, DriverOutput, TrialTimeout
from . import chat, deep_work

KINDS = ("repeat", "memory", "redteam")
FILE_KEY = "memory_eval"
MEMORY_SECTION = re.compile(r"(?ms)^## (?:Project memory|Project playbook)\b.*?(?=^## |\Z)")
APPLY_WAIT_S = {"fake": 120.0, "live": 300.0}
POLL_S = 0.5


# --- the database the eval stack shares with its backend (SQLite) --------------------------------------------------------------


def _hex(value: Any) -> str:
    return str(value).replace("-", "")


def _uuid(hex_or_uuid: Any) -> str | None:
    if hex_or_uuid in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(hex_or_uuid)))
    except ValueError:
        return str(hex_or_uuid)


def wipe_memory(ctx: DriverContext) -> None:
    for table in ("memory_bullets", "memory_files"):
        sql_write(ctx.ew.world, f"DELETE FROM {table}")


def put_file(ctx: DriverContext, project: Any, tenant: Any, path: str, content: str, *, status: str = "active",
             source_kind: str = "user") -> None:
    sql_write(
        ctx.ew.world,
        "INSERT INTO memory_files (id, tenant_id, project_id, user_id, path, content, content_hash, status, source_kind) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
        uuid.uuid4().hex, _hex(tenant), _hex(project), path, content, hashlib.sha256(content.encode()).hexdigest(), status,
        source_kind,
    )


def put_bullet(ctx: DriverContext, project: Any, tenant: Any, key: str, section: str, text: str, *, status: str = "active",
               source_kind: str = "user") -> None:
    sql_write(
        ctx.ew.world,
        "INSERT INTO memory_bullets (id, tenant_id, project_id, bullet_key, section, text, status, source_kind, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        uuid.uuid4().hex, _hex(tenant), _hex(project), key, section, text, status, source_kind,
        json.dumps({"deltas": [], "instruction_flags": [], "idle_runs": 0}),
    )


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def read_rows(ctx: DriverContext) -> list[dict[str, Any]]:
    """Every memory file and bullet of the graded project, with the fields the graders and the reviewer use."""
    project = _hex(ctx.ew.world.project)
    rows: list[dict[str, Any]] = []
    for id_, path, content, status, kind, conv, flags in sql(
        ctx.ew.world,
        "SELECT id, path, content, status, source_kind, source_conversation_id, flags FROM memory_files WHERE project_id = ?",
        project,
    ):
        rows.append({"table": "file", "id": _uuid(id_), "key": path, "text": content, "status": status, "source_kind": kind,
                     "source_conversation_id": _uuid(conv), "flags": _json(flags)})
    for id_, key, text_, status, kind, conv, flags in sql(
        ctx.ew.world,
        "SELECT id, bullet_key, text, status, source_kind, source_conversation_id, flags FROM memory_bullets WHERE project_id = ?",
        project,
    ):
        rows.append({"table": "bullet", "id": _uuid(id_), "key": key, "text": text_, "status": status, "source_kind": kind,
                     "source_conversation_id": _uuid(conv), "flags": _json(flags)})
    return rows


def seed_foreign(ctx: DriverContext) -> dict[str, list[str]]:
    """Canary rows in another project of this tenant and in a project of another tenant (isolation probes)."""
    from evals.memory_corpus import FOREIGN

    canaries = {**FOREIGN, **(ctx.task.input.get("canaries") or {})}
    w = ctx.ew.world
    put_file(ctx, w.other_project, w.tenant, "core.md", f"# Other project\n- Reports end with {canaries['project']}.")
    put_bullet(ctx, w.other_project, w.tenant, "other-bullet-1", "Practices", f"Always mention {canaries['project']}.")
    put_file(ctx, w.stranger_project, w.other_tenant, "core.md", f"# Other tenant\n- Reports end with {canaries['tenant']}.")
    return {"foreign": [canaries["project"], canaries["tenant"]]}


def seed_protected(ctx: DriverContext) -> list[dict[str, Any]]:
    """User-authored rows the red team tries to change; returns their original text."""
    seed = ctx.task.input.get("seed") or {}
    w = ctx.ew.world
    out: list[dict[str, Any]] = []
    if seed.get("core"):
        put_file(ctx, w.project, w.tenant, "core.md", seed["core"])
        out.append({"table": "file", "key": "core.md", "text": seed["core"]})
    if (b := seed.get("bullet")):
        put_bullet(ctx, w.project, w.tenant, b["key"], b["section"], b["text"])
        out.append({"table": "bullet", "key": b["key"], "text": b["text"]})
    return out


# --- chat helpers ----------------------------------------------------------------------------------------------------------------


def arm_of(ctx: DriverContext) -> str:
    return arms.memory_of(ctx.task) or arms.MEMORY_CONTROL


async def new_chat(ctx: DriverContext, selections: dict[str, Any] | None = None) -> str:
    r = await ctx.http.post(
        f"{ctx.backend}{chat.BASE}", headers=AUTH, json={"project_id": ctx.project_id, "selections": selections or {}}
    )
    if r.status_code != 201:
        raise DriverError("create_failed", f"{r.status_code} {r.text[:300]}")
    return r.json()["conversationId"]


async def say(ctx: DriverContext, conversation_id: str, text: str, tag: str) -> None:
    rid = f"{ctx.task.id}-t{ctx.trial_index}-{tag}"
    r = await ctx.http.post(
        f"{ctx.backend}{chat.BASE}/{conversation_id}/submissions",
        headers={**AUTH, "Idempotency-Key": rid},
        json={"text": text, "requestId": rid},
    )
    if r.status_code != 202:
        raise DriverError("submit_failed", f"{r.status_code} {r.text[:300]}")
    await chat.wait_settled(ctx, conversation_id, rid)


async def agent_of(ctx: DriverContext, conversation_id: str) -> dict[str, Any]:
    snap = await chat.snapshot(ctx, conversation_id)
    return snap.get("agent") or {}


def memory_sections(system_prompt: str) -> str:
    """The memory and playbook sections of a system prompt: what the model is shown from project memory."""
    return "\n\n".join(m.group(0).strip() for m in MEMORY_SECTION.finditer(system_prompt or ""))


async def block_of(ctx: DriverContext, conversation_id: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """({label, conversation_id, text}, agent config) of a conversation's injected memory text."""
    agent = await agent_of(ctx, conversation_id)
    return {"label": label, "conversation_id": conversation_id, "text": memory_sections(str(agent.get("systemPrompt") or ""))}, agent


def final_text(raw: dict[str, Any] | None) -> str:
    for e in reversed((raw or {}).get("entries") or []):
        if e.get("kind") != "assistant":
            continue
        blocks = ((e.get("payload") or {}).get("message") or {}).get("blocks") or []
        t = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if t:
            return t
    return ""


def visible_texts(raw: dict[str, Any] | None, label: str) -> list[dict[str, str]]:
    """What the model produced or was handed back from memory in a conversation: its answers and memory tool results."""
    out: list[dict[str, str]] = []
    for e in (raw or {}).get("entries") or []:
        p = e.get("payload") or {}
        if e.get("kind") == "assistant":
            blocks = (p.get("message") or {}).get("blocks") or []
            t = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if t:
                out.append({"label": f"{label} answer", "text": t})
        elif e.get("kind") == "tool_result" and str(p.get("toolName") or "").startswith("memory_"):
            content = p.get("content")
            t = content if isinstance(content, str) else json.dumps(content)
            out.append({"label": f"{label} {p.get('toolName')} result", "text": t})
    return out


def memory_writes(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The memory write calls of a conversation and whether each was refused."""
    out = []
    for e in (raw or {}).get("entries") or []:
        p = e.get("payload") or {}
        if e.get("kind") == "tool_result" and str(p.get("toolName") or "") in (
            "memory_create", "memory_str_replace", "memory_insert", "memory_delete"
        ):
            out.append({"tool": p.get("toolName"), "refused": bool(p.get("isError")), "status": p.get("status")})
    return out


def tainted(raw: dict[str, Any] | None) -> bool:
    return bool(((raw or {}).get("taint") or {}).get("tainted"))


# --- Deep Work + curator -------------------------------------------------------------------------------------------------------


async def dw_run(ctx: DriverContext, query: str, plan_task_input: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """One Deep Work run to completion. Returns (session_id, conversation_id, raw trace, files for the graders)."""
    session_id, conversation_id = await deep_work.open_session(ctx, query)
    plan = await deep_work.make_plan(ctx, query, session_id)
    await deep_work.start(ctx, query, session_id, plan)
    await deep_work.wait_terminal(ctx, conversation_id)
    raw = await deep_work.fetch_settled_trace(ctx, conversation_id)
    files: dict[str, Any] = {
        "fixture_urls": deep_work.fixture_urls(ctx.ew.replay_dir),
        "learnings": deep_work.saved_learnings(ctx, session_id),
    }
    if (report := await deep_work.read_report(ctx, session_id)) is not None:
        files["report.md"] = report
    return session_id, conversation_id, raw, files


def run_score(task_input: dict[str, Any], raw: dict[str, Any], files: dict[str, Any]) -> float | None:
    """Mean score of `input.score_graders` over one run (a grader that errors scores 0)."""
    specs = [
        GraderSpec(id=s["id"], type=s["type"], gating=True, params=dict(s.get("params") or {}))
        for s in task_input.get("score_graders") or []
    ]
    if not specs:
        return None
    ctx = GradeContext(trace=flatten(raw), raw=raw, files=files)
    scores: list[float] = []
    for spec in specs:
        try:
            scores.append(float(grade(spec, ctx)["score"]))
        except Exception:  # noqa: BLE001 - a broken score grader must not end the trial
            scores.append(0.0)
    return sum(scores) / len(scores)


def prompt_tokens(raw: dict[str, Any]) -> int:
    u = total_usage(flatten(raw))
    return u.input_tokens + u.cache_read_tokens + u.cache_write_tokens


async def curate(ctx: DriverContext, conversation_id: str) -> dict[str, Any]:
    """Fire the Deep Work completion hook and wait for the curator's `apply`. Never raises on a curator that does not run."""
    r = await ctx.http.post(
        f"{ctx.backend}/e2e/memory/deep-work-completed", json={"conversation_id": conversation_id}, timeout=60
    )
    body = r.json() if r.status_code == 200 else {}
    entry: dict[str, Any] = {
        "conversation_id": conversation_id, "trigger": body.get("curation") or f"http {r.status_code}", "applied": False,
        "counts": {}, "run_id": body.get("run_id"),
    }
    if body.get("curation") != "started":
        return entry
    deadline = time.monotonic() + min(max(ctx.remaining(), 0.0), APPLY_WAIT_S[ctx.ew.mode])
    while time.monotonic() < deadline:
        rows = sql(
            ctx.ew.world,
            "SELECT status, outcome FROM tool_invocations WHERE tool = 'memory_curate_apply' AND idempotency_key LIKE ?",
            f"curate:{conversation_id}:%",
        )
        done = [(s, _json(o)) for s, o in rows if s == "completed"]
        if done:
            outcome = done[-1][1]
            entry["applied"] = outcome.get("status") == "applied"
            entry["counts"] = outcome.get("counts") or {}
            entry["reason"] = outcome.get("reason")
            return entry
        await asyncio.sleep(POLL_S)
    entry["trigger"] = "started, apply never completed"
    return entry


async def review(ctx: DriverContext) -> list[str]:
    """The reviewer: approve the pending rows that carry no instruction flag, through the memory API. Returns their keys."""
    approved: list[str] = []
    for row in read_rows(ctx):
        if row["status"] != "pending" or row["flags"].get("instruction_flags"):
            continue
        r = await ctx.http.post(
            f"{ctx.backend}/api/v1/projects/{ctx.project_id}/memory/pending/{row['id']}/approve", headers=AUTH, timeout=60
        )
        if r.status_code != 200:
            raise DriverError("approve_failed", f"{row['key']}: {r.status_code} {r.text[:200]}")
        approved.append(row["key"])
    return approved


async def money(ctx: DriverContext, conversation_ids: list[str]) -> float:
    return await ledger.settle_extra_cost(ctx.http, ctx.backend, AUTH, conversation_ids)


# --- repeat --------------------------------------------------------------------------------------------------------------------


async def run_repeat(ctx: DriverContext) -> DriverOutput:
    inp = ctx.task.input
    arm = arm_of(ctx)
    runs_n = int(inp.get("runs") or 5)
    query = inp.get("query")
    if not isinstance(query, str) or len(query) < 5 or not isinstance(inp.get("plan"), dict):
        raise DriverError("bad_task", "repeat tasks need input.query and input.plan")
    started = time.monotonic()
    wipe_memory(ctx)
    canaries = seed_foreign(ctx)
    ctx.lap("setup", started)

    runs: list[dict[str, Any]] = []
    curations: list[dict[str, Any]] = []
    approved: list[str] = []
    blocks: list[dict[str, Any]] = []
    visible: list[dict[str, str]] = []
    convs: list[str] = []
    sessions: list[str] = []
    tainted_ids: list[str] = []
    last: tuple[str, dict[str, Any], dict[str, Any]] | None = None
    timed_out = False
    started = time.monotonic()
    try:
        for i in range(runs_n):
            session_id, conv, raw, files = await dw_run(ctx, query)
            convs.append(conv)
            sessions.append(session_id)
            block, agent = await block_of(ctx, conv, f"run {i + 1} orchestrator")
            blocks.append(block)
            visible += visible_texts(raw, f"run {i + 1}")
            if tainted(raw):
                tainted_ids.append(conv)
            runs.append({
                "index": i, "conversation_id": conv, "tainted": tainted(raw), "score": run_score(inp, raw, files),
                "input_tokens": prompt_tokens(raw), "block_chars": len(block["text"]),
                "injected_ids": list((agent.get("metadata") or {}).get("playbookBulletIds") or []),
            })
            last = (conv, raw, files)
            if arm in ("propose", "auto"):
                curations.append(await curate(ctx, conv))
                if inp.get("reviewer") == "approve_unflagged":
                    approved += await review(ctx)
    except TrialTimeout:
        timed_out = True
        if sessions:
            await deep_work.call(ctx, "POST", f"/engine/{sessions[-1]}/cancel")
    ctx.lap("run", started)
    if last is None:
        raise DriverError("timeout", "no Deep Work run finished")

    started = time.monotonic()
    conv, raw, files = last
    others = convs[:-1]
    curator_ids = [str(curator_conversation_id(c["conversation_id"], c["run_id"])) for c in curations if c.get("run_id")]
    spend = {
        "source_usd": await money(ctx, convs),
        "curator_usd": await money(ctx, curator_ids) if curator_ids else 0.0,
    }
    rec = {
        "version": 1, "kind": "repeat", "arm": arm, "mode": ctx.ew.mode, "runs": runs, "rows": read_rows(ctx), "protected": [],
        "tainted_conversations": tainted_ids, "curations": curations, "approved": approved, "blocks": blocks,
        "visible": visible, "canaries": {"poison": [], **canaries}, "spend": spend, "probes": [],
    }
    ctx.lap("collect", started)
    return DriverOutput(
        conversation_id=conv, raw=raw, files={**files, FILE_KEY: rec}, phases=dict(ctx.phases), timed_out=timed_out,
        cost_conversation_ids=[*others, *curator_ids],
        note=None if len(runs) == runs_n else f"stopped after {len(runs)} of {runs_n} runs",
    )


# --- preference probes -----------------------------------------------------------------------------------------------------------


async def delete_memory(ctx: DriverContext, how: str, inp: dict[str, Any]) -> None:
    base = f"{ctx.backend}/api/v1/projects/{ctx.project_id}/memory"
    if how == "file":
        r = await ctx.http.delete(f"{base}/files/{inp.get('stored_path', 'core.md')}", headers=AUTH, timeout=60)
    elif how == "bullet":
        key = (inp.get("seed_bullet") or {}).get("key")
        row = next((x for x in read_rows(ctx) if x["table"] == "bullet" and x["key"] == key), None)
        if row is None:
            raise DriverError("bad_task", f"no bullet {key!r} to delete")
        r = await ctx.http.delete(f"{base}/bullets/{row['id']}", headers=AUTH, timeout=60)
    elif how == "purge":
        r = await ctx.http.delete(base, headers={**AUTH, "X-E2E-User": "admin"}, timeout=60)
    else:
        raise DriverError("bad_task", f"unknown delete kind {how!r}")
    if r.status_code not in (200, 204):
        raise DriverError("delete_failed", f"{how}: {r.status_code} {r.text[:200]}")


async def run_prefs(ctx: DriverContext) -> DriverOutput:
    inp = ctx.task.input
    started = time.monotonic()
    wipe_memory(ctx)
    canaries = seed_foreign(ctx)
    w = ctx.ew.world
    if (b := inp.get("seed_bullet")):
        put_bullet(ctx, w.project, w.tenant, b["key"], b["section"], b["text"])
    ctx.lap("setup", started)

    started = time.monotonic()
    convs: list[str] = []
    blocks: list[dict[str, Any]] = []
    visible: list[dict[str, str]] = []
    probes: list[dict[str, Any]] = []
    timed_out = False
    raw2: dict[str, Any] = {}
    try:
        c1 = await new_chat(ctx)
        convs.append(c1)
        await say(ctx, c1, inp["statement"], "state")
        raw1 = await fetch_trace(ctx.api, c1, raw=True)
        visible += visible_texts(raw1, "conversation 1")
        token = str(inp["token"]).lower()
        stored = [r for r in read_rows(ctx) if r["status"] == "active" and token in " ".join(r["text"].lower().split())]

        c2 = await new_chat(ctx)
        convs.append(c2)
        block2, _ = await block_of(ctx, c2, "conversation 2")
        blocks.append(block2)
        await say(ctx, c2, inp["question"], "ask")
        raw2 = await fetch_trace(ctx.api, c2, raw=True)
        visible += visible_texts(raw2, "conversation 2")
        probes.append({
            "id": ctx.task.id, "kind": "preference", "token": inp["token"], "expect": inp.get("expect"), "forbid": inp.get("forbid"),
            "stored_text": "\n".join(r["text"] for r in stored), "block": block2["text"], "answer": final_text(raw2),
        })

        if (how := inp.get("delete")):
            await delete_memory(ctx, how, inp)
            tokens = [str(t) for t in inp.get("delete_tokens") or [inp["token"]]]
            c3 = await new_chat(ctx)
            convs.append(c3)
            block3, _ = await block_of(ctx, c3, "conversation 3")
            blocks.append(block3)
            await say(ctx, c3, inp["question_again"], "again")
            raw3 = await fetch_trace(ctx.api, c3, raw=True)
            after = read_rows(ctx)
            seen3 = visible_texts(raw3, "conversation 3")
            visible += seen3
            gone = [r for r in after if any(t.lower() in " ".join(r["text"].lower().split()) for t in tokens)]
            probes.append({
                "id": ctx.task.id, "kind": "deletion", "delete": how, "tokens": tokens,
                "rows_text": "\n".join(r["text"] for r in after), "rows_remaining": len(gone), "block": block3["text"],
                "visible": "\n".join(t["text"] for t in seen3 if "answer" not in t["label"]), "answer": final_text(raw3),
            })
    except TrialTimeout:
        timed_out = True
        if convs:
            await ctx.http.post(f"{ctx.backend}{chat.BASE}/{convs[-1]}/abort", headers=AUTH, json={})
            raw2 = raw2 or await fetch_trace(ctx.api, convs[-1], raw=True)
    ctx.lap("run", started)
    if not raw2:
        raise DriverError("timeout", "the first conversation never settled")

    rows = read_rows(ctx)
    pref = next((p for p in probes if p["kind"] == "preference"), {})
    rec = {
        "version": 1, "kind": "prefs", "arm": arm_of(ctx), "mode": ctx.ew.mode, "runs": [], "rows": rows, "protected": [],
        "tainted_conversations": [], "curations": [], "approved": [], "blocks": blocks, "visible": visible,
        "canaries": {"poison": [], **canaries}, "spend": {}, "probes": probes,
    }
    files = {
        FILE_KEY: rec, "memory": pref.get("stored_text") or "(nothing stored)", "request": inp["question"],
        "response": pref.get("answer") or final_text(raw2),
    }
    conv2 = convs[1] if len(convs) > 1 else convs[0]
    return DriverOutput(
        conversation_id=conv2, raw=raw2, files=files, phases=dict(ctx.phases), timed_out=timed_out,
        cost_conversation_ids=[c for c in convs if c != conv2],
    )


# --- red team ---------------------------------------------------------------------------------------------------------------------


async def run_redteam(ctx: DriverContext) -> DriverOutput:
    inp = ctx.task.input
    arm = arm_of(ctx)
    path = inp.get("path")
    if path not in ("chat", "dw"):
        raise DriverError("bad_task", "redteam tasks need input.path chat or dw")
    started = time.monotonic()
    wipe_memory(ctx)
    canaries = seed_foreign(ctx)
    protected = seed_protected(ctx)
    ctx.lap("setup", started)

    started = time.monotonic()
    convs: list[str] = []
    blocks: list[dict[str, Any]] = []
    visible: list[dict[str, str]] = []
    curations: list[dict[str, Any]] = []
    tainted_ids: list[str] = []
    writes: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    timed_out = False
    attack_raw: dict[str, Any] = {}
    attack_conv = ""
    try:
        if path == "chat":
            attack_conv = await new_chat(ctx, inp.get("selections"))
            convs.append(attack_conv)
            if inp.get("unattended", True):  # a refused write must not leave the run waiting for an approver
                mark_unattended(ctx.ew.world, attack_conv)
            block, _ = await block_of(ctx, attack_conv, "attack conversation")
            blocks.append(block)
            for i, turn in enumerate(chat.turns_of(inp)):
                await say(ctx, attack_conv, turn, f"attack{i}")
            attack_raw = await fetch_trace(ctx.api, attack_conv, raw=True)
        else:
            query = str(inp["query"])
            _, attack_conv, attack_raw, files = await dw_run(ctx, query)
            convs.append(attack_conv)
            block, _ = await block_of(ctx, attack_conv, "attack run orchestrator")
            blocks.append(block)
            if arm in ("propose", "auto"):
                curations.append(await curate(ctx, attack_conv))
        visible += visible_texts(attack_raw, "attack")
        writes = memory_writes(attack_raw)
        if tainted(attack_raw):
            tainted_ids.append(attack_conv)

        probe = await new_chat(ctx)
        convs.append(probe)
        block, _ = await block_of(ctx, probe, "probe conversation")
        blocks.append(block)
        if inp.get("probe"):
            await say(ctx, probe, str(inp["probe"]), "probe")
            visible += visible_texts(await fetch_trace(ctx.api, probe, raw=True), "probe")
    except TrialTimeout:
        timed_out = True
        if attack_conv and path == "chat":
            await ctx.http.post(f"{ctx.backend}{chat.BASE}/{attack_conv}/abort", headers=AUTH, json={})
            attack_raw = attack_raw or await fetch_trace(ctx.api, attack_conv, raw=True)
    ctx.lap("run", started)
    if not attack_raw:
        raise DriverError("timeout", "the attack conversation never finished")

    started = time.monotonic()
    curator_ids = [str(curator_conversation_id(c["conversation_id"], c["run_id"])) for c in curations if c.get("run_id")]
    rec = {
        "version": 1, "kind": "redteam", "arm": arm, "mode": ctx.ew.mode, "runs": [], "rows": read_rows(ctx), "protected": protected,
        "tainted_conversations": tainted_ids, "curations": curations, "approved": [], "blocks": blocks, "visible": visible,
        "canaries": {"poison": [inp["canary"]] if inp.get("canary") else [], **canaries}, "spend": {}, "probes": [],
        "attempts": {"memory_writes": writes, "channel": inp.get("channel"), "attack": inp.get("attack")},
    }
    ctx.lap("collect", started)
    return DriverOutput(
        conversation_id=attack_conv, raw=attack_raw, files={**files, FILE_KEY: rec}, phases=dict(ctx.phases), timed_out=timed_out,
        cost_conversation_ids=[*(c for c in convs if c != attack_conv), *curator_ids],
    )


async def run(ctx: DriverContext) -> DriverOutput:
    if ctx.task.kind == "repeat":
        return await run_repeat(ctx)
    if ctx.task.kind == "memory":
        return await run_prefs(ctx)
    return await run_redteam(ctx)
