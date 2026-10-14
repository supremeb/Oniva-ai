from typing import List, Optional
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.core.database import get_db
from app.models.skills import Skill, SkillExecution
from app.schemas.skill import SkillRead, SkillCreate, SkillUpdate
from app.agents.skills.registry import SkillRegistry
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser

router = APIRouter()

@router.get("", response_model=List[SkillRead])
async def list_skills(
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    List all available skills (system + custom)
    """
    # 1. Get custom skills from DB (filter by tenant for custom skills)
    query = select(Skill).where(
        Skill.enabled == True,
        or_(Skill.is_system == True, Skill.tenant_id == tenant_id)
    )
    if category:
        query = query.where(Skill.category == category)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    db_skills = result.scalars().all()
    
    # 2. Get system skills from Registry (Manifests)
    from uuid import uuid5, NAMESPACE_DNS
    registry_skills = SkillRegistry.get_all_skills() # Dict[name, SkillManifest]
    
    system_skills_list = []
    # Create a deterministic namespace for system skills
    SYSTEM_SKILL_NAMESPACE = uuid5(NAMESPACE_DNS, "system.skills.oniva")

    for name, manifest in registry_skills.items():
        if category and manifest.category != category:
            continue
            
        # Generate deterministic ID
        skill_id = uuid5(SYSTEM_SKILL_NAMESPACE, name)
        
        # Check if already in DB skills (override system with custom if name collision? 
        # Or usually system skills are distinct. DB skills have `is_custom=True` typically).
        # We'll assume names are unique enough or DB takes precedence if we want.
        # But for now, let's just append system skills that aren't in DB (by name).
        if any(s.name == name for s in db_skills):
            continue

        system_skills_list.append(SkillRead(
            id=skill_id,
            name=manifest.name,
            description=manifest.description,
            category=manifest.category,
            is_custom=False,
            is_system=True,
            enabled=True,
            skill_dependencies=manifest.requires,
            mcp_dependencies=manifest.mcp_dependencies,
            mock=manifest.mock,
            author=manifest.author,
            version=manifest.version,
            code=None,
            configuration={},
            created_at=None,
            updated_at=None
        ))

    return db_skills + system_skills_list

@router.post("", response_model=SkillRead)
async def create_skill(
    skill_in: SkillCreate,
    db: AsyncSession = Depends(get_db),
    # user: User = Depends(get_current_user) # Assuming auth
):
    """
    Create a new custom skill
    """
    # Validate code security for custom skills
    if skill_in.code:
        from app.agents.skills.security import SecurityValidator
        is_valid, errors = SecurityValidator.validate_code(skill_in.code)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Security Validation Failed: {'; '.join(errors)}")

    # Prepare data for DB model
    skill_data = skill_in.model_dump(exclude={"author", "config_schema"}) 
    
    # Map Pydantic fields to SQLAlchemy Columns
    if "skill_dependencies" in skill_data:
        skill_data["requires"] = skill_data.pop("skill_dependencies")
    if "config" in skill_data:
        skill_data["configuration"] = skill_data.pop("config")

    skill = Skill(**skill_data)
    skill.is_custom = True
    # skill.author_id = user.id
    
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill

@router.get("/categories", response_model=List[str])
async def list_categories(db: AsyncSession = Depends(get_db)):
    """
    List all skill categories
    """
    # Get distinct categories from DB
    query = select(Skill.category).distinct()
    result = await db.execute(query)
    categories = list(result.scalars().all())

    # Add system categories if missed
    system_skills = SkillRegistry.get_all_skills()
    for s in system_skills.values():
        if s.category not in categories:
            categories.append(s.category)

    return sorted(list(set(categories)))


@router.get("/executions/export")
async def export_skill_executions(
    start_date: datetime = Query(..., description="Start date for export range"),
    end_date: datetime = Query(..., description="End date for export range"),
    skill_name: Optional[str] = Query(None, description="Filter by skill name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    format: str = Query("json", description="Export format: json or csv"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Export skill executions for audit/compliance purposes.

    Supports JSON and CSV formats for regulatory reporting.
    Results are filtered by tenant_id for security.
    """
    import csv
    import io

    # Build query with tenant isolation
    query = select(SkillExecution).where(
        SkillExecution.tenant_id == tenant_id,
        SkillExecution.created_at >= start_date,
        SkillExecution.created_at <= end_date
    )

    if skill_name:
        query = query.where(SkillExecution.skill_name == skill_name)
    if status:
        query = query.where(SkillExecution.status == status)

    query = query.order_by(SkillExecution.created_at.asc())

    result = await db.execute(query)
    executions = result.scalars().all()

    # Build export data
    export_data = []
    for e in executions:
        export_data.append({
            "id": str(e.id),
            "skill_name": e.skill_name,
            "status": e.status,
            "duration_ms": e.duration_ms,
            "error_message": e.error_message,
            "error_type": e.error_type,
            "user_id": str(e.user_id) if e.user_id else None,
            "session_id": e.session_id,
            "workflow_id": str(e.workflow_id) if e.workflow_id else None,
            "workflow_run_id": str(e.workflow_run_id) if e.workflow_run_id else None,
            "agent_id": e.agent_id,
            "input_hash": e.input_hash,
            "output_hash": e.output_hash,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "completed_at": e.completed_at.isoformat() if e.completed_at else None,
        })

    if format == "csv":
        # Generate CSV
        output = io.StringIO()
        if export_data:
            writer = csv.DictWriter(output, fieldnames=export_data[0].keys())
            writer.writeheader()
            writer.writerows(export_data)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=skill_executions_{start_date.date()}_{end_date.date()}.csv"
            }
        )
    else:
        # Return JSON
        return {
            "export_info": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "tenant_id": str(tenant_id),
                "total_records": len(export_data),
                "filters": {
                    "skill_name": skill_name,
                    "status": status
                }
            },
            "executions": export_data
        }


@router.get("/{skill_id}", response_model=SkillRead)
async def get_skill(
    skill_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    Get skill details
    """
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillRead)
async def update_skill(
    skill_id: UUID,
    skill_in: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Update an existing custom skill.

    Only custom skills (is_system=False) owned by the tenant can be updated.
    System skills are read-only.
    """
    # Fetch skill
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Security: Cannot update system skills
    if skill.is_system:
        raise HTTPException(
            status_code=403,
            detail="System skills cannot be modified"
        )

    # Security: Tenant isolation - custom skills must belong to tenant
    if skill.tenant_id and skill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Validate code if provided
    if skill_in.code:
        from app.agents.skills.security import SecurityValidator
        is_valid, errors = SecurityValidator.validate_code(skill_in.code)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Security validation failed: {'; '.join(errors)}"
            )

    # Update fields
    update_data = skill_in.model_dump(exclude_unset=True)

    # Handle field aliases
    if "skill_dependencies" in update_data:
        update_data["requires"] = update_data.pop("skill_dependencies")
    if "config" in update_data:
        update_data["configuration"] = update_data.pop("config")

    from app.utils.safe_update import safe_update
    from app.schemas.skill import SkillUpdate
    safe_update(skill, update_data, SkillUpdate)

    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Delete a custom skill (soft delete).

    Only custom skills (is_system=False) owned by the tenant can be deleted.
    System skills cannot be deleted.
    """
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Security: Cannot delete system skills
    if skill.is_system:
        raise HTTPException(
            status_code=403,
            detail="System skills cannot be deleted"
        )

    # Security: Tenant isolation
    if skill.tenant_id and skill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Soft delete: disable the skill instead of hard delete
    skill.enabled = False

    await db.commit()

    return {"message": "Skill deleted successfully", "skill_id": str(skill_id)}


@router.get("/{skill_id}/executions")
async def list_skill_executions(
    skill_id: UUID,
    status: Optional[str] = Query(None, description="Filter by status (success, failed, timeout)"),
    since: Optional[datetime] = Query(None, description="Filter by created_at >= since"),
    until: Optional[datetime] = Query(None, description="Filter by created_at <= until"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=250),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    List execution history for a specific skill.

    Provides audit trail for compliance and debugging.
    Results are filtered by tenant_id for security.
    """
    # Verify skill exists and user has access
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # For custom skills, verify tenant owns it
    if not skill.is_system and skill.tenant_id and skill.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Build query with tenant isolation
    query = select(SkillExecution).where(
        SkillExecution.tenant_id == tenant_id,
        SkillExecution.skill_id == skill_id
    )

    if status:
        query = query.where(SkillExecution.status == status)
    if since:
        query = query.where(SkillExecution.created_at >= since)
    if until:
        query = query.where(SkillExecution.created_at <= until)

    query = query.order_by(SkillExecution.created_at.desc())
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    executions = result.scalars().all()

    return {
        "skill_id": str(skill_id),
        "skill_name": skill.name,
        "executions": [
            {
                "id": str(e.id),
                "status": e.status,
                "duration_ms": e.duration_ms,
                "error_message": e.error_message,
                "user_id": str(e.user_id) if e.user_id else None,
                "session_id": e.session_id,
                "workflow_id": str(e.workflow_id) if e.workflow_id else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in executions
        ],
        "pagination": {
            "skip": skip,
            "limit": limit,
            "has_more": len(executions) == limit
        }
    }
