"""Pydantic schemas for Workflow Entity Tracking.

Part of Phase 2: Workflow Entity Tracking.
Provides request/response schemas for entity extraction and search APIs.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from uuid import UUID


# =============================================================================
# Entity Type Constants
# =============================================================================

EntityTypeEnum = Literal[
    "company",
    "person",
    "regulation",
    "financial_instrument",
    "risk_factor",
    "date",
    "amount",
    "location",
    "other"
]


# =============================================================================
# Entity Response Schemas
# =============================================================================

class EntityResponse(BaseModel):
    """Response for a single entity."""
    id: UUID
    tenant_id: UUID
    workflow_id: UUID
    execution_id: Optional[UUID] = None
    name: str
    entity_type: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    source_node_id: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime
    created_by: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class EntityListResponse(BaseModel):
    """Response for list of entities."""
    entities: List[EntityResponse]
    count: int
    total: Optional[int] = None  # For pagination


class EntitySummaryResponse(BaseModel):
    """Response for entity summary statistics."""
    total_entities: int
    by_type: Dict[str, int]
    workflow_id: Optional[str] = None


# =============================================================================
# Entity Search Schemas
# =============================================================================

class EntitySearchRequest(BaseModel):
    """Request to search entities by name."""
    entity_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Entity name to search for (case-insensitive partial match)"
    )
    entity_type: Optional[EntityTypeEnum] = Field(
        None,
        description="Filter by entity type"
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum results to return"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "entity_name": "Apple",
                "entity_type": "company",
                "limit": 20
            }]
        }
    )


class EntitySearchResponse(BaseModel):
    """Response for entity search results."""
    results: List[EntityResponse]
    count: int
    query: str


# =============================================================================
# Entity Extraction Schemas
# =============================================================================

class ExtractEntityRequest(BaseModel):
    """Request to manually extract entities from text."""
    text: str = Field(
        ...,
        min_length=50,
        max_length=10000,
        description="Text to extract entities from"
    )
    workflow_id: UUID = Field(
        ...,
        description="Workflow to associate entities with"
    )
    execution_id: Optional[UUID] = Field(
        None,
        description="Specific execution (optional)"
    )
    source_node_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Node that produced the text"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "text": "Apple Inc. reported strong earnings today. CEO Tim Cook announced...",
                "workflow_id": "550e8400-e29b-41d4-a716-446655440000"
            }]
        }
    )


class ExtractEntityResponse(BaseModel):
    """Response after entity extraction."""
    status: str = "extracted"
    entity_ids: List[UUID]
    count: int


# =============================================================================
# Entity Management Schemas
# =============================================================================

class EntityDeleteResponse(BaseModel):
    """Response after deleting entities."""
    status: str = "deleted"
    count: int


class EntityByWorkflowRequest(BaseModel):
    """Request parameters for getting entities by workflow."""
    execution_id: Optional[UUID] = Field(
        None,
        description="Filter by specific execution"
    )
    entity_type: Optional[EntityTypeEnum] = Field(
        None,
        description="Filter by entity type"
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum results to return"
    )
