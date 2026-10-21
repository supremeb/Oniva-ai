from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

from app.models.custom_agent import CustomAgent
from app.models.team import TeamMembership, TeamRole
from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.services.agent_rbac import AgentRBACService, AgentPermission
from app.services.agent_registry import AgentRegistryService
from app.exceptions import PermissionDeniedError
from app.utils.safe_update import safe_update

router = APIRouter()


async def get_rbac_service(db: AsyncSession = Depends(get_db, scope="function")) -> AgentRBACService:
    """Dependency to get RBAC service instance."""
    return AgentRBACService(db)


async def get_registry_service(db: AsyncSession = Depends(get_db, scope="function")) -> AgentRegistryService:
    """Dependency to get Registry service instance."""
    return AgentRegistryService(db)


# Pydantic schemas
class CustomAgentCreate(BaseModel):
    id: Optional[UUID] = None  # Client-provided UUID for localStorage sync; generated if omitted
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    icon: str = "Robot"
    agent_type: str = "custom"
    model: str = "gpt-4o"
    system_prompt: Optional[str] = Field(None, max_length=50000)  # S9: limit system prompt size
    instruction_format: str = "plain_text"  # plain_text | tiptap_json
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=32000)
    skills: List[str] = Field(default_factory=list)
    connectors: List[str] = Field(default_factory=list)
    workflow_ids: Optional[List[str]] = None
    config: dict = Field(default_factory=dict)
    team_id: Optional[UUID] = None  # If set, agent belongs to team; requires MEMBER+ role


class CustomAgentUpdate(BaseModel):
    model_config = {"extra": "ignore"}

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    icon: Optional[str] = None
    agent_type: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = Field(None, max_length=50000)  # S9: limit system prompt size
    instruction_format: Optional[str] = None  # plain_text | tiptap_json
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=32000)
    skills: Optional[List[str]] = None
    connectors: Optional[List[str]] = None
    workflow_ids: Optional[List[str]] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None
    background_config: Optional[dict] = None  # {enabled, cron_expression, timezone, webhook_url, webhook_headers}
    resource_constraints: Optional[dict] = None  # {max_concurrent, token_budget, timeout_seconds}
    row_version: Optional[int] = None  # Optimistic locking: client sends current version


class CustomAgentResponse(BaseModel):
    id: str
    user_id: str
    team_id: Optional[str] = None
    name: str
    description: Optional[str]
    icon: str
    type: str
    model: str
    system_prompt: Optional[str]
    instruction_format: str = "plain_text"
    temperature: float
    max_tokens: int
    skills: List[str]
    connectors: List[str]
    workflow_ids: List[str] = Field(default_factory=list)
    config: dict
    is_active: bool
    is_template: bool
    # Phase B: Registry
    visibility: str = "private"
    category: Optional[str] = None
    published_at: Optional[str] = None
    clone_count: int = 0
    cloned_from_id: Optional[str] = None
    compliance_tags: List[str] = Field(default_factory=list)
    compliance_notes: Optional[str] = None
    # Phase B: Orchestration
    is_supervisor: bool = False
    child_agent_config: list = Field(default_factory=list)
    execution_mode: str = "sequential"
    resource_constraints: dict = Field(default_factory=dict)
    background_config: Optional[dict] = None
    # Metadata
    created_at: str
    updated_at: str
    last_used_at: Optional[str]
    usage_count: int
    row_version: int = 1

    class Config:
        from_attributes = True


class PaginatedCustomAgentsResponse(BaseModel):
    agents: List[CustomAgentResponse]
    total: int
    limit: int
    offset: int


class PublishAgentRequest(BaseModel):
    visibility: str = Field(..., pattern="^(team|tenant)$")
    category: Optional[str] = Field(None, max_length=50)
    compliance_tags: Optional[List[str]] = Field(None, max_length=20)


class CloneAgentRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)


# ── Organization Browse (Phase B) ────────────────────────────────────────────
# NOTE: This endpoint MUST be defined before /{agent_id} to avoid route conflicts

@router.get("/organization", response_model=PaginatedCustomAgentsResponse)
async def browse_organization_agents(
    query: Optional[str] = None,
    category: Optional[str] = None,
    compliance_tag: Optional[str] = None,
    sort_by: str = "clone_count",
    limit: int = 20,
    offset: int = 0,
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    registry: AgentRegistryService = Depends(get_registry_service),
):
    """Browse agents published to organization (visibility=tenant)."""
    result = await registry.browse_organization(
        tenant_id=tenant_id,
        query=query,
        category=category,
        compliance_tag=compliance_tag,
        sort_by=sort_by,
        limit=min(limit, 100),
        offset=offset,
    )
    return PaginatedCustomAgentsResponse(
        agents=[CustomAgentResponse(**a.to_dict()) for a in result["agents"]],
        total=result["total"],
        limit=limit,
        offset=offset,
    )


@router.get("/organization/categories")
async def get_organization_categories(
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    registry: AgentRegistryService = Depends(get_registry_service),
):
    """Get categories with counts for organization agents."""
    return await registry.get_categories(tenant_id)


# ── Publish / Unpublish / Clone (Phase B) ───────────────────────────────────

@router.post("/{agent_id}/publish", response_model=CustomAgentResponse)
async def publish_agent(
    agent_id: UUID,
    request: PublishAgentRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service),
    registry: AgentRegistryService = Depends(get_registry_service),
):
    """Publish agent to team or organization.

    - team visibility: immediate publish
    - tenant visibility: immediate for now (approval queue added in Phase B.4)
    """
    user_id = UUID(user.id)
    await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.PUBLISH)

    agent = await registry.publish_agent(
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        visibility=request.visibility,
        category=request.category,
        compliance_tags=request.compliance_tags,
    )
    return CustomAgentResponse(**agent.to_dict())


@router.delete("/{agent_id}/publish", response_model=CustomAgentResponse)
async def unpublish_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service),
    registry: AgentRegistryService = Depends(get_registry_service),
):
    """Remove agent from registry (reset to private)."""
    user_id = UUID(user.id)
    await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.PUBLISH)

    agent = await registry.unpublish_agent(
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    return CustomAgentResponse(**agent.to_dict())


# ── Standard CRUD ────────────────────────────────────────────────────────────

@router.post("/", response_model=CustomAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_agent(
    agent_data: CustomAgentCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new custom agent.
    If team_id provided, user must be MEMBER+ in that team.
    """
    user_id = UUID(user.id)

    # RBAC: If creating in a team, verify user is MEMBER+ in that team
    if agent_data.team_id:
        membership_query = select(TeamMembership).where(
            TeamMembership.team_id == agent_data.team_id,
            TeamMembership.user_id == user_id
        )
        result = await db.execute(membership_query)
        membership = result.scalar_one_or_none()
        if not membership or membership.role == TeamRole.VIEWER.value:
            raise PermissionDeniedError("create team agent", "member")

    # Convert connector strings to UUIDs (skip registry-based string IDs like "servicenow", "ofac")
    connector_uuids = []
    if agent_data.connectors:
        for c in agent_data.connectors:
            try:
                connector_uuids.append(UUID(c))
            except ValueError:
                # Registry-based connector IDs (e.g. "servicenow", "ofac") are not UUIDs — skip silently
                pass

    # Convert workflow_ids strings to UUIDs
    workflow_uuids = []
    if agent_data.workflow_ids:
        try:
            workflow_uuids = [UUID(w) for w in agent_data.workflow_ids]
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid workflow UUID: {str(e)}"
            )

    # Merge instruction_format into config JSONB
    config = dict(agent_data.config) if agent_data.config else {}
    if agent_data.instruction_format:
        config['instruction_format'] = agent_data.instruction_format

    # Create agent (use client-provided UUID if syncing from localStorage)
    agent_kwargs = dict(
        user_id=user_id,
        tenant_id=tenant_id,
        team_id=agent_data.team_id,
        name=agent_data.name,
        description=agent_data.description,
        icon=agent_data.icon,
        agent_type=agent_data.agent_type,
        model=agent_data.model,
        system_prompt=agent_data.system_prompt,
        temperature=agent_data.temperature,
        max_tokens=agent_data.max_tokens,
        skills=agent_data.skills,
        connector_ids=connector_uuids,
        workflow_ids=workflow_uuids,
        config=config,
    )
    if agent_data.id:
        agent_kwargs["id"] = agent_data.id

    # Auto-assign tenant-scoped sequence_number (AGT-001, AGT-002, etc.)
    from sqlalchemy import func as sa_func
    max_seq_result = await db.execute(
        select(sa_func.max(CustomAgent.sequence_number))
        .where(CustomAgent.tenant_id == tenant_id)
    )
    max_seq = max_seq_result.scalar() or 0
    agent_kwargs["sequence_number"] = max_seq + 1

    agent = CustomAgent(**agent_kwargs)

    db.add(agent)
    await db.flush()
    await db.refresh(agent)

    return CustomAgentResponse(**agent.to_dict())


@router.get("/", response_model=List[CustomAgentResponse])
async def list_custom_agents(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service),
    include_inactive: bool = False
):
    """
    List all custom agents the user can view (own agents + team agents).
    """
    user_id = UUID(user.id)

    # Single LEFT OUTER JOIN query: personal agents + team agents with role check
    agents = await rbac.list_agents_for_user(
        user_id, tenant_id, AgentPermission.VIEW, include_inactive=include_inactive
    )

    return [CustomAgentResponse(**agent.to_dict()) for agent in agents]


@router.get("/{agent_id}", response_model=CustomAgentResponse)
async def get_custom_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service)
):
    """
    Get a specific custom agent by ID.
    Requires VIEW permission (owner or team member).
    """
    user_id = UUID(user.id)

    # RBAC check loads and returns the agent — no duplicate query needed
    agent = await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.VIEW)

    return CustomAgentResponse(**agent.to_dict())


@router.patch("/{agent_id}", response_model=CustomAgentResponse)
async def update_custom_agent(
    agent_id: UUID,
    agent_data: CustomAgentUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service)
):
    """
    Update a custom agent.
    Requires EDIT permission (MEMBER+ in team or owner).
    """
    user_id = UUID(user.id)

    # RBAC check loads and returns the agent — no duplicate query needed
    agent = await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.EDIT)

    # Update fields
    update_data = agent_data.model_dump(exclude_unset=True)

    # ── Optimistic locking ──────────────────────────────────────────────
    # If client sends row_version, enforce version match before writing.
    # Old clients that omit row_version get backward-compatible behavior.
    expected_version = update_data.pop('row_version', None)
    if expected_version is not None:
        if (agent.row_version or 1) != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This agent was modified by another user. Reload to see changes.",
            )

    # Extract instruction_format — stored in config JSONB via property setter.
    # Must be applied AFTER config to avoid config overwrite clobbering it.
    instruction_format = update_data.pop('instruction_format', None)

    # Handle connector UUIDs (skip registry-based string IDs like "servicenow", "ofac")
    if 'connectors' in update_data and update_data['connectors'] is not None:
        connector_uuids = []
        for c in update_data['connectors']:
            try:
                connector_uuids.append(UUID(c))
            except ValueError:
                pass
        update_data['connector_ids'] = connector_uuids
        del update_data['connectors']

    # Handle workflow_ids UUIDs
    if 'workflow_ids' in update_data and update_data['workflow_ids'] is not None:
        try:
            update_data['workflow_ids'] = [UUID(w) for w in update_data['workflow_ids']]
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid workflow UUID: {str(e)}"
            )

    safe_update(agent, update_data, CustomAgentUpdate)

    # Validate background_config cron/timezone if provided
    bg_config = update_data.get('background_config')
    if bg_config and isinstance(bg_config, dict):
        cron_expr = bg_config.get('cron_expression')
        tz_str = bg_config.get('timezone')
        if cron_expr:
            try:
                from app.services.workflow_scheduler import validate_cron_frequency
                from croniter import croniter
                croniter(cron_expr)  # syntax check
                validate_cron_frequency(cron_expr)  # frequency check
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid cron expression: {e}",
                )
        if tz_str:
            try:
                from app.services.workflow_scheduler import validate_timezone
                validate_timezone(tz_str)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(e),
                )

    # Apply instruction_format after config is set so it isn't lost
    if instruction_format is not None:
        agent.instruction_format = instruction_format

    # Increment row_version on every write
    agent.row_version = (agent.row_version or 0) + 1
    agent.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(agent)

    return CustomAgentResponse(**agent.to_dict())


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service)
):
    """
    Delete a custom agent.
    Requires MANAGE permission (OWNER only).
    """
    user_id = UUID(user.id)

    # RBAC check loads and returns the agent — no duplicate query needed
    agent = await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.MANAGE)

    await db.delete(agent)
    await db.flush()


@router.post("/{agent_id}/clone", response_model=CustomAgentResponse, status_code=status.HTTP_201_CREATED)
async def clone_custom_agent(
    agent_id: UUID,
    clone_request: Optional[CloneAgentRequest] = None,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service),
    registry: AgentRegistryService = Depends(get_registry_service),
):
    """Clone an existing custom agent.

    For published agents (visibility != private), any tenant user can clone.
    For private agents, requires VIEW permission (owner or team member).
    Uses registry service to track clone lineage and increment clone_count.
    """
    user_id = UUID(user.id)

    # Check if agent is published (visible to org) - skip RBAC for published agents
    result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id,
        )
    )
    source_agent = result.scalar_one_or_none()
    if not source_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Private agents require VIEW permission; published agents are open to org
    if source_agent.visibility == 'private':
        await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.VIEW)

    new_name = clone_request.name if clone_request else None
    cloned = await registry.clone_agent(
        source_agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        new_name=new_name,
    )

    return CustomAgentResponse(**cloned.to_dict())


@router.post("/{agent_id}/increment-usage", response_model=CustomAgentResponse)
async def increment_agent_usage(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    rbac: AgentRBACService = Depends(get_rbac_service)
):
    """
    Increment usage count for an agent.
    Requires VIEW permission (any viewer can record usage).
    """
    user_id = UUID(user.id)

    # RBAC check loads and returns the agent — no duplicate query needed
    agent = await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.VIEW)

    agent.usage_count += 1
    agent.last_used_at = datetime.utcnow()

    await db.flush()
    await db.refresh(agent)

    return CustomAgentResponse(**agent.to_dict())
