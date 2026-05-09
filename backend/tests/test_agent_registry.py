"""Unit tests for AgentRegistryService (Phase B: Registry-First Architecture)."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime

from app.services.agent_registry import AgentRegistryService
from app.models.custom_agent import CustomAgent


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


def _make_agent(
    name="Test Agent",
    tenant_id=None,
    user_id=None,
    visibility="private",
    category=None,
    clone_count=0,
    is_active=True,
    compliance_tags=None,
):
    """Build a mock CustomAgent."""
    agent = MagicMock(spec=CustomAgent)
    agent.id = uuid4()
    agent.tenant_id = tenant_id or uuid4()
    agent.user_id = user_id or uuid4()
    agent.name = name
    agent.description = f"Description for {name}"
    agent.icon = "Robot"
    agent.agent_type = "custom"
    agent.model = "gpt-4o"
    agent.system_prompt = "You are a helpful agent."
    agent.temperature = 0.7
    agent.max_tokens = 2000
    agent.skills = ["web_search"]
    agent.connector_ids = []
    agent.config = {}
    agent.risk_tier = "low"
    agent.visibility = visibility
    agent.category = category
    agent.published_at = datetime.utcnow() if visibility != "private" else None
    agent.clone_count = clone_count
    agent.cloned_from_id = None
    agent.compliance_tags = compliance_tags or []
    agent.compliance_notes = None
    agent.is_active = is_active
    agent.is_supervisor = False
    agent.child_agent_config = []
    agent.execution_mode = "sequential"
    agent.resource_constraints = {}
    return agent


# ── publish_agent ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_agent_to_tenant(tenant_id, user_id):
    """Publish sets visibility and published_at."""
    agent = _make_agent(tenant_id=tenant_id, user_id=user_id)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = agent
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    updated = await svc.publish_agent(
        agent_id=agent.id,
        user_id=user_id,
        tenant_id=tenant_id,
        visibility="tenant",
        category="Financial",
    )

    assert updated.visibility == "tenant"
    assert updated.category == "Financial"
    assert updated.published_at is not None
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(updated)


@pytest.mark.asyncio
async def test_publish_agent_to_team(tenant_id, user_id):
    """Publish to team visibility."""
    agent = _make_agent(tenant_id=tenant_id, user_id=user_id)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = agent
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    updated = await svc.publish_agent(
        agent_id=agent.id,
        user_id=user_id,
        tenant_id=tenant_id,
        visibility="team",
    )

    assert updated.visibility == "team"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_agent_not_found(tenant_id, user_id):
    """ValueError when agent not found."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    with pytest.raises(ValueError, match="not found"):
        await svc.publish_agent(
            agent_id=uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
            visibility="tenant",
        )


@pytest.mark.asyncio
async def test_publish_preserves_existing_category(tenant_id, user_id):
    """When no category provided, keeps existing category."""
    agent = _make_agent(tenant_id=tenant_id, user_id=user_id, category="Compliance")

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = agent
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    updated = await svc.publish_agent(
        agent_id=agent.id,
        user_id=user_id,
        tenant_id=tenant_id,
        visibility="tenant",
        category=None,
    )

    assert updated.category == "Compliance"


# ── unpublish_agent ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unpublish_agent(tenant_id, user_id):
    """Unpublish resets to private and clears published_at."""
    agent = _make_agent(tenant_id=tenant_id, visibility="tenant")

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = agent
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    updated = await svc.unpublish_agent(
        agent_id=agent.id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert updated.visibility == "private"
    assert updated.published_at is None
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(updated)


@pytest.mark.asyncio
async def test_unpublish_not_found(tenant_id, user_id):
    """ValueError when agent not found."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    with pytest.raises(ValueError, match="not found"):
        await svc.unpublish_agent(
            agent_id=uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
        )


# ── clone_agent ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_agent_creates_private_copy(tenant_id, user_id):
    """Clone creates a private copy with correct lineage."""
    source = _make_agent(
        name="Source Agent",
        tenant_id=tenant_id,
        visibility="tenant",
        category="Research",
        clone_count=5,
        compliance_tags=["SOC2", "GDPR"],
    )

    cloned_agent = MagicMock()
    cloned_agent.id = uuid4()

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = source
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)

    # Track what gets added to db
    added_agents = []
    original_add = db.add

    def capture_add(obj):
        added_agents.append(obj)

    db.add = capture_add

    result = await svc.clone_agent(
        source_agent_id=source.id,
        user_id=user_id,
        tenant_id=tenant_id,
        new_name="My Clone",
    )

    # Source clone_count incremented
    assert source.clone_count == 6

    # DB add was called
    assert len(added_agents) == 1

    # DB flush and refresh were called
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_clone_agent_default_name(tenant_id, user_id):
    """Clone increments count and commits correctly."""
    source = _make_agent(name="Original", tenant_id=tenant_id, clone_count=0)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = source
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    await svc.clone_agent(
        source_agent_id=source.id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    # Source clone count was incremented
    assert source.clone_count == 1
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_clone_agent_not_found(tenant_id, user_id):
    """ValueError when source agent not found."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    with pytest.raises(ValueError, match="not found"):
        await svc.clone_agent(
            source_agent_id=uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
        )


@pytest.mark.asyncio
async def test_clone_increments_source_count_from_none(tenant_id, user_id):
    """Clone increments clone_count on source from None to 1."""
    source = _make_agent(tenant_id=tenant_id, clone_count=0)
    source.clone_count = None  # Simulate a NULL in DB

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = source
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    await svc.clone_agent(
        source_agent_id=source.id,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    assert source.clone_count == 1


# ── browse_organization ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browse_organization_returns_structure(tenant_id):
    """browse_organization returns dict with agents and total."""
    agents = [_make_agent(name=f"Agent {i}", tenant_id=tenant_id) for i in range(3)]

    db = AsyncMock()
    # First execute call: count query
    count_result = MagicMock()
    count_result.scalar.return_value = 3
    # Second execute call: agents query
    agents_result = MagicMock()
    agents_result.scalars.return_value.all.return_value = agents

    db.execute = AsyncMock(side_effect=[count_result, agents_result])

    svc = AgentRegistryService(db)
    result = await svc.browse_organization(tenant_id=tenant_id)

    assert result["total"] == 3
    assert len(result["agents"]) == 3
    assert db.execute.call_count == 2


@pytest.mark.asyncio
async def test_browse_organization_empty(tenant_id):
    """browse_organization returns empty when no agents."""
    db = AsyncMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    agents_result = MagicMock()
    agents_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[count_result, agents_result])

    svc = AgentRegistryService(db)
    result = await svc.browse_organization(tenant_id=tenant_id)

    assert result["total"] == 0
    assert result["agents"] == []


# ── get_categories ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_categories(tenant_id):
    """get_categories returns list of name/count dicts."""
    db = AsyncMock()

    row1 = MagicMock()
    row1.category = "Financial"
    row1.count = 5
    row2 = MagicMock()
    row2.category = "Compliance"
    row2.count = 3

    result_mock = MagicMock()
    result_mock.all.return_value = [row1, row2]
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    categories = await svc.get_categories(tenant_id=tenant_id)

    assert len(categories) == 2
    assert categories[0] == {"name": "Financial", "count": 5}
    assert categories[1] == {"name": "Compliance", "count": 3}


@pytest.mark.asyncio
async def test_get_categories_empty(tenant_id):
    """get_categories returns empty list when no categories."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.all.return_value = []
    db.execute.return_value = result_mock

    svc = AgentRegistryService(db)
    categories = await svc.get_categories(tenant_id=tenant_id)

    assert categories == []
