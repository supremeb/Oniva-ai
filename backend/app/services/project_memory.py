"""Project memory store (evals/context/memory plan, U25 / KTD16, KTD17).

Tenant- and project-scoped, path-addressed text files with command semantics that mirror the
memory-tool interface (``view``, ``create``, ``str_replace``, ``insert``, ``delete``). This layer owns
path confinement, caps, versioning, status (``active`` / ``pending``) and provenance; the gateway
tools (U26), playbook (U27), injection (U28) and curator (U31) build on it.

Conventions:
  * Every function takes an ``AsyncSession`` and filters by ``tenant_id`` and ``project_id`` in the
    query on top of RLS. Callers own the transaction: this module flushes, never commits.
  * Pending rows (tainted or proposed) are invisible unless ``include_pending=True``; model-facing
    callers never pass it.
  * ``playbook.md`` is a generated, read-only virtual file rendered from active bullets; every write
    command rejects it (the curator is its only writer, through ``memory_bullets``).
  * Failures raise ``MemoryStoreError`` with a stable ``code`` and a corrective message.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_memory import SOURCE_KINDS, STATUSES, MemoryBullet, MemoryFile

MAX_PATH_CHARS = 128
MAX_FILE_BYTES = 32 * 1024
MAX_CORE_BYTES = 8 * 1024
MAX_FILES_PER_PROJECT = 64
MAX_BULLETS_PER_PROJECT = 400  # enforced by the playbook service (U27)
CORE_PATH = "core.md"
PLAYBOOK_PATH = "playbook.md"
# The injected part of core.md is cut at ~2k tokens (about 4 bytes per token).
CORE_INJECT_BYTES = 8000
TRUNCATION_MARKER = "\n[truncated]"

ALLOWED_EXTENSIONS = (".md", ".txt")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class MemoryStoreError(Exception):
    """A rejected memory operation. ``code`` is stable; ``message`` tells the caller how to fix it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_path(path: str) -> str:
    """Return ``path`` if it is a confined memory path, else raise ``invalid_path``."""
    if not isinstance(path, str) or not path:
        raise MemoryStoreError("invalid_path", "Path is required.")
    if len(path) > MAX_PATH_CHARS:
        raise MemoryStoreError("invalid_path", f"Path is longer than {MAX_PATH_CHARS} characters.")
    if not _PATH_RE.match(path):
        raise MemoryStoreError("invalid_path", "Path may only use letters, digits, '.', '_', '-' and '/'.")
    if path.startswith("/") or path.endswith("/") or "//" in path or ".." in path:
        raise MemoryStoreError("invalid_path", "Path must be relative, without '..', '//' or a trailing '/'.")
    base = path.rsplit("/", 1)[-1]
    ext = next((e for e in ALLOWED_EXTENSIONS if base.endswith(e)), None)
    if ext is None or len(base) == len(ext):
        raise MemoryStoreError("invalid_path", "Path must name a file ending in .md or .txt.")
    return path


def _writable_path(path: str) -> str:
    validate_path(path)
    if path == PLAYBOOK_PATH:
        raise MemoryStoreError("read_only", f"{PLAYBOOK_PATH} is generated and read-only.")
    return path


def _check_meta(status: str, source_kind: str) -> None:
    if status not in STATUSES:
        raise MemoryStoreError("invalid_argument", f"status must be one of {', '.join(STATUSES)}.")
    if source_kind not in SOURCE_KINDS:
        raise MemoryStoreError("invalid_argument", f"source_kind must be one of {', '.join(SOURCE_KINDS)}.")


def _check_size(path: str, content: str) -> None:
    size = len(content.encode("utf-8"))
    cap = MAX_CORE_BYTES if path == CORE_PATH else MAX_FILE_BYTES
    if size > cap:
        raise MemoryStoreError("too_large", f"{path} would be {size} bytes; the limit is {cap}.")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _scope(user_id: uuid.UUID | None):
    return MemoryFile.user_id.is_(None) if user_id is None else MemoryFile.user_id == user_id


async def _get(
    db: AsyncSession, *, tenant_id, project_id, user_id, path: str, include_pending: bool, lock: bool = False
) -> MemoryFile | None:
    stmt = select(MemoryFile).where(
        MemoryFile.tenant_id == tenant_id,
        MemoryFile.project_id == project_id,
        _scope(user_id),
        MemoryFile.path == path,
    )
    if not include_pending:
        stmt = stmt.where(MemoryFile.status == "active")
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalars().first()


async def _require(db, *, tenant_id, project_id, user_id, path, include_pending, lock=False) -> MemoryFile:
    row = await _get(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                     include_pending=include_pending, lock=lock)
    if row is None:
        raise MemoryStoreError("not_found", f"No memory file at {path}. Use view without a path to list files.")
    return row


def _check_version(row: MemoryFile, expected_version: int | None) -> None:
    if expected_version is not None and row.version != expected_version:
        raise MemoryStoreError(
            "version_conflict", f"{row.path} is at version {row.version}, not {expected_version}. View it again."
        )


def _apply(row: MemoryFile, content: str) -> None:
    row.content = content
    row.content_hash = _hash(content)
    row.version = (row.version or 0) + 1


async def _render_playbook(db: AsyncSession, tenant_id, project_id) -> str | None:
    bullets = (
        await db.execute(
            select(MemoryBullet).where(
                MemoryBullet.tenant_id == tenant_id,
                MemoryBullet.project_id == project_id,
                MemoryBullet.status == "active",
            )
        )
    ).scalars().all()
    if not bullets:
        return None
    bullets.sort(key=lambda b: (-(b.helpful - 2 * b.harmful), b.bullet_key))
    sections: dict[str, list[str]] = {}
    for b in bullets:
        sections.setdefault(b.section, []).append(f"- [{b.bullet_key}] {b.text} (+{b.helpful}/-{b.harmful})")
    return "\n\n".join(f"## {name}\n" + "\n".join(lines) for name, lines in sections.items())


def _entry(row: MemoryFile) -> dict[str, Any]:
    return {
        "path": row.path,
        "version": row.version,
        "size": len(row.content.encode("utf-8")),
        "status": row.status,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_index(
    db: AsyncSession, *, tenant_id, project_id, user_id=None, include_pending: bool = False
) -> list[dict[str, Any]]:
    """Files in a scope, sorted by path. ``playbook.md`` is listed when it has active bullets."""
    stmt = select(MemoryFile).where(
        MemoryFile.tenant_id == tenant_id, MemoryFile.project_id == project_id, _scope(user_id)
    )
    if not include_pending:
        stmt = stmt.where(MemoryFile.status == "active")
    rows = (await db.execute(stmt.order_by(MemoryFile.path))).scalars().all()
    entries = [_entry(r) for r in rows]
    if user_id is None:
        playbook = await _render_playbook(db, tenant_id, project_id)
        if playbook is not None:
            entries.append({"path": PLAYBOOK_PATH, "version": None, "size": len(playbook.encode("utf-8")),
                            "status": "active", "updated_at": None, "read_only": True})
    return sorted(entries, key=lambda e: e["path"])


async def view(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    path: str | None = None,
    user_id=None,
    view_range: tuple[int, int] | None = None,
    include_pending: bool = False,
) -> dict[str, Any]:
    """One file (optionally a 1-based inclusive line range), or the index when ``path`` is empty."""
    if path in (None, "", "/"):
        return {"files": await list_index(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id,
                                          include_pending=include_pending)}
    validate_path(path)
    if path == PLAYBOOK_PATH:
        content = await _render_playbook(db, tenant_id, project_id) if user_id is None else None
        if content is None:
            raise MemoryStoreError("not_found", f"No memory file at {path}.")
        meta: dict[str, Any] = {"path": path, "version": None, "status": "active", "read_only": True}
    else:
        row = await _require(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                             include_pending=include_pending)
        content = row.content
        meta = {"path": row.path, "version": row.version, "status": row.status, "read_only": False,
                "source_kind": row.source_kind, "flags": row.flags}
    if view_range is not None:
        start, end = view_range
        lines = content.split("\n")
        if start < 1 or end < start or end > len(lines):
            raise MemoryStoreError("out_of_range", f"view_range must be within 1..{len(lines)} and start <= end.")
        content = "\n".join(lines[start - 1:end])
    return {**meta, "content": content}


async def create(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    path: str,
    content: str,
    source_kind: str,
    user_id=None,
    source_conversation_id=None,
    status: str = "active",
    expected_version: int | None = None,
    include_pending: bool = False,
) -> MemoryFile:
    """Create the file, or replace its content when the path exists (idempotent by path)."""
    _writable_path(path)
    _check_meta(status, source_kind)
    _check_size(path, content)
    existing = await _get(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                          include_pending=True, lock=True)
    if existing is not None and existing.status == "pending" and not include_pending:
        raise MemoryStoreError("pending_exists", f"{path} has a proposed version awaiting review.")
    if existing is None:
        if expected_version is not None:
            raise MemoryStoreError("version_conflict", f"{path} does not exist yet.")
        count = (
            await db.execute(
                select(func.count()).select_from(MemoryFile).where(
                    MemoryFile.tenant_id == tenant_id, MemoryFile.project_id == project_id
                )
            )
        ).scalar_one()
        if count >= MAX_FILES_PER_PROJECT:
            raise MemoryStoreError(
                "too_many_files", f"A project holds at most {MAX_FILES_PER_PROJECT} memory files. Delete one first."
            )
        row = MemoryFile(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path, content=content,
            content_hash=_hash(content), version=1, status=status, source_kind=source_kind,
            source_conversation_id=source_conversation_id,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError as exc:  # lost a create race on the unique path
            raise MemoryStoreError("conflict", f"{path} was created concurrently. View it and retry.") from exc
        return row
    _check_version(existing, expected_version)
    _apply(existing, content)
    existing.status = status
    existing.source_kind = source_kind
    existing.source_conversation_id = source_conversation_id
    await db.flush()
    return existing


async def str_replace(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    path: str,
    old: str,
    new: str,
    user_id=None,
    expected_version: int | None = None,
    include_pending: bool = False,
) -> MemoryFile:
    """Replace the single occurrence of ``old`` with ``new``."""
    _writable_path(path)
    if not old:
        raise MemoryStoreError("invalid_argument", "old_str must not be empty.")
    row = await _require(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                         include_pending=include_pending, lock=True)
    _check_version(row, expected_version)
    matches = row.content.count(old)
    if matches == 0:
        raise MemoryStoreError("no_match", f"The text to replace was not found in {path}.")
    if matches > 1:
        raise MemoryStoreError(
            "multiple_matches", f"The text to replace appears {matches} times in {path}. Include more context."
        )
    updated = row.content.replace(old, new, 1)
    _check_size(path, updated)
    _apply(row, updated)
    await db.flush()
    return row


async def insert(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    path: str,
    line: int,
    text: str,
    user_id=None,
    expected_version: int | None = None,
    include_pending: bool = False,
) -> MemoryFile:
    """Insert ``text`` so that it becomes 1-based ``line`` (one past the last line appends)."""
    _writable_path(path)
    row = await _require(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                         include_pending=include_pending, lock=True)
    _check_version(row, expected_version)
    lines = row.content.split("\n") if row.content else []
    if not isinstance(line, int) or line < 1 or line > len(lines) + 1:
        raise MemoryStoreError("out_of_range", f"line must be within 1..{len(lines) + 1}.")
    lines.insert(line - 1, text)
    updated = "\n".join(lines)
    _check_size(path, updated)
    _apply(row, updated)
    await db.flush()
    return row


async def delete(
    db: AsyncSession,
    *,
    tenant_id,
    project_id,
    path: str,
    user_id=None,
    expected_version: int | None = None,
    include_pending: bool = False,
) -> None:
    _writable_path(path)
    row = await _require(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, path=path,
                         include_pending=include_pending, lock=True)
    _check_version(row, expected_version)
    await db.delete(row)
    await db.flush()


async def render_core(db: AsyncSession, *, tenant_id, project_id) -> str:
    """Active project-level ``core.md`` cut to the injection budget; empty string when absent."""
    row = await _get(db, tenant_id=tenant_id, project_id=project_id, user_id=None, path=CORE_PATH,
                     include_pending=False)
    if row is None or not row.content:
        return ""
    raw = row.content.encode("utf-8")
    if len(raw) <= CORE_INJECT_BYTES:
        return row.content
    return raw[:CORE_INJECT_BYTES].decode("utf-8", errors="ignore").rstrip() + TRUNCATION_MARKER
