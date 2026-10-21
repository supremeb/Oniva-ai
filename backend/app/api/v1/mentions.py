"""
Mention resolution API for the Hybrid Document Editor (Phase B).

Provides autocomplete search and batch resolution for @mentions
in the Tiptap instructions editor. Searches across skills, connectors,
agents, and knowledge bases.

Endpoints:
- GET /api/v1/mentions/search — autocomplete search across entity types
- GET /api/v1/mentions/resolve — batch resolve mention IDs for editor load
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.agents.skills.registry import SkillRegistry
from app.connectors.registry import ConnectorRegistry


router = APIRouter(prefix="/mentions", tags=["mentions"])


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class MentionItem(BaseModel):
    """A single mention result for autocomplete or resolution."""

    entity_type: str = Field(..., description="Type: skill, connector, agent, knowledge_base")
    entity_id: str = Field(..., description="Unique entity identifier")
    display_name: str = Field(..., description="Display name for the mention chip")
    icon: str = Field(default="", description="Icon name or emoji")
    description: str = Field(default="", description="Short description")
    enabled: bool = Field(default=True, description="Whether the entity is enabled/configured")
    deleted: bool = Field(default=False, description="Whether the entity has been deleted")


class MentionSearchResponse(BaseModel):
    """Response for mention search."""

    results: list[MentionItem]
    total: int


class MentionResolveResponse(BaseModel):
    """Response for batch mention resolution."""

    items: list[MentionItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_skills(query: str, limit: int) -> list[MentionItem]:
    """Search skills from the registry by name/description."""
    registry_skills = SkillRegistry.get_all_skills()
    results = []
    q = query.lower()

    for name, manifest in registry_skills.items():
        if q in name.lower() or q in (manifest.description or "").lower():
            results.append(MentionItem(
                entity_type="skill",
                entity_id=name,
                display_name=manifest.display_name if hasattr(manifest, "display_name") else name,
                icon="Wrench",
                description=manifest.description or "",
                enabled=True,
            ))
            if len(results) >= limit:
                break

    return results


def _search_connectors(query: str, limit: int) -> list[MentionItem]:
    """Search MCP connectors from the registry."""
    available = ConnectorRegistry.list_available()
    results = []
    q = query.lower()

    for meta in available:
        if q in meta.name.lower() or q in (meta.description or "").lower():
            results.append(MentionItem(
                entity_type="connector",
                entity_id=meta.id,
                display_name=meta.name,
                icon=meta.icon or "Plugs",
                description=meta.description or "",
                enabled=True,
            ))
            if len(results) >= limit:
                break

    return results


async def _search_agents(
    query: str, limit: int, db: AsyncSession, tenant_id: UUID, user_id: UUID
) -> list[MentionItem]:
    """Search custom agents by name/description within tenant."""
    from sqlalchemy import text

    q = f"%{query.lower()}%"
    sql = text(
        "SELECT id, name, description, icon, is_active "
        "FROM custom_agents "
        "WHERE tenant_id = :tenant_id AND is_active = true "
        "AND (name ILIKE :q OR description ILIKE :q) "
        "LIMIT :lim"
    )
    result = await db.execute(sql, {"tenant_id": tenant_id, "q": q, "lim": limit})
    rows = result.fetchall()

    return [
        MentionItem(
            entity_type="agent",
            entity_id=str(row.id),
            display_name=row.name,
            icon=row.icon or "Robot",
            description=row.description or "",
            enabled=row.is_active,
        )
        for row in rows
    ]


async def _search_knowledge_bases(
    query: str, limit: int, db: AsyncSession
) -> list[MentionItem]:
    """Search knowledge bases by name."""
    from sqlalchemy import text

    q = query.lower()
    sql = text("""
        SELECT
            doc_metadata->>'kb_id' as id,
            COALESCE(doc_metadata->>'kb_name', doc_metadata->>'kb_id') as name,
            COUNT(*) as document_count
        FROM documents
        WHERE doc_metadata->>'kb_id' IS NOT NULL
          AND LOWER(COALESCE(doc_metadata->>'kb_name', doc_metadata->>'kb_id')) LIKE :query
        GROUP BY doc_metadata->>'kb_id', doc_metadata->>'kb_name'
        ORDER BY name
        LIMIT :limit
    """)
    result = await db.execute(sql, {"query": f"%{q}%", "limit": limit})
    rows = result.fetchall()

    return [
        MentionItem(
            entity_type="knowledge_base",
            entity_id=row.id,
            display_name=row.name,
            icon="Books",
            description=f"{row.document_count} document{'s' if row.document_count != 1 else ''}",
            enabled=True,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Resolve helpers
# ---------------------------------------------------------------------------


def _resolve_skill(entity_id: str) -> MentionItem:
    """Resolve a single skill by ID."""
    registry_skills = SkillRegistry.get_all_skills()
    manifest = registry_skills.get(entity_id)
    if not manifest:
        return MentionItem(
            entity_type="skill",
            entity_id=entity_id,
            display_name=entity_id,
            icon="Wrench",
            deleted=True,
        )
    return MentionItem(
        entity_type="skill",
        entity_id=entity_id,
        display_name=manifest.display_name if hasattr(manifest, "display_name") else entity_id,
        icon="Wrench",
        description=manifest.description or "",
        enabled=True,
    )


def _resolve_connector(entity_id: str) -> MentionItem:
    """Resolve a single connector by ID."""
    available = ConnectorRegistry.list_available()
    for meta in available:
        if meta.id == entity_id:
            return MentionItem(
                entity_type="connector",
                entity_id=meta.id,
                display_name=meta.name,
                icon=meta.icon or "Plugs",
                description=meta.description or "",
                enabled=True,
            )
    return MentionItem(
        entity_type="connector",
        entity_id=entity_id,
        display_name=entity_id,
        icon="Plugs",
        deleted=True,
    )


async def _resolve_agent(entity_id: str, db: AsyncSession) -> MentionItem:
    """Resolve a single agent by UUID."""
    from sqlalchemy import text

    try:
        agent_uuid = UUID(entity_id)
    except ValueError:
        return MentionItem(
            entity_type="agent",
            entity_id=entity_id,
            display_name=entity_id,
            icon="Robot",
            deleted=True,
        )

    sql = text(
        "SELECT id, name, description, icon, is_active "
        "FROM custom_agents WHERE id = :agent_id"
    )
    result = await db.execute(sql, {"agent_id": agent_uuid})
    agent = result.fetchone()

    if not agent:
        return MentionItem(
            entity_type="agent",
            entity_id=entity_id,
            display_name=entity_id,
            icon="Robot",
            deleted=True,
        )

    return MentionItem(
        entity_type="agent",
        entity_id=str(agent.id),
        display_name=agent.name,
        icon=agent.icon or "Robot",
        description=agent.description or "",
        enabled=agent.is_active,
    )


async def _resolve_knowledge_base(entity_id: str, db: AsyncSession) -> MentionItem:
    """Resolve a single knowledge base by ID."""
    from sqlalchemy import text

    sql = text("""
        SELECT
            doc_metadata->>'kb_id' as id,
            COALESCE(doc_metadata->>'kb_name', doc_metadata->>'kb_id') as name,
            COUNT(*) as document_count
        FROM documents
        WHERE doc_metadata->>'kb_id' = :kb_id
        GROUP BY doc_metadata->>'kb_id', doc_metadata->>'kb_name'
        LIMIT 1
    """)
    result = await db.execute(sql, {"kb_id": entity_id})
    row = result.fetchone()

    if not row:
        return MentionItem(
            entity_type="knowledge_base",
            entity_id=entity_id,
            display_name=entity_id,
            icon="Books",
            deleted=True,
        )

    return MentionItem(
        entity_type="knowledge_base",
        entity_id=row.id,
        display_name=row.name,
        icon="Books",
        description=f"{row.document_count} document{'s' if row.document_count != 1 else ''}",
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/search", response_model=MentionSearchResponse)
async def search_mentions(
    q: str = Query("", description="Search query"),
    types: Optional[str] = Query(
        None,
        description="Comma-separated entity types to search (skill,connector,agent,knowledge_base)",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> MentionSearchResponse:
    """
    Autocomplete search across skills, connectors, agents, and knowledge bases.

    Returns up to 10 results per type, 20 total.
    """
    allowed_types = {"skill", "connector", "agent", "knowledge_base"}
    if types:
        search_types = {t.strip() for t in types.split(",") if t.strip() in allowed_types}
    else:
        search_types = allowed_types

    per_type_limit = 10
    results: list[MentionItem] = []

    if not q:
        return MentionSearchResponse(results=[], total=0)

    if "skill" in search_types:
        results.extend(_search_skills(q, per_type_limit))

    if "connector" in search_types:
        results.extend(_search_connectors(q, per_type_limit))

    if "agent" in search_types:
        results.extend(await _search_agents(q, per_type_limit, db, tenant_id, user.id))

    if "knowledge_base" in search_types:
        results.extend(await _search_knowledge_bases(q, per_type_limit, db))

    # Cap total results
    total = len(results)
    results = results[:20]

    return MentionSearchResponse(results=results, total=total)


@router.get("/resolve", response_model=MentionResolveResponse)
async def resolve_mentions(
    ids: str = Query(
        ...,
        description="Comma-separated {entity_type}:{entity_id} pairs to resolve",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> MentionResolveResponse:
    """
    Batch resolve mention IDs for editor load.

    Given pairs like `skill:web_search,connector:linear,agent:<uuid>`,
    returns current metadata for each. Marks deleted entities with `deleted: true`.
    """
    items: list[MentionItem] = []
    pairs = [p.strip() for p in ids.split(",") if ":" in p.strip()]

    for pair in pairs:
        entity_type, entity_id = pair.split(":", 1)
        entity_type = entity_type.strip()
        entity_id = entity_id.strip()

        if entity_type == "skill":
            items.append(_resolve_skill(entity_id))
        elif entity_type == "connector":
            items.append(_resolve_connector(entity_id))
        elif entity_type == "agent":
            items.append(await _resolve_agent(entity_id, db))
        elif entity_type == "knowledge_base":
            items.append(await _resolve_knowledge_base(entity_id, db))

    return MentionResolveResponse(items=items)
