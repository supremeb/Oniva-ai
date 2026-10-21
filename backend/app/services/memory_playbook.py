"""Project playbook: structured, delta-only bullets (plan U27 / KTD16, KTD18).

The playbook is a set of short bullets (``memory_bullets``) with helpful/harmful counters. Nothing
rewrites it wholesale: a curator proposes *deltas* by id (``add``, ``bump``, ``revise``, ``retire``) and
``apply_deltas`` validates and applies them. ``render_playbook`` produces the read-only virtual
``playbook.md``.

Rules:
  * Caps: at most 12 operations per apply (extra ones are dropped with a warning) and 240 characters
    per bullet text. Invalid operations are skipped with a warning; they never abort the rest.
  * Taint gate: a tainted source can only ``add``, and every added row is ``pending`` (never injected).
    It cannot bump, revise or retire active rows, and a duplicate of an active bullet is not a bump.
  * Idempotent by ``delta_id`` (derived from run id and operation when absent): ``add`` uses a key
    derived from the id; the other ops record applied ids in the target's ``flags['deltas']``.
  * Unknown ids are ignored with a warning, never created.
  * Callers own the transaction: this module flushes, never commits.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._clock import utc_now
from app.models.project_memory import MemoryBullet
from app.services import project_memory as store
from app.services.memory_flagging import detect_flags

MAX_OPS_PER_RUN = 12
MAX_BULLET_CHARS = 240
MAX_SECTION_CHARS = 64
DEFAULT_IDLE_RUNS = 20
COSINE_THRESHOLD = 0.88
JACCARD_THRESHOLD = 0.8
HARMFUL_MARGIN = 2
_MAX_RECORDED_DELTAS = 50

OPS = ("add", "bump", "revise", "retire")
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class DeltaSource:
    """Provenance of a delta set: copied onto added rows."""

    kind: str = "curator"
    conversation_id: uuid.UUID | None = None
    run_id: str | None = None


@dataclass
class DeltaResult:
    added: list[str] = field(default_factory=list)
    bumped: list[str] = field(default_factory=list)
    revised: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def warn(self, code: str, message: str, delta_id: str | None = None) -> None:
        self.warnings.append({"code": code, "message": message, "delta_id": delta_id})


# --- text helpers -----------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _score(b: MemoryBullet) -> int:
    return (b.helpful or 0) - 2 * (b.harmful or 0)


def _recency(b: MemoryBullet) -> float:
    ts = b.last_used_at or b.updated_at or b.created_at
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def _ranked(bullets: Sequence[MemoryBullet]) -> list[MemoryBullet]:
    return sorted(bullets, key=lambda b: (-_score(b), -_recency(b), b.bullet_key))


# --- queries ------------------------------------------------------------------------------


async def _active(db: AsyncSession, tenant_id, project_id, section: str | None = None) -> list[MemoryBullet]:
    stmt = select(MemoryBullet).where(
        MemoryBullet.tenant_id == tenant_id,
        MemoryBullet.project_id == project_id,
        MemoryBullet.status == "active",
    )
    if section is not None:
        stmt = stmt.where(MemoryBullet.section == section)
    return list((await db.execute(stmt)).scalars().all())


async def _get_bullet(db: AsyncSession, tenant_id, project_id, key: str) -> MemoryBullet | None:
    return (
        await db.execute(
            select(MemoryBullet).where(
                MemoryBullet.tenant_id == tenant_id,
                MemoryBullet.project_id == project_id,
                MemoryBullet.bullet_key == key,
            )
        )
    ).scalars().first()


async def top_bullets(
    db: AsyncSession, *, tenant_id, project_id, section: str | None = None, n: int = 10
) -> list[MemoryBullet]:
    """Active bullets by ``helpful - 2*harmful`` (ties: most recently used first), at most ``n``."""
    return _ranked(await _active(db, tenant_id, project_id, section))[: max(n, 0)]


async def render_playbook(db: AsyncSession, *, tenant_id, project_id, n: int | None = None) -> str | None:
    """The virtual ``playbook.md``: active bullets grouped by section, best first. None when empty."""
    bullets = _ranked(await _active(db, tenant_id, project_id))
    if n is not None:
        bullets = bullets[: max(n, 0)]
    if not bullets:
        return None
    sections: dict[str, list[str]] = {}
    for b in bullets:
        sections.setdefault(b.section, []).append(f"- [{b.bullet_key}] {b.text} (+{b.helpful}/-{b.harmful})")
    return "\n\n".join(f"## {name}\n" + "\n".join(lines) for name, lines in sections.items())


# --- flags bookkeeping ----------------------------------------------------------------------


def _flags(b: MemoryBullet) -> dict[str, Any]:
    return dict(b.flags) if isinstance(b.flags, dict) else {}


def _record(b: MemoryBullet, delta_id: str, **extra: Any) -> None:
    flags = _flags(b)
    flags["deltas"] = [*flags.get("deltas", []), delta_id][-_MAX_RECORDED_DELTAS:]
    flags.update(extra)
    b.flags = flags  # reassign so the JSON column registers the change


def _already(b: MemoryBullet, delta_id: str) -> bool:
    return delta_id in _flags(b).get("deltas", [])


def _derive_delta_id(op: dict[str, Any], source: DeltaSource) -> str:
    body = {k: v for k, v in op.items() if k != "delta_id"}
    scope = source.run_id or (str(source.conversation_id) if source.conversation_id else "")
    raw = scope + "|" + json.dumps(body, sort_keys=True, default=str)
    return "d_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _key_for(delta_id: str) -> str:
    return "b" + hashlib.sha256(delta_id.encode()).hexdigest()[:12]


# --- dedupe -----------------------------------------------------------------------------------


async def dedupe(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    section: str,
    text: str,
    embedder: Embedder | None = None,
    cosine_threshold: float = COSINE_THRESHOLD,
    jaccard_threshold: float = JACCARD_THRESHOLD,
) -> MemoryBullet | None:
    """The active bullet of ``section`` that ``text`` duplicates, else None.

    Uses embedding cosine when an ``embedder`` is given and works; otherwise (none configured, or it
    raised) normalized-token Jaccard.
    """
    candidates = await _active(db, tenant_id, project_id, section)
    if not candidates:
        return None
    if embedder is not None:
        try:
            vectors = await embedder([text, *(c.text for c in candidates)])
            if len(vectors) == len(candidates) + 1:
                best = max(zip(candidates, vectors[1:]), key=lambda cv: cosine(vectors[0], cv[1]))
                return best[0] if cosine(vectors[0], best[1]) >= cosine_threshold else None
        except Exception:
            pass  # fall through to the lexical check
    best_b = max(candidates, key=lambda c: jaccard(text, c.text))
    return best_b if jaccard(text, best_b.text) >= jaccard_threshold else None


# --- apply ---------------------------------------------------------------------------------------


def _bump(b: MemoryBullet, kind: str, now: datetime) -> None:
    if kind == "harmful":
        b.harmful = (b.harmful or 0) + 1
    else:
        b.helpful = (b.helpful or 0) + 1
    b.last_used_at = now
    flags = _flags(b)
    flags["idle_runs"] = 0
    b.flags = flags


async def apply_deltas(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    deltas: Any,
    source: DeltaSource,
    tainted: bool,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> DeltaResult:
    """Validate and apply a delta set. See the module docstring for the rules."""
    now = now or utc_now()
    res = DeltaResult()
    if not isinstance(deltas, (list, tuple)):
        res.warn("invalid_op", "deltas must be a list of operations.")
        return res
    ops = list(deltas)
    if len(ops) > MAX_OPS_PER_RUN:
        res.warn("over_op_cap", f"Only the first {MAX_OPS_PER_RUN} operations are applied; {len(ops) - MAX_OPS_PER_RUN} dropped.")
        ops = ops[:MAX_OPS_PER_RUN]
    bumped_now: set[str] = set()

    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in OPS:
            res.warn("invalid_op", "Each delta must be an object with op add|bump|revise|retire.")
            continue
        kind = op["op"]
        delta_id = op.get("delta_id")
        if not isinstance(delta_id, str) or not delta_id.strip():
            delta_id = _derive_delta_id(op, source)
        text = op.get("text")
        if kind in ("add", "revise"):
            if not isinstance(text, str) or not _norm(text):
                res.warn("invalid_op", f"{kind} needs non-empty text.", delta_id)
                continue
            text = _norm(text)
            if len(text) > MAX_BULLET_CHARS:
                res.warn("text_too_long", f"Bullet text is limited to {MAX_BULLET_CHARS} characters.", delta_id)
                continue
        if kind == "add":
            section = op.get("section")
            if not isinstance(section, str) or not _norm(section) or len(_norm(section)) > MAX_SECTION_CHARS:
                res.warn("invalid_op", f"add needs a section of 1-{MAX_SECTION_CHARS} characters.", delta_id)
                continue
            await _op_add(db, tenant_id, project_id, _norm(section), text, delta_id, source, tainted,
                          embedder, now, bumped_now, res)
            continue
        key = op.get("id")
        if not isinstance(key, str) or not key:
            res.warn("invalid_op", f"{kind} needs the id of an existing bullet.", delta_id)
            continue
        if kind == "bump" and op.get("kind") not in ("helpful", "harmful"):
            res.warn("invalid_op", "bump needs kind helpful or harmful.", delta_id)
            continue
        if tainted:
            res.warn("tainted_skipped", f"A tainted source cannot {kind} existing bullets.", delta_id)
            continue
        bullet = await _get_bullet(db, tenant_id, project_id, key)
        if bullet is None:
            res.warn("unknown_id", f"No bullet {key}; ignored.", delta_id)
            continue
        if _already(bullet, delta_id):
            res.warn("already_applied", f"Delta {delta_id} was already applied.", delta_id)
            continue
        if bullet.status != "active":
            res.warn("not_active", f"Bullet {key} is pending review; ignored.", delta_id)
            continue
        if kind == "bump":
            if key in bumped_now:
                res.warn("bump_capped", f"Bullet {key} was already bumped in this run.", delta_id)
                continue
            bumped_now.add(key)
            _bump(bullet, op["kind"], now)
            _record(bullet, delta_id)
            res.bumped.append(key)
        elif kind == "revise":
            bullet.text = text
            bullet.helpful = 0
            bullet.harmful = 0
            _record(bullet, delta_id, revised=True, instruction_flags=detect_flags(text))
            res.revised.append(key)
        else:  # retire
            await db.delete(bullet)
            res.retired.append(key)
        await db.flush()
    return res


async def _op_add(db, tenant_id, project_id, section, text, delta_id, source, tainted, embedder, now,
                  bumped_now, res) -> None:
    key = _key_for(delta_id)
    if await _get_bullet(db, tenant_id, project_id, key) is not None:
        res.warn("already_applied", f"Delta {delta_id} was already applied.", delta_id)
        return
    dup = await dedupe(db, tenant_id=tenant_id, project_id=project_id, section=section, text=text,
                       embedder=embedder)
    if dup is not None:
        if tainted:
            res.warn("duplicate_skipped", f"Duplicate of {dup.bullet_key}; a tainted source cannot bump it.", delta_id)
        elif dup.bullet_key in bumped_now:
            res.warn("bump_capped", f"Bullet {dup.bullet_key} was already bumped in this run.", delta_id)
        else:
            bumped_now.add(dup.bullet_key)
            _bump(dup, "helpful", now)
            _record(dup, delta_id)
            res.bumped.append(dup.bullet_key)
            await db.flush()
        return
    try:
        await store.check_bullet_capacity(db, tenant_id=tenant_id, project_id=project_id)
    except store.MemoryStoreError as exc:
        res.warn("bullet_cap", exc.message, delta_id)
        return
    db.add(MemoryBullet(
        tenant_id=tenant_id, project_id=project_id, bullet_key=key, section=section, text=text,
        helpful=0, harmful=0, status="pending" if tainted else "active",
        source_kind=source.kind if source.kind in store.SOURCE_KINDS else "curator",
        source_conversation_id=source.conversation_id, source_run_id=source.run_id,
        flags={"deltas": [delta_id], "instruction_flags": detect_flags(text), "idle_runs": 0},
    ))
    await db.flush()
    res.added.append(key)


# --- expiry ----------------------------------------------------------------------------------------


def _default_idle_runs() -> int:
    try:
        from app.config import settings

        return int(getattr(settings, "MEMORY_BULLET_IDLE_RUNS", DEFAULT_IDLE_RUNS))
    except Exception:  # pragma: no cover
        return DEFAULT_IDLE_RUNS


async def retire_expired(
    db: AsyncSession, *, tenant_id, project_id, run_id: str, idle_runs: int | None = None
) -> list[str]:
    """Delete active bullets that expired; return their keys. Call once per finished run.

    Expired: ``harmful > helpful + 2``, or not bumped for ``idle_runs`` runs (default
    ``MEMORY_BULLET_IDLE_RUNS``, 20). Idle runs are counted in ``flags['idle_runs']`` and reset by a
    bump; a run id is counted at most once, so repeating the call for the same run is harmless.
    """
    limit = idle_runs if idle_runs is not None else _default_idle_runs()
    gone: list[str] = []
    for b in await _active(db, tenant_id, project_id):
        if (b.harmful or 0) > (b.helpful or 0) + HARMFUL_MARGIN:
            gone.append(b.bullet_key)
            await db.delete(b)
            continue
        flags = _flags(b)
        if flags.get("idle_run_id") != run_id:
            flags["idle_runs"] = int(flags.get("idle_runs", 0)) + 1
            flags["idle_run_id"] = run_id
            b.flags = flags
        if int(flags.get("idle_runs", 0)) >= limit:
            gone.append(b.bullet_key)
            await db.delete(b)
    await db.flush()
    return sorted(gone)
