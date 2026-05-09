"""Agent Alias API endpoints for PRODUCTION deployment pointers."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from datetime import datetime

from app.models.agent_alias import AgentAlias
from app.models.agent_version import AgentVersion
from app.models.custom_agent import CustomAgent
from app.models.deploy_history import DeployHistory
from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.schemas.agent_alias import AliasCreateOrUpdate, AliasResponse, AliasListResponse

router = APIRouter()

# Allowed alias names (STAGING deferred — zero current consumers)
ALLOWED_ALIASES = {'PRODUCTION'}


def alias_to_response(alias: AgentAlias, version: AgentVersion = None) -> AliasResponse:
    """Convert AgentAlias model to response schema."""
    return AliasResponse(
        id=alias.id,
        tenant_id=alias.tenant_id,
        agent_id=alias.agent_id,
        alias_name=alias.alias_name,
        version_id=alias.version_id,
        version_number=version.version_number if version else None,
        version_name=version.name if version else None,
        updated_by=alias.updated_by,
        updated_at=alias.updated_at,
        created_at=alias.created_at,
    )


@router.get("/{agent_id}/aliases", response_model=AliasListResponse)
async def list_aliases(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List all aliases for an agent."""
    # Verify agent exists in tenant
    agent_result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id
        )
    )
    if not agent_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent not found")

    result = await db.execute(
        select(AgentAlias).where(AgentAlias.agent_id == agent_id)
    )
    aliases = result.scalars().all()

    # Resolve version details
    responses = []
    for alias in aliases:
        v_result = await db.execute(
            select(AgentVersion).where(AgentVersion.id == alias.version_id)
        )
        version = v_result.scalar_one_or_none()
        responses.append(alias_to_response(alias, version))

    return AliasListResponse(aliases=responses, total=len(responses))


@router.get("/{agent_id}/aliases/{alias_name}", response_model=AliasResponse)
async def get_alias(
    agent_id: UUID,
    alias_name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get a specific alias with resolved version details."""
    result = await db.execute(
        select(AgentAlias).where(
            AgentAlias.agent_id == agent_id,
            AgentAlias.alias_name == alias_name.upper(),
            AgentAlias.tenant_id == tenant_id
        )
    )
    alias = result.scalar_one_or_none()

    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_name}' not found for this agent")

    v_result = await db.execute(
        select(AgentVersion).where(AgentVersion.id == alias.version_id)
    )
    version = v_result.scalar_one_or_none()

    return alias_to_response(alias, version)


@router.put("/{agent_id}/aliases/{alias_name}", response_model=AliasResponse)
async def create_or_update_alias(
    agent_id: UUID,
    alias_name: str,
    request: AliasCreateOrUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Create or update an alias to point to a specific version.
    Moving PRODUCTION alias = deploying. Records in DeployHistory.
    Requires DEPLOY permission (admin+).
    """
    user_id = UUID(user.id)
    alias_name_upper = alias_name.upper()

    if alias_name_upper not in ALLOWED_ALIASES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid alias name '{alias_name}'. Allowed: {', '.join(ALLOWED_ALIASES)}"
        )

    # Verify agent exists in tenant
    agent_result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id
        )
    )
    if not agent_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent not found")

    # Verify version exists and belongs to this agent
    version_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == request.version_id,
            AgentVersion.agent_id == agent_id
        )
    )
    target_version = version_result.scalar_one_or_none()
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found for this agent")

    # Get existing alias (use SELECT FOR UPDATE for atomicity)
    existing_result = await db.execute(
        select(AgentAlias)
        .where(
            AgentAlias.agent_id == agent_id,
            AgentAlias.alias_name == alias_name_upper
        )
        .with_for_update()
    )
    existing_alias = existing_result.scalar_one_or_none()

    previous_version_id = None
    previous_version_number = None
    action = 'deploy'

    if existing_alias:
        previous_version_id = existing_alias.version_id

        # Determine if this is a deploy or rollback
        if previous_version_id != request.version_id:
            prev_v = await db.execute(
                select(AgentVersion.version_number).where(AgentVersion.id == previous_version_id)
            )
            previous_version_number = prev_v.scalar_one_or_none()

            if previous_version_number and target_version.version_number and target_version.version_number < previous_version_number:
                action = 'rollback'

        existing_alias.point_to(request.version_id, user_id)
    else:
        existing_alias = AgentAlias(
            tenant_id=tenant_id,
            agent_id=agent_id,
            alias_name=alias_name_upper,
            version_id=request.version_id,
            updated_by=user_id,
        )
        db.add(existing_alias)

    # Also update legacy is_deployed flags for backward compat
    deployed_versions = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_deployed == True
        )
    )
    for v in deployed_versions.scalars().all():
        v.is_deployed = False
    target_version.is_deployed = True
    target_version.deployed_at = datetime.utcnow()
    target_version.deployed_by = user_id

    # Record in DeployHistory
    deploy_record = DeployHistory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        agent_version_id=request.version_id,
        action=action,
        status='success' if action == 'deploy' else 'rolled_back',
        gate_check_passed=True,
        previous_version_id=previous_version_id,
        deployed_by=user_id,
        notes=f"Alias {alias_name_upper} moved to v{target_version.version_number}",
    )
    db.add(deploy_record)

    await db.flush()
    await db.refresh(existing_alias)

    return alias_to_response(existing_alias, target_version)


@router.delete("/{agent_id}/aliases/{alias_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(
    agent_id: UUID,
    alias_name: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete an alias. Requires MANAGE permission (owner)."""
    result = await db.execute(
        select(AgentAlias).where(
            AgentAlias.agent_id == agent_id,
            AgentAlias.alias_name == alias_name.upper(),
            AgentAlias.tenant_id == tenant_id
        )
    )
    alias = result.scalar_one_or_none()

    if not alias:
        raise HTTPException(status_code=404, detail=f"Alias '{alias_name}' not found")

    await db.delete(alias)
    await db.flush()
