"""Agent Versions API endpoints for sequential versioning of agent configurations."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func as sa_func
from uuid import UUID
from datetime import datetime

from app.models.agent_version import AgentVersion
from app.models.agent_alias import AgentAlias
from app.models.custom_agent import CustomAgent
from app.models.user import User
from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.schemas.agent_version import (
    AgentVersionCreate,
    AgentVersionUpdate,
    AgentVersionResponse,
    AgentVersionListResponse,
    AgentVersionDetailResponse,
    DraftStatusResponse,
    DeployVersionRequest,
    DeployVersionResponse,
    CompareVersionsRequest,
    CompareVersionsResponse,
    ConfigDiff,
    RollbackRequest,
    RollbackResponse,
)

router = APIRouter()


def version_to_response(version: AgentVersion) -> AgentVersionResponse:
    """Convert AgentVersion model to response schema."""
    return AgentVersionResponse(
        id=version.id,
        tenant_id=version.tenant_id,
        agent_id=version.agent_id,
        version_number=version.version_number,
        version=version.legacy_version,  # maps to legacy_version via alias
        name=version.name,
        config=version.config or {},
        is_deployed=version.is_deployed,
        deployed_at=version.deployed_at,
        deployed_by=version.deployed_by,
        accuracy=version.accuracy,
        accuracy_at_deploy=version.accuracy_at_deploy,
        deploy_blocked=version.deploy_blocked,
        block_reason=version.block_reason,
        visibility=version.visibility or 'private',
        published_at=version.published_at,
        compliance_tags=version.compliance_tags,
        created_by=version.created_by,
        notes=version.notes,
        created_at=version.created_at,
    )


async def get_agent_config_snapshot(agent: CustomAgent) -> dict:
    """Create a complete config snapshot from current agent state.

    Captures ALL behavioral fields so deployed versions have full agent config.
    """
    return {
        "name": agent.name,
        "description": agent.description,
        "icon": agent.icon,
        "agent_type": agent.agent_type,
        "model": agent.model,
        "system_prompt": agent.system_prompt,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "skills": agent.skills or [],
        "connector_ids": [str(c) for c in (agent.connector_ids or [])],
        "config": agent.config or {},
        # Expanded fields (previously missing from snapshot)
        "knowledge_base_ids": [str(k) for k in (agent.knowledge_base_ids or [])],
        "workflow_ids": [str(w) for w in (agent.workflow_ids or [])],
        "is_supervisor": agent.is_supervisor or False,
        "child_agent_config": agent.child_agent_config or [],
        "execution_mode": agent.execution_mode or 'sequential',
        "resource_constraints": agent.resource_constraints or {},
        "background_config": agent.background_config,
    }


async def _get_next_version_number(db: AsyncSession, agent_id: UUID) -> int:
    """Get the next sequential version number for an agent."""
    result = await db.execute(
        select(sa_func.max(AgentVersion.version_number))
        .where(AgentVersion.agent_id == agent_id)
    )
    max_version = result.scalar()
    return (max_version or 0) + 1


async def _get_production_alias(db: AsyncSession, agent_id: UUID) -> AgentAlias | None:
    """Get the PRODUCTION alias for an agent, if it exists."""
    result = await db.execute(
        select(AgentAlias).where(
            AgentAlias.agent_id == agent_id,
            AgentAlias.alias_name == 'PRODUCTION'
        )
    )
    return result.scalar_one_or_none()


@router.post("/", response_model=AgentVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_version(
    version_data: AgentVersionCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new version snapshot of an agent's configuration.
    Version number is auto-assigned sequentially (v1, v2, v3...).
    """
    user_id = UUID(user.id)

    # Get the agent
    result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == version_data.agent_id,
            CustomAgent.tenant_id == tenant_id
        )
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    # Auto-assign next sequential version number
    next_version = await _get_next_version_number(db, agent.id)

    # Create config snapshot
    config_snapshot = await get_agent_config_snapshot(agent)

    # Create version
    version = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=next_version,
        name=version_data.name,
        config=config_snapshot,
        notes=version_data.notes,
        created_by=user_id,
        is_deployed=False,
    )

    db.add(version)
    await db.flush()
    await db.refresh(version)

    return version_to_response(version)


@router.get("/agent/{agent_id}", response_model=AgentVersionListResponse)
async def list_agent_versions(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List all versions for a specific agent, newest first."""
    # Verify agent exists in tenant
    agent_result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id
        )
    )
    if not agent_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    # Get versions ordered by version_number descending
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(desc(AgentVersion.version_number))
    )
    versions = result.scalars().all()

    # Get PRODUCTION alias
    prod_alias = await _get_production_alias(db, agent_id)
    production_version_number = None
    current_deployed = None
    if prod_alias:
        for v in versions:
            if v.id == prod_alias.version_id:
                production_version_number = v.version_number
                current_deployed = f"v{v.version_number}"
                break

    # Fallback: check legacy is_deployed flag
    if not current_deployed:
        for v in versions:
            if v.is_deployed:
                current_deployed = v.legacy_version or f"v{v.version_number}"
                break

    return AgentVersionListResponse(
        versions=[version_to_response(v) for v in versions],
        total=len(versions),
        current_deployed=current_deployed,
        production_version_number=production_version_number,
    )


@router.get("/agent/{agent_id}/draft-status", response_model=DraftStatusResponse)
async def get_draft_status(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Check whether the current agent draft has changes since the latest version."""
    # Get agent
    agent_result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id
        )
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get latest version
    latest_result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(desc(AgentVersion.version_number))
        .limit(1)
    )
    latest_version = latest_result.scalar_one_or_none()

    if not latest_version:
        return DraftStatusResponse(has_changes=True, latest_version_number=None, changed_fields=[], production_version_number=None)

    # Snapshot current agent and compare
    current_config = await get_agent_config_snapshot(agent)
    version_config = latest_version.config or {}

    changed_fields = []
    for key in set(current_config.keys()) | set(version_config.keys()):
        if current_config.get(key) != version_config.get(key):
            changed_fields.append(key)

    # Get production version number
    prod_alias = await _get_production_alias(db, agent_id)
    prod_version_num = None
    if prod_alias:
        prod_v_result = await db.execute(
            select(AgentVersion.version_number).where(AgentVersion.id == prod_alias.version_id)
        )
        prod_version_num = prod_v_result.scalar_one_or_none()

    return DraftStatusResponse(
        has_changes=len(changed_fields) > 0,
        latest_version_number=latest_version.version_number,
        changed_fields=changed_fields,
        production_version_number=prod_version_num,
    )


@router.get("/{version_id}", response_model=AgentVersionDetailResponse)
async def get_version(
    version_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get a specific version with full details."""
    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.tenant_id == tenant_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get deployer info
    deployer_name = deployer_email = None
    if version.deployed_by:
        deployer_result = await db.execute(select(User).where(User.id == version.deployed_by))
        deployer = deployer_result.scalar_one_or_none()
        if deployer:
            deployer_name = deployer.name
            deployer_email = deployer.email

    # Get creator info
    creator_name = creator_email = None
    if version.created_by:
        creator_result = await db.execute(select(User).where(User.id == version.created_by))
        creator = creator_result.scalar_one_or_none()
        if creator:
            creator_name = creator.name
            creator_email = creator.email

    response = version_to_response(version)
    return AgentVersionDetailResponse(
        **response.model_dump(),
        deployer_name=deployer_name,
        deployer_email=deployer_email,
        creator_name=creator_name,
        creator_email=creator_email,
    )


@router.patch("/{version_id}", response_model=AgentVersionResponse)
async def update_version(
    version_id: UUID,
    version_data: AgentVersionUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Update version metadata (notes, name)."""
    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.tenant_id == tenant_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if version_data.notes is not None:
        version.notes = version_data.notes
    if version_data.name is not None:
        version.name = version_data.name

    await db.flush()
    await db.refresh(version)

    return version_to_response(version)


@router.delete("/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(
    version_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete a version. Cannot delete if it's referenced by a PRODUCTION alias."""
    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.tenant_id == tenant_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Check if referenced by any alias
    alias_result = await db.execute(
        select(AgentAlias).where(AgentAlias.version_id == version_id)
    )
    if alias_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a version referenced by a deployment alias. Remove the alias first."
        )

    # Legacy check
    if version.is_deployed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a deployed version."
        )

    await db.delete(version)
    await db.flush()


@router.post("/deploy", response_model=DeployVersionResponse)
async def deploy_version(
    request: DeployVersionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Deploy a specific version (legacy endpoint — use alias endpoints for new deployments)."""
    user_id = UUID(user.id)

    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == request.version_id,
            AgentVersion.tenant_id == tenant_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Undeploy other versions (legacy is_deployed flag)
    existing_deployed = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == version.agent_id,
            AgentVersion.is_deployed == True
        )
    )
    for v in existing_deployed.scalars().all():
        v.is_deployed = False

    version.is_deployed = True
    version.deployed_at = datetime.utcnow()
    version.deployed_by = user_id

    # Also create/update PRODUCTION alias
    prod_alias = await _get_production_alias(db, version.agent_id)
    if prod_alias:
        prod_alias.point_to(version.id, user_id)
    else:
        prod_alias = AgentAlias(
            tenant_id=tenant_id,
            agent_id=version.agent_id,
            alias_name='PRODUCTION',
            version_id=version.id,
            updated_by=user_id,
        )
        db.add(prod_alias)

    await db.flush()
    await db.refresh(version)

    return DeployVersionResponse(
        success=True,
        version_number=version.version_number,
        legacy_version=version.legacy_version,
        deployed_at=version.deployed_at,
        message=f"Version v{version.version_number} deployed successfully"
    )


@router.post("/compare", response_model=CompareVersionsResponse)
async def compare_versions(
    request: CompareVersionsRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Compare two versions and show differences."""
    result_a = await db.execute(
        select(AgentVersion).where(AgentVersion.id == request.version_a, AgentVersion.tenant_id == tenant_id)
    )
    version_a = result_a.scalar_one_or_none()

    result_b = await db.execute(
        select(AgentVersion).where(AgentVersion.id == request.version_b, AgentVersion.tenant_id == tenant_id)
    )
    version_b = result_b.scalar_one_or_none()

    if not version_a or not version_b:
        raise HTTPException(status_code=404, detail="One or both versions not found")

    if version_a.agent_id != version_b.agent_id:
        raise HTTPException(status_code=400, detail="Versions must belong to the same agent")

    # Compare configs
    differences = []
    config_a = version_a.config or {}
    config_b = version_b.config or {}

    all_keys = set(config_a.keys()) | set(config_b.keys())
    for key in sorted(all_keys):
        val_a = config_a.get(key)
        val_b = config_b.get(key)
        if val_a != val_b:
            differences.append(ConfigDiff(field=key, old_value=val_a, new_value=val_b))

    summary = f"{len(differences)} field(s) changed between v{version_a.version_number} and v{version_b.version_number}"

    return CompareVersionsResponse(
        version_a=version_a.version_number,
        version_b=version_b.version_number,
        differences=differences,
        summary=summary,
    )


@router.post("/rollback", response_model=RollbackResponse)
async def rollback_to_version(
    request: RollbackRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Rollback to a previous version by moving the PRODUCTION alias.
    Does NOT copy config back to CustomAgent (alias pointer swap only).
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == request.version_id,
            AgentVersion.tenant_id == tenant_id
        )
    )
    target_version = result.scalar_one_or_none()

    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get current PRODUCTION alias
    prod_alias = await _get_production_alias(db, target_version.agent_id)
    from_version_num = None

    if prod_alias:
        # Get the current production version number for the response
        current_prod = await db.execute(
            select(AgentVersion.version_number).where(AgentVersion.id == prod_alias.version_id)
        )
        from_version_num = current_prod.scalar_one_or_none()

        # Move alias pointer
        prod_alias.point_to(target_version.id, user_id)
    else:
        # Create PRODUCTION alias pointing to target
        prod_alias = AgentAlias(
            tenant_id=tenant_id,
            agent_id=target_version.agent_id,
            alias_name='PRODUCTION',
            version_id=target_version.id,
            updated_by=user_id,
        )
        db.add(prod_alias)

    # Also update legacy is_deployed flags for backward compat
    existing_deployed = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == target_version.agent_id,
            AgentVersion.is_deployed == True
        )
    )
    for v in existing_deployed.scalars().all():
        v.is_deployed = False
    target_version.is_deployed = True
    target_version.deployed_at = datetime.utcnow()
    target_version.deployed_by = user_id

    await db.flush()

    return RollbackResponse(
        success=True,
        from_version=from_version_num,
        to_version=target_version.version_number,
        message=f"Successfully rolled back to v{target_version.version_number}"
    )
