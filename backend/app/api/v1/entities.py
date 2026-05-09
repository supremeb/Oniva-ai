"""
Entity API Endpoints for Oniva.

Part of Phase 2: Workflow Entity Tracking.
Provides endpoints for:
- Listing entities by workflow
- Searching entities across workflows
- Manual entity extraction
- Entity statistics
- Entity deletion
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.services.entity_extraction import get_entity_extraction_service
from app.schemas.entity import (
    EntityResponse,
    EntityListResponse,
    EntitySummaryResponse,
    EntitySearchRequest,
    EntitySearchResponse,
    ExtractEntityRequest,
    ExtractEntityResponse,
    EntityDeleteResponse,
)

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("/workflow/{workflow_id}", response_model=EntityListResponse)
async def get_workflow_entities(
    workflow_id: UUID,
    execution_id: Optional[UUID] = Query(None, description="Filter by specific execution"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    limit: int = Query(default=100, ge=1, le=500, description="Maximum results"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get entities extracted from a workflow.

    Returns all entities associated with the workflow, optionally
    filtered by execution ID or entity type.
    """
    service = get_entity_extraction_service()

    entities = await service.get_workflow_entities(
        db=db,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        execution_id=execution_id,
        entity_type=entity_type,
        limit=limit,
    )

    return EntityListResponse(
        entities=[EntityResponse.model_validate(e) for e in entities],
        count=len(entities),
    )


@router.post("/search", response_model=EntitySearchResponse)
async def search_entities(
    request: EntitySearchRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Search for entities by name across all workflows.

    Enables queries like "What workflows analyzed Acme Corp?"
    Uses case-insensitive partial matching.
    """
    service = get_entity_extraction_service()

    results = await service.search_by_entity(
        db=db,
        tenant_id=tenant_id,
        entity_name=request.entity_name,
        entity_type=request.entity_type,
        limit=request.limit,
    )

    return EntitySearchResponse(
        results=[EntityResponse(**r) for r in results],
        count=len(results),
        query=request.entity_name,
    )


@router.get("/search", response_model=EntitySearchResponse)
async def search_entities_get(
    entity_name: str = Query(..., min_length=1, max_length=255, description="Entity name to search"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum results"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Search for entities by name (GET endpoint for convenience).

    Same as POST /search but using query parameters.
    """
    service = get_entity_extraction_service()

    results = await service.search_by_entity(
        db=db,
        tenant_id=tenant_id,
        entity_name=entity_name,
        entity_type=entity_type,
        limit=limit,
    )

    return EntitySearchResponse(
        results=[EntityResponse(**r) for r in results],
        count=len(results),
        query=entity_name,
    )


@router.post("/extract", response_model=ExtractEntityResponse, status_code=status.HTTP_201_CREATED)
async def extract_entities(
    request: ExtractEntityRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Manually extract entities from text.

    Uses LLM-based NER to identify companies, people, regulations,
    and other entities from the provided text.
    """
    service = get_entity_extraction_service()
    user_id = UUID(user.id)

    try:
        entity_ids = await service.extract_and_store(
            db=db,
            tenant_id=tenant_id,
            workflow_id=request.workflow_id,
            execution_id=request.execution_id,
            text=request.text,
            source_node_id=request.source_node_id,
            created_by=user_id,
        )

        return ExtractEntityResponse(
            status="extracted",
            entity_ids=entity_ids,
            count=len(entity_ids),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/summary", response_model=EntitySummaryResponse)
async def get_entity_summary(
    workflow_id: Optional[UUID] = Query(None, description="Filter by workflow"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get entity summary statistics.

    Returns total count and breakdown by entity type.
    Optionally filter by workflow ID.
    """
    service = get_entity_extraction_service()

    summary = await service.get_entity_summary(
        db=db,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
    )

    return EntitySummaryResponse(**summary)


@router.delete("/workflow/{workflow_id}", response_model=EntityDeleteResponse)
async def delete_workflow_entities(
    workflow_id: UUID,
    execution_id: Optional[UUID] = Query(None, description="Delete only from specific execution"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Delete entities for a workflow.

    Optionally filter by execution ID to delete only entities
    from a specific workflow run.
    """
    service = get_entity_extraction_service()

    count = await service.delete_workflow_entities(
        db=db,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        execution_id=execution_id,
    )

    return EntityDeleteResponse(
        status="deleted",
        count=count,
    )
