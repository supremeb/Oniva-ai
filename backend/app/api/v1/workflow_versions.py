"""
Workflow Versions API endpoints
Vellum-inspired version control for workflows

Supports:
- Creating new versions from current workflow state
- Listing version history
- Comparing versions (diff)
- Activating/deploying specific versions
- Rolling back to previous versions
- Tagging versions (e.g., "production", "staging")
"""

import hashlib
import json
import uuid
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.core.database import get_db
from app.models.workflow import Workflow, WorkflowVersion
from app.auth.dependencies import get_current_user, get_current_tenant_id, get_current_user_id
from app.auth.providers.base import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Pydantic Models ---

class VersionCreate(BaseModel):
    """Request to create a new version"""
    description: Optional[str] = None
    version_tag: Optional[str] = None  # Semantic version like "1.0.0", "1.1.0", "2.0.0"
    is_draft: bool = True


class PublishRequest(BaseModel):
    """Request to publish a draft as a new version"""
    version_tag: str  # Required semantic version like "1.0.0"
    description: Optional[str] = None
    change_type: Optional[str] = "minor"  # "patch", "minor", "major"


class VersionUpdate(BaseModel):
    """Request to update version metadata"""
    description: Optional[str] = None
    version_tag: Optional[str] = None


class VersionActivate(BaseModel):
    """Request to activate a version"""
    activate: bool = True


class VersionResponse(BaseModel):
    """Version response model"""
    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    version_tag: Optional[str]  # Semantic version string like "1.0.0"
    description: Optional[str]
    is_active: bool
    is_draft: bool
    node_count: int
    edge_count: int
    spec_hash: Optional[str]
    created_at: datetime
    published_at: Optional[datetime]
    created_by: Optional[uuid.UUID]

    class Config:
        from_attributes = True


class DraftResponse(BaseModel):
    """Response for draft status"""
    has_draft: bool
    draft: Optional[VersionResponse] = None
    based_on_version: Optional[VersionResponse] = None  # The published version the draft is based on
    latest_published: Optional[VersionResponse] = None
    suggested_versions: Dict[str, str] = {}  # patch, minor, major suggestions


class VersionFullResponse(VersionResponse):
    """Version response with full spec"""
    spec: Dict[str, Any]


class VersionDiff(BaseModel):
    """Diff between two versions"""
    version_a: int
    version_b: int
    nodes_added: List[str]
    nodes_removed: List[str]
    nodes_modified: List[str]
    edges_added: List[str]
    edges_removed: List[str]
    summary: str


# --- Helper Functions ---

def compute_spec_hash(spec: Dict[str, Any]) -> str:
    """Compute SHA-256 hash of workflow spec for change detection"""
    spec_json = json.dumps(spec, sort_keys=True, default=str)
    return hashlib.sha256(spec_json.encode()).hexdigest()


def parse_semantic_version(version_tag: Optional[str]) -> tuple[int, int, int]:
    """Parse a semantic version string into (major, minor, patch)"""
    if not version_tag:
        return (0, 0, 0)
    # Remove 'v' prefix if present
    tag = version_tag.lstrip('v')
    parts = tag.split('.')
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        return (0, 0, 0)


def suggest_next_versions(current_tag: Optional[str]) -> Dict[str, str]:
    """Suggest next semantic versions based on current version"""
    major, minor, patch = parse_semantic_version(current_tag)
    return {
        "patch": f"{major}.{minor}.{patch + 1}",
        "minor": f"{major}.{minor + 1}.0",
        "major": f"{major + 1}.0.0",
    }


def validate_semantic_version(
    proposed_tag: str,
    existing_tags: List[str],
    latest_tag: Optional[str]
) -> tuple[bool, Optional[str]]:
    """Validate a proposed semantic version tag.
    Returns (is_valid, error_message)
    """
    # Check format
    proposed = parse_semantic_version(proposed_tag)
    if proposed == (0, 0, 0) and proposed_tag not in ['0.0.0', 'v0.0.0']:
        return False, f"Invalid version format: {proposed_tag}. Use MAJOR.MINOR.PATCH format."

    # Normalize tag for comparison
    normalized = f"{proposed[0]}.{proposed[1]}.{proposed[2]}"

    # Check if version already exists
    existing_normalized = [f"{parse_semantic_version(t)[0]}.{parse_semantic_version(t)[1]}.{parse_semantic_version(t)[2]}" for t in existing_tags]
    if normalized in existing_normalized:
        return False, f"Version {normalized} already exists."

    # Check if version is higher than latest (optional - allow any version for flexibility)
    if latest_tag:
        latest = parse_semantic_version(latest_tag)
        if proposed <= latest:
            # This is a warning, not an error - allow it but inform user
            pass

    return True, None


def count_nodes_and_edges(spec: Dict[str, Any]) -> tuple[int, int]:
    """Count nodes and edges in a workflow spec"""
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    return len(nodes), len(edges)


def diff_specs(spec_a: Dict[str, Any], spec_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two workflow specs and return differences"""
    nodes_a = {n.get("id"): n for n in spec_a.get("nodes", [])}
    nodes_b = {n.get("id"): n for n in spec_b.get("nodes", [])}
    edges_a = {e.get("id"): e for e in spec_a.get("edges", [])}
    edges_b = {e.get("id"): e for e in spec_b.get("edges", [])}

    nodes_added = list(set(nodes_b.keys()) - set(nodes_a.keys()))
    nodes_removed = list(set(nodes_a.keys()) - set(nodes_b.keys()))
    nodes_modified = []

    # Check for modified nodes (same ID but different content)
    for node_id in set(nodes_a.keys()) & set(nodes_b.keys()):
        if json.dumps(nodes_a[node_id], sort_keys=True) != json.dumps(nodes_b[node_id], sort_keys=True):
            nodes_modified.append(node_id)

    edges_added = list(set(edges_b.keys()) - set(edges_a.keys()))
    edges_removed = list(set(edges_a.keys()) - set(edges_b.keys()))

    # Build summary
    changes = []
    if nodes_added:
        changes.append(f"+{len(nodes_added)} nodes")
    if nodes_removed:
        changes.append(f"-{len(nodes_removed)} nodes")
    if nodes_modified:
        changes.append(f"~{len(nodes_modified)} nodes modified")
    if edges_added:
        changes.append(f"+{len(edges_added)} edges")
    if edges_removed:
        changes.append(f"-{len(edges_removed)} edges")

    summary = ", ".join(changes) if changes else "No changes"

    return {
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "nodes_modified": nodes_modified,
        "edges_added": edges_added,
        "edges_removed": edges_removed,
        "summary": summary,
    }


# --- API Endpoints ---

@router.get("/workflows/{workflow_id}/versions", response_model=List[VersionResponse])
async def list_versions(
    workflow_id: uuid.UUID,
    include_drafts: bool = Query(True, description="Include draft versions"),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """List all versions for a workflow"""
    # Verify workflow exists and belongs to tenant
    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id
        )
    )
    workflow_result = await db.execute(workflow_stmt)
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Build query for versions
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )

    if not include_drafts:
        stmt = stmt.where(WorkflowVersion.is_draft == False)

    stmt = stmt.order_by(WorkflowVersion.version_number.desc())
    stmt = stmt.offset(offset).limit(limit)

    result = await db.execute(stmt)
    versions = result.scalars().all()

    return [v.to_dict() for v in versions]


# NOTE: Specific routes like /active, /compare, /by-number must come BEFORE /{version_id}
# to avoid FastAPI matching "active" as a UUID

@router.get("/workflows/{workflow_id}/versions/draft", response_model=DraftResponse)
async def get_draft_status(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get current draft status and suggested next versions"""
    # Get all versions for this workflow
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    ).order_by(WorkflowVersion.version_number.desc())

    result = await db.execute(stmt)
    versions = result.scalars().all()

    # Find draft and latest published
    draft = None
    latest_published = None
    existing_tags = []

    for v in versions:
        if v.is_draft and draft is None:
            draft = v
        if not v.is_draft and latest_published is None:
            latest_published = v
        if v.version_tag:
            existing_tags.append(v.version_tag)

    # Determine base version (what the draft is based on)
    based_on = latest_published

    # Generate version suggestions
    current_tag = latest_published.version_tag if latest_published else None
    suggested = suggest_next_versions(current_tag)

    return DraftResponse(
        has_draft=draft is not None,
        draft=VersionResponse(**draft.to_dict()) if draft else None,
        based_on_version=VersionResponse(**based_on.to_dict()) if based_on else None,
        latest_published=VersionResponse(**latest_published.to_dict()) if latest_published else None,
        suggested_versions=suggested,
    )


@router.get("/workflows/{workflow_id}/versions/active", response_model=VersionFullResponse)
async def get_active_version(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get the currently active (deployed) version"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.is_active == True
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="No active version found")

    return version.to_full_dict()


@router.get("/workflows/{workflow_id}/versions/by-number/{version_number}", response_model=VersionFullResponse)
async def get_version_by_number(
    workflow_id: uuid.UUID,
    version_number: int,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get a specific version by version number"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version_number == version_number,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return version.to_full_dict()


@router.get("/workflows/{workflow_id}/versions/{version_id}", response_model=VersionFullResponse)
async def get_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Get a specific version with full spec"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return version.to_full_dict()


@router.post("/workflows/{workflow_id}/versions", response_model=VersionResponse)
async def create_version(
    workflow_id: uuid.UUID,
    request: VersionCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Create a new version from current workflow state"""
    # Get current workflow
    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id
        )
    )
    workflow_result = await db.execute(workflow_stmt)
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get next version number (include tenant_id for defense-in-depth)
    max_version_stmt = select(func.max(WorkflowVersion.version_number)).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    max_result = await db.execute(max_version_stmt)
    max_version = max_result.scalar() or 0
    next_version = max_version + 1

    # Compute spec hash
    spec_hash = compute_spec_hash(workflow.spec)

    # Check if spec has changed
    # When publishing (is_draft=False), compare against last PUBLISHED version only
    # When saving draft (is_draft=True), compare against ANY last version
    if max_version > 0:
        if request.is_draft:
            # Draft: compare against any last version to avoid duplicate drafts
            last_version_stmt = select(WorkflowVersion).where(
                and_(
                    WorkflowVersion.workflow_id == workflow_id,
                    WorkflowVersion.tenant_id == tenant_id,
                    WorkflowVersion.version_number == max_version
                )
            )
        else:
            # Publish: compare against last PUBLISHED version only
            # This allows: save draft → publish (same spec as draft, but different from last published)
            last_version_stmt = select(WorkflowVersion).where(
                and_(
                    WorkflowVersion.workflow_id == workflow_id,
                    WorkflowVersion.tenant_id == tenant_id,
                    WorkflowVersion.is_draft == False  # noqa: E712 - SQLAlchemy requires == for SQL generation
                )
            ).order_by(WorkflowVersion.version_number.desc()).limit(1)

        last_result = await db.execute(last_version_stmt)
        last_version = last_result.scalar_one_or_none()

        # Note: last_version is None when no published versions exist (first publish scenario)
        # In this case, we should allow the operation
        if last_version and last_version.spec_hash == spec_hash:
            raise HTTPException(
                status_code=409,
                detail=f"No changes detected since version {last_version.version_number}"
            )

    # Count nodes and edges
    node_count, edge_count = count_nodes_and_edges(workflow.spec)

    # Create new version
    now = datetime.now(timezone.utc)
    new_version = WorkflowVersion(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        version_number=next_version,
        version_tag=request.version_tag,
        spec=workflow.spec,
        spec_hash=spec_hash,
        description=request.description,
        created_by=user_id,
        is_draft=request.is_draft,
        is_active=False,
        node_count=node_count,
        edge_count=edge_count,
        created_at=now,
    )

    db.add(new_version)
    await db.commit()
    # Skip refresh - with expire_on_commit=False and RLS, refresh can fail

    return new_version.to_dict()


@router.patch("/workflows/{workflow_id}/versions/{version_id}", response_model=VersionResponse)
async def update_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    request: VersionUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Update version metadata (description, tag)"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if request.description is not None:
        version.description = request.description
    if request.version_tag is not None:
        version.version_tag = request.version_tag

    await db.commit()
    # Skip refresh - with expire_on_commit=False and RLS, refresh can fail

    return version.to_dict()


@router.post("/workflows/{workflow_id}/versions/publish", response_model=VersionResponse)
async def publish_workflow(
    workflow_id: uuid.UUID,
    request: PublishRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Publish current workflow state as a new version with semantic versioning"""
    # Get current workflow
    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id
        )
    )
    workflow_result = await db.execute(workflow_stmt)
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get existing published versions (ordered by version_number DESC)
    existing_stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.is_draft == False
        )
    ).order_by(WorkflowVersion.version_number.desc())
    existing_result = await db.execute(existing_stmt)
    existing_versions = existing_result.scalars().all()

    existing_tags = [v.version_tag for v in existing_versions if v.version_tag]
    latest_published = existing_versions[0] if existing_versions else None
    latest_tag = latest_published.version_tag if latest_published else None

    # Check if there are draft versions - we'll delete them after publishing
    draft_stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.is_draft == True
        )
    )
    draft_result = await db.execute(draft_stmt)
    draft_versions = draft_result.scalars().all()

    # Validate the proposed version tag
    is_valid, error = validate_semantic_version(request.version_tag, existing_tags, latest_tag)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Get next version number
    max_version_stmt = select(func.max(WorkflowVersion.version_number)).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    max_result = await db.execute(max_version_stmt)
    max_version = max_result.scalar() or 0
    next_version = max_version + 1

    # Compute spec hash
    spec_hash = compute_spec_hash(workflow.spec)

    # Debug logging
    logger.info(f"[PUBLISH DEBUG] workflow_id={workflow_id}")
    logger.info(f"[PUBLISH DEBUG] current spec node_count={len(workflow.spec.get('nodes', []))}, edge_count={len(workflow.spec.get('edges', []))}")
    logger.info(f"[PUBLISH DEBUG] current spec_hash={spec_hash[:16]}...")
    if latest_published:
        logger.info(f"[PUBLISH DEBUG] latest_published version={latest_published.version_tag}, spec_hash={latest_published.spec_hash[:16] if latest_published.spec_hash else 'None'}...")

    # Check for changes against last published version
    if latest_published:
        if latest_published.spec_hash == spec_hash:
            logger.warning(f"[PUBLISH DEBUG] HASH MATCH - No changes detected! current={spec_hash[:16]}... == published={latest_published.spec_hash[:16] if latest_published.spec_hash else 'None'}...")
            raise HTTPException(
                status_code=409,
                detail=f"No changes detected since version {latest_published.version_tag or latest_published.version_number}"
            )

    # Count nodes and edges
    node_count, edge_count = count_nodes_and_edges(workflow.spec)

    # Create published version
    now = datetime.now(timezone.utc)
    new_version = WorkflowVersion(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        version_number=next_version,
        version_tag=request.version_tag,
        spec=workflow.spec,
        spec_hash=spec_hash,
        description=request.description,
        created_by=user_id,
        is_draft=False,
        is_active=True,  # New published version becomes active
        node_count=node_count,
        edge_count=edge_count,
        created_at=now,
        published_at=now,
    )

    # Deactivate other versions
    deactivate_stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.is_active == True
        )
    )
    deactivate_result = await db.execute(deactivate_stmt)
    for v in deactivate_result.scalars().all():
        v.is_active = False

    # Delete all draft versions (they're being replaced by this published version)
    for draft_version in draft_versions:
        await db.delete(draft_version)

    db.add(new_version)
    await db.commit()

    return new_version.to_dict()


@router.post("/workflows/{workflow_id}/versions/{version_id}/activate", response_model=VersionResponse)
async def activate_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Activate (deploy) a specific version"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Publish if draft
    if version.is_draft:
        version.is_draft = False
        version.published_at = datetime.now(timezone.utc)

    # Deactivate other versions first
    deactivate_stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id,
            WorkflowVersion.is_active == True,
            WorkflowVersion.id != version_id
        )
    )
    deactivate_result = await db.execute(deactivate_stmt)
    for v in deactivate_result.scalars().all():
        v.is_active = False

    # Activate this version
    version.is_active = True

    await db.commit()
    # Skip refresh - with expire_on_commit=False and RLS, refresh can fail

    return version.to_dict()


@router.post("/workflows/{workflow_id}/versions/{version_id}/rollback", response_model=Dict[str, Any])
async def rollback_to_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Rollback workflow to a specific version (updates current spec)"""
    # Get the version to rollback to
    version_stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    version_result = await db.execute(version_stmt)
    version = version_result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get the workflow - MUST include tenant_id filter for security
    workflow_stmt = select(Workflow).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id
        )
    )
    workflow_result = await db.execute(workflow_stmt)
    workflow = workflow_result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Update workflow spec to version's spec
    workflow.spec = version.spec
    workflow.updated_at = datetime.now(timezone.utc)

    # Activate this version
    version.is_active = True

    await db.commit()

    return {
        "success": True,
        "message": f"Workflow rolled back to version {version.version_number}",
        "version_number": version.version_number,
        "workflow_id": str(workflow_id),
    }


@router.get("/workflows/{workflow_id}/versions/compare", response_model=VersionDiff)
async def compare_versions(
    workflow_id: uuid.UUID,
    version_a: int = Query(..., description="First version number"),
    version_b: int = Query(..., description="Second version number"),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Compare two versions and return diff"""
    # Get version A
    stmt_a = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version_number == version_a,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result_a = await db.execute(stmt_a)
    ver_a = result_a.scalar_one_or_none()

    if not ver_a:
        raise HTTPException(status_code=404, detail=f"Version {version_a} not found")

    # Get version B
    stmt_b = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version_number == version_b,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result_b = await db.execute(stmt_b)
    ver_b = result_b.scalar_one_or_none()

    if not ver_b:
        raise HTTPException(status_code=404, detail=f"Version {version_b} not found")

    # Compute diff
    diff = diff_specs(ver_a.spec, ver_b.spec)

    return VersionDiff(
        version_a=version_a,
        version_b=version_b,
        **diff
    )


@router.delete("/workflows/{workflow_id}/versions/{version_id}")
async def delete_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Delete a version (only drafts or inactive versions can be deleted)"""
    stmt = select(WorkflowVersion).where(
        and_(
            WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
    )
    result = await db.execute(stmt)
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if version.is_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete active version. Activate another version first."
        )

    await db.delete(version)
    await db.commit()

    return {"success": True, "message": f"Version {version.version_number} deleted"}


# ============================================
# Version Comparison (Enterprise)
# ============================================

@router.get("/{workflow_id}/versions/compare")
async def compare_versions(
    workflow_id: uuid.UUID,
    base_version: int = Query(..., description="Base version number"),
    compare_version: int = Query(..., description="Compare version number"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Compare two workflow versions.

    Returns detailed diff showing added/removed/modified nodes and edges.

    Enterprise feature for workflow change tracking and audit.
    Disabled by default - enable with ENABLE_VERSION_DIFF=true.
    """
    from app.config import get_settings
    from app.services.workflow_diff import compute_workflow_diff

    settings = get_settings()
    if not settings.ENABLE_VERSION_DIFF:
        raise HTTPException(
            status_code=501,
            detail="Version comparison is disabled. Enable with ENABLE_VERSION_DIFF=true"
        )

    # Fetch both versions
    query_a = select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id,
        WorkflowVersion.tenant_id == tenant_id,
        WorkflowVersion.version_number == base_version
    )
    result_a = await db.execute(query_a)
    version_a = result_a.scalar_one_or_none()

    if not version_a:
        raise HTTPException(404, f"Base version {base_version} not found")

    query_b = select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id,
        WorkflowVersion.tenant_id == tenant_id,
        WorkflowVersion.version_number == compare_version
    )
    result_b = await db.execute(query_b)
    version_b = result_b.scalar_one_or_none()

    if not version_b:
        raise HTTPException(404, f"Compare version {compare_version} not found")

    # Compute diff
    diff = compute_workflow_diff(version_a.spec, version_b.spec)

    return {
        "workflow_id": str(workflow_id),
        "base_version": base_version,
        "compare_version": compare_version,
        "nodes_added": diff.nodes_added,
        "nodes_removed": diff.nodes_removed,
        "nodes_modified": [nm.model_dump() for nm in diff.nodes_modified],
        "edges_added": diff.edges_added,
        "edges_removed": diff.edges_removed,
        "summary": diff.summary
    }
