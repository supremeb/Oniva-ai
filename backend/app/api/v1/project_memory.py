"""Project memory API (plan U32): view, edit, approve, reject, export and erase a project's memory.

Gates, in order: the ``ENABLE_PROJECT_MEMORY`` flag (404 when off for the tenant), the RBAC permission
(``project_memory:read`` viewer+, ``project_memory:write`` member+, ``project_memory:purge`` admin+), then
project access (a non-member gets 404; writes also need editor+ on the project). Each destructive or
state-changing route writes a signed audit entry (paths, keys and counts only, never memory text) and the
request session commits it together with the change (``get_db`` function scope, tenant context intact).

Approving a pending row makes it active and records the approver; rejecting deletes it.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.permissions import has_permission
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.exceptions import EntityNotFoundError, PermissionDeniedError
from app.models.project_memory import MemoryBullet, MemoryFile
from app.models.tenant import Tenant
from app.schemas.project_memory import (
    MemoryBulletOut,
    MemoryBulletPatch,
    MemoryDecisionOut,
    MemoryExport,
    MemoryFileOut,
    MemoryFilePut,
    MemoryOverview,
    MemoryPurgeOut,
)
from app.services import project_memory as store
from app.services.audit_chain import AuditChain
from app.services.feature_flags import get_feature
from app.services.memory_flagging import detect_flags
from app.services.memory_playbook import MAX_BULLET_CHARS, MAX_SECTION_CHARS, render_playbook
from app.services.project_memory import MemoryStoreError
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)

router = APIRouter()

FLAG = "ENABLE_PROJECT_MEMORY"
AUDIT_AGENT_ID = "project_memory"

# MemoryStoreError.code -> HTTP status. Anything unlisted is a 422.
_STATUS = {
    "not_found": 404,
    "version_conflict": 409,
    "conflict": 409,
    "pending_exists": 409,
    "multiple_matches": 409,
    "too_many_files": 409,
    "too_many_bullets": 409,
    "too_large": 413,
}


def _raise_store(exc: MemoryStoreError) -> None:
    raise HTTPException(status_code=_STATUS.get(exc.code, 422), detail={"code": exc.code, "message": exc.message})


# --- dependencies -------------------------------------------------------------------------------


async def get_memory_audit(db: AsyncSession = Depends(get_db, scope="function")) -> AuditChain:
    """The audit chain on the request session (overridden in the SQLite tier, whose ARRAY columns it lacks)."""
    return AuditChain(db)


async def require_memory_enabled(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
) -> AuthUser:
    """404 when project memory is off for the caller's tenant (the feature does not exist for them)."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == UUID(user.tenant_id)))).scalars().first()
    if tenant is None or not get_feature(tenant, FLAG):
        raise HTTPException(status_code=404, detail="Project memory is not enabled")
    return user


def _need(permission: str):
    async def checker(user: AuthUser = Depends(require_memory_enabled)) -> AuthUser:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=403, detail=f"Permission denied. Required: {permission}")
        return user

    return checker


async def _project_access(db: AsyncSession, user: AuthUser, project_id: UUID, role: str = "viewer") -> None:
    """Project membership at ``role`` or better; no access at all is 404, too little is 403."""
    try:
        await ProjectService(db).check_permission(project_id, UUID(user.id), UUID(user.tenant_id), required_role=role)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Access denied")


async def _project_exists(db: AsyncSession, user: AuthUser, project_id: UUID) -> None:
    if await ProjectService(db)._get_project_raw(project_id, UUID(user.tenant_id)) is None:
        raise HTTPException(status_code=404, detail="Project not found")


async def _audit(audit: AuditChain, user: AuthUser, action: str, decision: str, rationale: str, **meta: Any) -> None:
    await audit.append(
        tenant_id=user.tenant_id,
        agent_id=AUDIT_AGENT_ID,
        action=action,
        decision=decision,
        rationale=rationale,
        user_id=user.id,
        actor_email=user.email,
        actor_name=user.name,
        extra_metadata={"actor_id": user.id, "actor_role": user.role, **meta},
    )


# --- helpers ------------------------------------------------------------------------------------


def _file_out(row: MemoryFile) -> MemoryFileOut:
    return MemoryFileOut.model_validate(row, from_attributes=True)


def _bullet_out(row: MemoryBullet) -> MemoryBulletOut:
    return MemoryBulletOut.model_validate(row, from_attributes=True)


async def _rows(db: AsyncSession, tenant_id: UUID, project_id: UUID):
    files = (
        await db.execute(
            select(MemoryFile)
            .where(MemoryFile.tenant_id == tenant_id, MemoryFile.project_id == project_id)
            .order_by(MemoryFile.path)
        )
    ).scalars().all()
    bullets = (
        await db.execute(
            select(MemoryBullet)
            .where(MemoryBullet.tenant_id == tenant_id, MemoryBullet.project_id == project_id)
            .order_by(MemoryBullet.section, MemoryBullet.bullet_key)
        )
    ).scalars().all()
    return files, bullets


def _with_flags(existing: Any, **updates: Any) -> dict:
    flags = dict(existing) if isinstance(existing, dict) else {}
    flags.update(updates)
    return flags  # a new dict, so the JSON column registers the change


async def _get_file(db: AsyncSession, tenant_id: UUID, project_id: UUID, path: str) -> MemoryFile | None:
    return (
        await db.execute(
            select(MemoryFile).where(
                MemoryFile.tenant_id == tenant_id,
                MemoryFile.project_id == project_id,
                MemoryFile.user_id.is_(None),
                MemoryFile.path == path,
            )
        )
    ).scalars().first()


async def _get_bullet(db: AsyncSession, tenant_id: UUID, project_id: UUID, bullet_id: UUID) -> MemoryBullet | None:
    return (
        await db.execute(
            select(MemoryBullet).where(
                MemoryBullet.tenant_id == tenant_id,
                MemoryBullet.project_id == project_id,
                MemoryBullet.id == bullet_id,
            )
        )
    ).scalars().first()


# --- routes -------------------------------------------------------------------------------------


@router.get("/{project_id}/memory", response_model=MemoryOverview)
async def get_memory(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:read")),
):
    """Files, bullets (pending included, flagged for review) and the rendered playbook."""
    await _project_access(db, user, project_id)
    tenant_id = UUID(user.tenant_id)
    files, bullets = await _rows(db, tenant_id, project_id)
    playbook = await render_playbook(db, tenant_id=tenant_id, project_id=project_id)
    pending = sum(1 for r in (*files, *bullets) if r.status == "pending")
    return MemoryOverview(
        project_id=project_id,
        files=[_file_out(f) for f in files],
        bullets=[_bullet_out(b) for b in bullets],
        playbook=playbook,
        pending_count=pending,
    )


@router.put("/{project_id}/memory/files/{path:path}", response_model=MemoryFileOut)
async def put_file(
    project_id: UUID,
    path: str,
    body: MemoryFilePut,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    """Create or replace a file (``expected_version`` gives an optimistic check, 409 on mismatch)."""
    await _project_access(db, user, project_id, "editor")
    tenant_id = UUID(user.tenant_id)
    try:
        row = await store.create(
            db, tenant_id=tenant_id, project_id=project_id, path=path, content=body.content,
            source_kind="user", expected_version=body.expected_version,
        )
    except MemoryStoreError as exc:
        _raise_store(exc)
    row.flags = _with_flags(row.flags, instruction_flags=detect_flags(body.content))
    await db.flush()
    await _audit(audit, user, "memory_edited", "edit_file", "project memory file edited by a user",
                 project_id=str(project_id), path=path, version=row.version)
    return _file_out(row)


@router.delete("/{project_id}/memory/files/{path:path}", status_code=204)
async def delete_file(
    project_id: UUID,
    path: str,
    expected_version: Optional[int] = Query(None, ge=1),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    await _project_access(db, user, project_id, "editor")
    try:
        await store.delete(
            db, tenant_id=UUID(user.tenant_id), project_id=project_id, path=path,
            expected_version=expected_version, include_pending=True,
        )
    except MemoryStoreError as exc:
        _raise_store(exc)
    await _audit(audit, user, "memory_deleted", "delete_file", "project memory file deleted by a user",
                 project_id=str(project_id), path=path)


@router.patch("/{project_id}/memory/bullets/{bullet_id}", response_model=MemoryBulletOut)
async def patch_bullet(
    project_id: UUID,
    bullet_id: UUID,
    body: MemoryBulletPatch,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    await _project_access(db, user, project_id, "editor")
    if body.text is None and body.section is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_argument", "message": "Nothing to change."})
    text_ = body.text.strip() if body.text is not None else None
    section = body.section.strip() if body.section is not None else None
    if text_ is not None and not 0 < len(text_) <= MAX_BULLET_CHARS:
        raise HTTPException(
            status_code=422, detail={"code": "too_large", "message": f"A bullet is at most {MAX_BULLET_CHARS} characters."}
        )
    if section is not None and not 0 < len(section) <= MAX_SECTION_CHARS:
        raise HTTPException(
            status_code=422, detail={"code": "too_large", "message": f"A section name is at most {MAX_SECTION_CHARS} characters."}
        )
    row = await _get_bullet(db, UUID(user.tenant_id), project_id, bullet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bullet not found")
    if text_ is not None:
        row.text = text_
        row.flags = _with_flags(row.flags, instruction_flags=detect_flags(text_))
    if section is not None:
        row.section = section
    await db.flush()
    await _audit(audit, user, "memory_edited", "edit_bullet", "playbook bullet edited by a user",
                 project_id=str(project_id), bullet_id=str(bullet_id), bullet_key=row.bullet_key)
    return _bullet_out(row)


@router.delete("/{project_id}/memory/bullets/{bullet_id}", status_code=204)
async def delete_bullet(
    project_id: UUID,
    bullet_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    await _project_access(db, user, project_id, "editor")
    row = await _get_bullet(db, UUID(user.tenant_id), project_id, bullet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Bullet not found")
    key = row.bullet_key
    await db.delete(row)
    await db.flush()
    await _audit(audit, user, "memory_deleted", "delete_bullet", "playbook bullet deleted by a user",
                 project_id=str(project_id), bullet_id=str(bullet_id), bullet_key=key)


async def _find_pending(db: AsyncSession, tenant_id: UUID, project_id: UUID, item_id: UUID):
    """The row (file or bullet) with this id; 404 if none, 409 if it is not pending."""
    for model, kind in ((MemoryFile, "file"), (MemoryBullet, "bullet")):
        row = (
            await db.execute(
                select(model).where(
                    model.id == item_id, model.tenant_id == tenant_id, model.project_id == project_id
                )
            )
        ).scalars().first()
        if row is not None:
            if row.status != "pending":
                raise HTTPException(status_code=409, detail={"code": "not_pending", "message": "Item is not pending."})
            return row, kind
    raise HTTPException(status_code=404, detail="Pending item not found")


def _key_of(row: Any, kind: str) -> dict:
    return {"path": row.path} if kind == "file" else {"bullet_key": row.bullet_key}


@router.post("/{project_id}/memory/pending/{item_id}/approve", response_model=MemoryDecisionOut)
async def approve_pending(
    project_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    """Make a pending row active (and injectable) and record who approved it."""
    await _project_access(db, user, project_id, "editor")
    row, kind = await _find_pending(db, UUID(user.tenant_id), project_id, item_id)
    row.status = "active"
    row.flags = _with_flags(
        row.flags, approved_by=user.id, approved_at=datetime.now(timezone.utc).isoformat()
    )
    await db.flush()
    await _audit(audit, user, "memory_approved", "approve", "pending project memory approved",
                 project_id=str(project_id), item_id=str(item_id), kind=kind, **_key_of(row, kind))
    return MemoryDecisionOut(id=item_id, kind=kind, status="active")


@router.post("/{project_id}/memory/pending/{item_id}/reject", response_model=MemoryDecisionOut)
async def reject_pending(
    project_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:write")),
    audit: AuditChain = Depends(get_memory_audit),
):
    """Reject a pending row: it is deleted."""
    await _project_access(db, user, project_id, "editor")
    row, kind = await _find_pending(db, UUID(user.tenant_id), project_id, item_id)
    meta = _key_of(row, kind)
    await db.delete(row)
    await db.flush()
    await _audit(audit, user, "memory_rejected", "reject", "pending project memory rejected",
                 project_id=str(project_id), item_id=str(item_id), kind=kind, **meta)
    return MemoryDecisionOut(id=item_id, kind=kind, status="rejected")


@router.post("/{project_id}/memory/export", response_model=MemoryExport)
async def export_memory(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:read")),
):
    """Everything the project remembers, as JSON (data portability)."""
    await _project_access(db, user, project_id)
    tenant_id = UUID(user.tenant_id)
    files, bullets = await _rows(db, tenant_id, project_id)
    playbook = await render_playbook(db, tenant_id=tenant_id, project_id=project_id)
    return MemoryExport(
        project_id=project_id,
        exported_at=datetime.now(timezone.utc),
        files=[_file_out(f) for f in files],
        bullets=[_bullet_out(b) for b in bullets],
        playbook=playbook,
        meta={"file_count": len(files), "bullet_count": len(bullets)},
    )


@router.delete("/{project_id}/memory", response_model=MemoryPurgeOut)
async def purge_memory(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(_need("project_memory:purge")),
    audit: AuditChain = Depends(get_memory_audit),
):
    """Hard-delete every memory row of the project (tenant admins). Conversation workspace files are untouched."""
    await _project_exists(db, user, project_id)
    tenant_id = UUID(user.tenant_id)
    files = (await db.execute(delete(MemoryFile).where(
        MemoryFile.tenant_id == tenant_id, MemoryFile.project_id == project_id))).rowcount or 0
    bullets = (await db.execute(delete(MemoryBullet).where(
        MemoryBullet.tenant_id == tenant_id, MemoryBullet.project_id == project_id))).rowcount or 0
    await db.flush()
    await _audit(audit, user, "memory_purged", "purge", "all project memory deleted",
                 project_id=str(project_id), files_deleted=files, bullets_deleted=bullets)
    return MemoryPurgeOut(files_deleted=files, bullets_deleted=bullets)
