"""
Memory API Endpoints for Oniva Enhanced Memory System.

Provides endpoints for:
- Storing agent decisions to episodic memory
- Searching similar past decisions (hybrid, semantic, or keyword)
- Memory health monitoring
- Memory consolidation (admin)
- Importance scoring management
- Archival of old memories
"""

import time
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.config import get_settings
from app.services.episodic_memory import get_episodic_memory_service
from sqlalchemy import select, update, delete, func, text
from sqlalchemy.orm import attributes as orm_attributes
from app.models.episodic_memory import EpisodicMemory
from app.schemas.memory import (
    StoreDecisionRequest,
    StoreDecisionResponse,
    SearchDecisionsRequest,
    SearchDecisionsResponse,
    HybridSearchRequest,
    HybridSearchResponse,
    ImportanceUpdateRequest,
    ImportanceUpdateResponse,
    ArchiveRequest,
    ArchiveResponse,
    MemoryHealthResponse,
    ConsolidationResponse,
    EpisodicMemoryResponse,
    MemoryFeedbackRequest,
    MemoryFeedbackResponse,
    MemoryUpdateRequest,
    MemoryUpdateResponse,
    MemoryDeleteResponse,
    MemoryStatsResponse,
    OrgProfileUpdateRequest,
    OrgProfileResponse,
)
from datetime import datetime

router = APIRouter(prefix="/memory", tags=["memory"])


def _check_feature_enabled():
    """Check if episodic memory feature is enabled."""
    settings = get_settings()
    if not getattr(settings, 'USE_EPISODIC_MEMORY', False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Episodic memory feature is not enabled"
        )


@router.post("/episodic", response_model=StoreDecisionResponse, status_code=status.HTTP_201_CREATED)
async def store_decision(
    request: StoreDecisionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Store a decision to episodic memory.

    Stores the decision with vector embedding for later similarity search.
    RLS enforces user-level isolation.
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    try:
        # Sharing requires USE_ORG_CONTEXT + admin/member role
        if request.visibility == 'shared':
            settings = get_settings()
            if not settings.USE_ORG_CONTEXT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Organization context feature is not enabled (USE_ORG_CONTEXT=False)"
                )
            if user.role not in ('owner', 'admin', 'member'):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only members and above can share memories with the organization"
                )

        memory_id = await service.store_decision(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=request.session_id,
            agent_name=request.agent_name,
            action_type=request.action_type,
            context=request.context,
            decision=request.decision,
            rationale=request.rationale,
            entities=request.entities,
            visibility=request.visibility,
            memory_type=request.memory_type,
            tags=request.tags,
        )

        return StoreDecisionResponse(id=memory_id, status="stored")

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/episodic/search", response_model=SearchDecisionsResponse)
async def search_decisions(
    request: SearchDecisionsRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Search for similar past decisions using vector similarity.

    Returns up to `limit` similar decisions with similarity scores.
    Default limit is 3 to stay within token budget (~600 tokens).
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    results = await service.recall_similar(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        query=request.query,
        limit=request.limit,
        min_similarity=request.min_similarity,
        agent_name=request.agent_name,
        action_type=request.action_type,
    )

    # Merge shared memories if requested
    if request.include_shared:
        shared = await service.recall_shared(
            db=db,
            tenant_id=tenant_id,
            query=request.query,
            limit=request.limit,
            agent_name=request.agent_name,
            action_type=request.action_type,
        )
        # Deduplicate by ID and re-sort by similarity
        seen = {r.id for r in results}
        for s in shared:
            if s.id not in seen:
                results.append(s)
                seen.add(s.id)
        results.sort(key=lambda r: r.similarity or 0, reverse=True)
        results = results[:request.limit]

    return SearchDecisionsResponse(results=results, count=len(results))


@router.post("/episodic/recall", response_model=HybridSearchResponse)
async def recall_memories(
    request: HybridSearchRequest,
    search_mode: Literal["hybrid", "semantic", "keyword"] = Query(
        default="hybrid",
        description="Search algorithm: hybrid (semantic+BM25), semantic (vector only), keyword (BM25 only)"
    ),
    include_archived: bool = Query(default=False, description="Include archived memories"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Recall similar memories using hybrid search (semantic + keyword).

    - **hybrid**: Combines vector similarity with BM25 keyword matching (default, best accuracy)
    - **semantic**: Pure vector similarity search
    - **keyword**: Pure full-text search (BM25)

    Includes importance scoring and recency boosts for better ranking.
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    if search_mode == "hybrid":
        results = await service.hybrid_recall(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            query=request.query,
            limit=request.limit,
            include_archived=include_archived,
            importance_weight=request.importance_weight,
            recency_weight=request.recency_weight,
            agent_name=request.agent_name,
            action_type=request.action_type,
        )
    elif search_mode == "semantic":
        results = await service.recall_similar(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            query=request.query,
            limit=request.limit,
            agent_name=request.agent_name,
            action_type=request.action_type,
        )
    else:  # keyword
        results = await service.keyword_recall(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            query=request.query,
            limit=request.limit,
            include_archived=include_archived,
        )

    # Merge shared memories if requested
    if request.include_shared:
        shared = await service.recall_shared(
            db=db,
            tenant_id=tenant_id,
            query=request.query,
            limit=request.limit,
            agent_name=request.agent_name,
            action_type=request.action_type,
        )
        seen = {r.id for r in results}
        for s in shared:
            if s.id not in seen:
                results.append(s)
                seen.add(s.id)
        results.sort(key=lambda r: r.similarity or 0, reverse=True)
        results = results[:request.limit]

    return HybridSearchResponse(
        results=results,
        count=len(results),
        search_mode=search_mode,
    )


@router.patch("/episodic/{memory_id}/importance", response_model=ImportanceUpdateResponse)
async def update_importance(
    memory_id: UUID,
    request: ImportanceUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Update the importance score of a memory (0.0-1.0).

    Higher importance = more likely to appear in search results.
    Use this to:
    - Mark critical decisions as high importance
    - Downgrade outdated or less relevant memories
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    success = await service.set_importance(
        db=db,
        memory_id=memory_id,
        importance=request.importance,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or not owned by user"
        )

    return ImportanceUpdateResponse(
        status="updated",
        memory_id=memory_id,
        importance=request.importance,
    )


@router.post("/episodic/archive", response_model=ArchiveResponse)
async def archive_old_memories(
    request: ArchiveRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Archive old, low-access memories.

    Archived memories are excluded from default searches but can still
    be retrieved with include_archived=True.

    Only archives memories that are:
    - Older than `days_threshold` days
    - Accessed less than `min_access_count` times
    - Have importance score below 0.7
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    count = await service.archive_old_memories(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        days_threshold=request.days_threshold,
        min_access_count=request.min_access_count,
    )

    return ArchiveResponse(
        status="archived",
        count=count,
        days_threshold=request.days_threshold,
    )


@router.get("/health", response_model=MemoryHealthResponse)
async def get_memory_health(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get health statistics for the memory system.

    Returns total memories, date range, and index status.
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    stats = await service.get_health_stats(db, tenant_id, user_id)

    return MemoryHealthResponse(
        status=stats["status"],
        total_memories=stats["total_memories"],
        oldest_memory=stats["oldest_memory"],
        newest_memory=stats["newest_memory"],
        index_status=stats["index_status"],
    )


@router.post("/consolidate", response_model=ConsolidationResponse)
async def consolidate_memories(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Trigger memory consolidation for the current user.

    Consolidation:
    - Archives expired memories
    - Notes near-duplicate memories

    This is typically called automatically when memory count exceeds threshold,
    but can be triggered manually.
    """
    _check_feature_enabled()

    service = get_episodic_memory_service()
    user_id = UUID(user.id)

    start_time = time.time()
    stats = await service.consolidate(db, tenant_id, user_id)
    duration_ms = int((time.time() - start_time) * 1000)

    return ConsolidationResponse(
        status="completed",
        duplicates_removed=stats["duplicates_noted"],
        archived=stats["archived"],
        duration_ms=duration_ms,
    )


# =============================================================================
# Phase 4: Memory Management Endpoints
# =============================================================================

@router.post("/episodic/{memory_id}/feedback", response_model=MemoryFeedbackResponse)
async def provide_feedback(
    memory_id: UUID,
    request: "MemoryFeedbackRequest",
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Provide feedback on a memory (helpful/not helpful).

    - **helpful**: Boosts importance by +0.15 (capped at 1.0)
    - **not_helpful**: Reduces importance by -0.15 (minimum 0.1)

    This feedback loop improves future search relevance.
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    # Update memory with feedback
    if request.feedback == "helpful":
        importance_delta = 0.15
    else:
        importance_delta = -0.15

    # Get current memory to calculate new importance
    result = await db.execute(
        select(EpisodicMemory).where(
            EpisodicMemory.id == memory_id,
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    memory = result.scalar_one_or_none()

    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or not owned by user"
        )

    new_importance = max(0.1, min(1.0, memory.importance + importance_delta))

    # Update memory
    await db.execute(
        update(EpisodicMemory).where(
            EpisodicMemory.id == memory_id
        ).values(
            feedback=request.feedback,
            feedback_at=datetime.utcnow(),
            importance=new_importance,
        )
    )
    await db.commit()

    return MemoryFeedbackResponse(
        status="updated",
        memory_id=memory_id,
        feedback=request.feedback,
        new_importance=new_importance,
    )


def _extract_section(combined_text: str, section: str) -> str:
    """Extract a section (Context, Decision, Rationale) from combined context_decision text."""
    import re
    # Match "Section: content" until the next section header or end
    pattern = rf'{section}:\s*(.*?)(?=\n\n(?:Context|Decision|Rationale):|$)'
    match = re.search(pattern, combined_text, re.DOTALL)
    return match.group(1).strip() if match else ""


@router.patch("/episodic/{memory_id}", response_model=MemoryUpdateResponse)
async def update_memory(
    memory_id: UUID,
    request: "MemoryUpdateRequest",
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Update memory properties and content.

    Use this to:
    - Change visibility (private/shared)
    - Add or replace tags for organization
    - Archive or unarchive memories
    - Edit content (context, decision, rationale, entities)
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    # Verify ownership
    result = await db.execute(
        select(EpisodicMemory.id).where(
            EpisodicMemory.id == memory_id,
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or not owned by user"
        )

    # Build update values
    update_values = {}
    updated_fields = []

    if request.visibility is not None:
        # PII check when sharing
        if request.visibility == 'shared':
            settings = get_settings()
            if not settings.USE_ORG_CONTEXT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Organization context feature is not enabled"
                )
            if user.role not in ('owner', 'admin', 'member'):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only members and above can share memories"
                )
            # Fetch memory content for PII scan
            mem_result = await db.execute(
                select(EpisodicMemory.context_decision).where(EpisodicMemory.id == memory_id)
            )
            content = mem_result.scalar_one_or_none()
            if content:
                from app.utils.pii_scanner import has_pii
                pii_found, pii_type = has_pii(content)
                if pii_found:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Cannot share memory: {pii_type}. Remove PII before sharing."
                    )
        update_values["visibility"] = request.visibility
        updated_fields.append("visibility")

    if request.tags is not None:
        update_values["tags"] = request.tags
        updated_fields.append("tags")

    if request.archived is not None:
        update_values["archived"] = request.archived
        updated_fields.append("archived")

    # Handle content field updates (context, decision, rationale)
    content_changed = request.context is not None or request.decision is not None or request.rationale is not None
    if content_changed:
        # Fetch current context_decision to merge with updates
        mem_result = await db.execute(
            select(EpisodicMemory.context_decision, EpisodicMemory.rationale).where(
                EpisodicMemory.id == memory_id
            )
        )
        row = mem_result.one_or_none()
        current_text = row[0] if row else ""
        current_rationale = row[1] if row else ""

        # Extract existing sections
        existing_context = _extract_section(current_text, "Context")
        existing_decision = _extract_section(current_text, "Decision")

        # Apply updates
        new_context = request.context if request.context is not None else existing_context
        new_decision = request.decision if request.decision is not None else existing_decision
        new_rationale = request.rationale if request.rationale is not None else (current_rationale or "")

        # Rebuild context_decision
        parts = []
        if new_context:
            parts.append(f"Context: {new_context}")
        if new_decision:
            parts.append(f"Decision: {new_decision}")
        if new_rationale:
            parts.append(f"Rationale: {new_rationale}")
        new_combined = "\n\n".join(parts)

        update_values["context_decision"] = new_combined
        if request.context is not None:
            updated_fields.append("context")
        if request.decision is not None:
            updated_fields.append("decision")

        if request.rationale is not None:
            update_values["rationale"] = request.rationale
            updated_fields.append("rationale")

        # Regenerate embedding for the updated content
        try:
            service = get_episodic_memory_service()
            embedding = await service._get_embedding(new_combined, db)
            if embedding is not None:
                update_values["embedding"] = embedding
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Re-embedding failed during update: {e}")
            # Graceful degradation: keep old embedding

    if request.entities is not None:
        update_values["entities"] = request.entities
        updated_fields.append("entities")

    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    await db.execute(
        update(EpisodicMemory).where(
            EpisodicMemory.id == memory_id
        ).values(**update_values)
    )

    # Regenerate search_vector when content or entities changed
    if content_changed or request.entities is not None:
        try:
            await db.execute(
                text("""
                    UPDATE episodic_memories
                    SET search_vector = to_tsvector('english',
                        coalesce(context_decision, '') || ' ' ||
                        coalesce(rationale, '') || ' ' ||
                        coalesce((SELECT string_agg(e.value, ' ') FROM jsonb_array_elements_text(entities) e), ''))
                    WHERE id = :id AND tenant_id = :tenant_id
                """),
                {"id": str(memory_id), "tenant_id": str(tenant_id)},
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"search_vector update failed: {e}")

    await db.commit()

    return MemoryUpdateResponse(
        status="updated",
        memory_id=memory_id,
        updated_fields=updated_fields,
    )


@router.get("/episodic", response_model=HybridSearchResponse)
async def list_memories(
    limit: int = Query(default=50, ge=1, le=200, description="Maximum results"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    include_archived: bool = Query(default=False, description="Include archived memories"),
    memory_type: Optional[str] = Query(default=None, description="Filter by memory type"),
    agent_name: Optional[str] = Query(default=None, description="Filter by agent name"),
    visibility: Optional[str] = Query(default=None, description="Filter by visibility (private, shared)"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    List all memories for the current user with optional filters.

    Returns memories sorted by creation date (newest first).
    Use visibility=shared to list shared org memories (tenant-wide).
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    # Build query - shared memories are scoped to tenant, not user
    if visibility == 'shared':
        query = select(EpisodicMemory).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.visibility == 'shared',
        )
    else:
        query = select(EpisodicMemory).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
        if visibility:
            query = query.where(EpisodicMemory.visibility == visibility)

    if not include_archived:
        query = query.where(EpisodicMemory.archived == False)

    if memory_type:
        query = query.where(EpisodicMemory.memory_type == memory_type)

    if agent_name:
        query = query.where(EpisodicMemory.agent_name == agent_name)

    # Order by newest first
    query = query.order_by(EpisodicMemory.created_at.desc())

    # Apply pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    memories = result.scalars().all()

    return HybridSearchResponse(
        results=[EpisodicMemoryResponse.model_validate(m) for m in memories],
        count=len(memories),
        search_mode="list",
    )


@router.get("/episodic/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get memory statistics for the current user.

    Returns counts by type, feedback, visibility, and average importance.
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    # Total count
    total_result = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    total = total_result.scalar() or 0

    # By type
    type_result = await db.execute(
        select(EpisodicMemory.memory_type, func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        ).group_by(EpisodicMemory.memory_type)
    )
    by_type = {row[0]: row[1] for row in type_result.all()}

    # By feedback
    feedback_result = await db.execute(
        select(EpisodicMemory.feedback, func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        ).group_by(EpisodicMemory.feedback)
    )
    by_feedback = {(row[0] or "none"): row[1] for row in feedback_result.all()}

    # By visibility
    visibility_result = await db.execute(
        select(EpisodicMemory.visibility, func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        ).group_by(EpisodicMemory.visibility)
    )
    by_visibility = {row[0]: row[1] for row in visibility_result.all()}

    # Archived count
    archived_result = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
            EpisodicMemory.archived == True,
        )
    )
    archived_count = archived_result.scalar() or 0

    # Average importance
    avg_result = await db.execute(
        select(func.avg(EpisodicMemory.importance)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    avg_importance = float(avg_result.scalar() or 0.5)

    return MemoryStatsResponse(
        total_memories=total,
        by_type=by_type,
        by_feedback=by_feedback,
        by_visibility=by_visibility,
        archived_count=archived_count,
        avg_importance=avg_importance,
    )


@router.get("/episodic/{memory_id}", response_model=EpisodicMemoryResponse)
async def get_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get a single memory by ID.
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    result = await db.execute(
        select(EpisodicMemory).where(
            EpisodicMemory.id == memory_id,
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    memory = result.scalar_one_or_none()

    if not memory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or not owned by user"
        )

    return EpisodicMemoryResponse.model_validate(memory)


@router.delete("/episodic/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Delete a memory permanently.

    Note: Consider archiving instead for data retention compliance.
    """
    _check_feature_enabled()
    user_id = UUID(user.id)

    # Verify ownership first
    result = await db.execute(
        select(EpisodicMemory.id).where(
            EpisodicMemory.id == memory_id,
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or not owned by user"
        )

    await db.execute(
        delete(EpisodicMemory).where(EpisodicMemory.id == memory_id)
    )
    await db.commit()

    return MemoryDeleteResponse(
        status="deleted",
        memory_id=memory_id,
    )


# =============================================================================
# Organization Context Endpoints
# =============================================================================

@router.get("/org-profile", response_model=OrgProfileResponse)
async def get_org_profile(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get the organization profile for context injection.

    Returns the org's industry, compliance frameworks, default context,
    and count of shared memories.
    """
    settings = get_settings()
    if not settings.USE_ORG_CONTEXT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization context feature is not enabled"
        )

    # Get tenant metadata
    from app.models.tenant import Tenant
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Count shared memories
    shared_count_result = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.visibility == 'shared',
            EpisodicMemory.archived == False,
        )
    )
    shared_count = shared_count_result.scalar() or 0

    # Read org profile from tenant metadata (JSONB)
    tenant_settings = tenant.settings or {}
    org_profile = tenant_settings.get('org_profile', {})

    return OrgProfileResponse(
        tenant_id=tenant_id,
        industry=org_profile.get('industry'),
        description=org_profile.get('description'),
        compliance_frameworks=org_profile.get('compliance_frameworks', []),
        default_context=org_profile.get('default_context'),
        shared_memory_count=shared_count,
        updated_at=org_profile.get('updated_at'),
    )


@router.patch("/org-profile", response_model=OrgProfileResponse)
async def update_org_profile(
    request: OrgProfileUpdateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Update the organization profile. Requires admin or owner role.

    The org profile is injected into every agent prompt for this tenant
    when USE_ORG_CONTEXT is enabled.
    """
    settings = get_settings()
    if not settings.USE_ORG_CONTEXT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization context feature is not enabled"
        )

    if user.role not in ('owner', 'admin'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins and owners can update the organization profile"
        )

    from app.models.tenant import Tenant
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Update tenant metadata with org profile
    tenant_settings = tenant.settings or {}
    org_profile = tenant_settings.get('org_profile', {})

    if request.industry is not None:
        org_profile['industry'] = request.industry
    if request.description is not None:
        org_profile['description'] = request.description
    if request.compliance_frameworks is not None:
        org_profile['compliance_frameworks'] = request.compliance_frameworks
    if request.default_context is not None:
        org_profile['default_context'] = request.default_context

    org_profile['updated_at'] = datetime.utcnow().isoformat()
    tenant_settings['org_profile'] = org_profile
    tenant.settings = tenant_settings
    # Flag the JSONB column as modified so SQLAlchemy emits an UPDATE
    orm_attributes.flag_modified(tenant, 'settings')

    await db.commit()

    # Count shared memories
    shared_count_result = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.tenant_id == tenant_id,
            EpisodicMemory.visibility == 'shared',
            EpisodicMemory.archived == False,
        )
    )
    shared_count = shared_count_result.scalar() or 0

    return OrgProfileResponse(
        tenant_id=tenant_id,
        industry=org_profile.get('industry'),
        description=org_profile.get('description'),
        compliance_frameworks=org_profile.get('compliance_frameworks', []),
        default_context=org_profile.get('default_context'),
        shared_memory_count=shared_count,
        updated_at=org_profile.get('updated_at'),
    )
