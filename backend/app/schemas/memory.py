"""Pydantic schemas for Enhanced Agent Memory System.

Provides request/response schemas for:
- Episodic Memory API
- User Preferences API
"""
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from uuid import UUID


# =============================================================================
# Episodic Memory Schemas
# =============================================================================

class StoreDecisionRequest(BaseModel):
    """Request to store a decision in episodic memory."""
    session_id: Optional[str] = Field(
        None,
        description="Session ID for linking to filesystem session"
    )
    agent_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the agent making the decision"
    )
    action_type: Literal["analysis", "compliance_check", "recommendation", "research", "chat", "other"] = Field(
        ...,
        description="Type of action performed"
    )
    context: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Context of the decision (what was asked)"
    )
    decision: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="The decision made"
    )
    rationale: Optional[str] = Field(
        None,
        max_length=2000,
        description="Reasoning behind the decision"
    )
    entities: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Entities involved (tickers, companies, etc.)"
    )
    visibility: Literal["private", "shared"] = Field(
        default="private",
        description="Visibility: 'private' (user only) or 'shared' (tenant-wide). Shared memories are PII-scanned."
    )
    memory_type: Literal["decision", "fact", "preference", "conversation"] = Field(
        default="decision",
        description="Type of memory being stored"
    )
    tags: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Tags for organizing memories (max 20)"
    )

    @field_validator('entities')
    @classmethod
    def validate_entities(cls, v):
        """Validate entity list."""
        if len(v) > 50:
            raise ValueError("Maximum 50 entities allowed")
        for entity in v:
            if len(entity) > 100:
                raise ValueError("Entity name too long (max 100 chars)")
            if '<' in entity or '>' in entity:
                raise ValueError("Invalid characters in entity name")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "session_id": "sess_abc123",
                "agent_name": "analysis",
                "action_type": "recommendation",
                "context": "User asked about AAPL risk profile",
                "decision": "Recommended moderate allocation based on volatility",
                "rationale": "Current P/E ratio suggests overvaluation",
                "entities": ["AAPL", "technology", "growth"]
            }]
        }
    )


class SearchDecisionsRequest(BaseModel):
    """Request to search similar past decisions."""
    query: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Search query for similarity matching"
    )
    limit: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of results (default 3 for token budget)"
    )
    min_similarity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold (default 0.85 for precision)"
    )
    agent_name: Optional[str] = Field(
        None,
        description="Filter by agent name"
    )
    action_type: Optional[Literal["analysis", "compliance_check", "recommendation", "research", "chat", "other"]] = Field(
        None,
        description="Filter by action type"
    )
    include_shared: bool = Field(
        default=False,
        description="Include shared org-level memories in results (requires USE_ORG_CONTEXT)"
    )


MemoryTypeEnum = Literal["decision", "fact", "preference", "conversation"]
FeedbackEnum = Literal["helpful", "not_helpful"]
VisibilityEnum = Literal["private", "shared"]
SourceTypeEnum = Literal["chat", "workflow", "api", "import"]


class EpisodicMemoryResponse(BaseModel):
    """Response for a single episodic memory entry."""
    id: UUID
    session_id: Optional[str]
    agent_name: str
    action_type: str
    context_decision: str
    rationale: Optional[str]
    outcome: Optional[Dict[str, Any]]
    entities: List[str]
    similarity: Optional[float] = Field(None, description="Similarity/relevance score (for search results)")
    importance: float = Field(default=0.5, description="Memory importance score (0-1)")
    archived: bool = Field(default=False, description="Whether memory is archived")
    access_count: int = Field(default=0, description="Number of times retrieved")
    created_at: datetime
    # Phase 4: UI fields
    memory_type: str = Field(default="decision", description="Memory type: decision, fact, preference, conversation")
    feedback: Optional[str] = Field(None, description="User feedback: helpful, not_helpful")
    feedback_at: Optional[datetime] = None
    visibility: str = Field(default="private", description="Visibility: private, shared, public")
    source_type: Optional[str] = Field(None, description="Source: chat, workflow, api, import")
    source_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list, description="User-defined tags")

    model_config = ConfigDict(from_attributes=True)


class StoreDecisionResponse(BaseModel):
    """Response after storing a decision."""
    id: UUID
    status: str = "stored"


class SearchDecisionsResponse(BaseModel):
    """Response for search results."""
    results: List[EpisodicMemoryResponse]
    count: int


class MemoryHealthResponse(BaseModel):
    """Response for memory system health check."""
    status: str
    total_memories: int
    oldest_memory: Optional[datetime]
    newest_memory: Optional[datetime]
    index_status: str


class ConsolidationResponse(BaseModel):
    """Response after memory consolidation."""
    status: str
    duplicates_removed: int
    archived: int
    duration_ms: int


# =============================================================================
# Hybrid Search Schemas
# =============================================================================

class HybridSearchRequest(BaseModel):
    """Request for hybrid memory search."""
    query: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Search query for finding relevant memories"
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results"
    )
    importance_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Weight for importance boost in scoring (0-1)"
    )
    recency_weight: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Weight for recency boost in scoring (0-1)"
    )
    agent_name: Optional[str] = Field(
        None,
        description="Filter by agent name"
    )
    action_type: Optional[Literal["analysis", "compliance_check", "recommendation", "research", "chat", "other"]] = Field(
        None,
        description="Filter by action type"
    )
    include_shared: bool = Field(
        default=False,
        description="Include shared org-level memories (requires USE_ORG_CONTEXT)"
    )


class HybridSearchResponse(BaseModel):
    """Response for hybrid memory search."""
    results: List[EpisodicMemoryResponse]
    count: int
    search_mode: str = Field(..., description="Search algorithm used (hybrid, semantic, keyword)")


class ImportanceUpdateRequest(BaseModel):
    """Request to update memory importance."""
    importance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Importance score (0.0-1.0). Higher = more relevant in searches."
    )


class ImportanceUpdateResponse(BaseModel):
    """Response after updating importance."""
    status: str = "updated"
    memory_id: UUID
    importance: float


class ArchiveRequest(BaseModel):
    """Request to archive old memories."""
    days_threshold: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Archive memories older than N days"
    )
    min_access_count: int = Field(
        default=2,
        ge=0,
        le=100,
        description="Only archive if accessed less than N times"
    )


class ArchiveResponse(BaseModel):
    """Response after archiving memories."""
    status: str = "archived"
    count: int = Field(..., description="Number of memories archived")
    days_threshold: int


# =============================================================================
# User Preferences Schemas
# =============================================================================

class AnalysisPreferences(BaseModel):
    """Preferences for financial analysis agents."""
    risk_tolerance: Literal["low", "moderate", "high"] = Field(
        default="moderate",
        description="User's risk tolerance level"
    )
    preferred_metrics: List[str] = Field(
        default_factory=lambda: ["P/E", "ROE", "Debt/Equity"],
        max_length=20,
        description="Preferred financial metrics"
    )
    default_timeframe: str = Field(
        default="1Y",
        pattern=r"^[0-9]+[DWMY]$",
        description="Default analysis timeframe"
    )
    include_competitors: bool = Field(
        default=True,
        description="Include competitor analysis"
    )
    benchmark_index: str = Field(
        default="SPY",
        description="Default benchmark index"
    )

    @field_validator('preferred_metrics')
    @classmethod
    def validate_metrics(cls, v):
        """Validate metric names."""
        for metric in v:
            if len(metric) > 50:
                raise ValueError("Metric name too long (max 50 chars)")
            if '<' in metric or '>' in metric:
                raise ValueError("Invalid characters in metric name")
        return v


class OutputPreferences(BaseModel):
    """Preferences for agent output format."""
    verbosity: Literal["concise", "standard", "detailed"] = Field(
        default="standard",
        description="Output verbosity level"
    )
    include_charts: bool = Field(
        default=True,
        description="Include charts in reports"
    )
    report_format: Literal["executive", "technical", "comprehensive"] = Field(
        default="technical",
        description="Report format style"
    )
    language: str = Field(
        default="en",
        description="Output language"
    )
    currency: str = Field(
        default="USD",
        description="Default currency"
    )


class DataSourcePreferences(BaseModel):
    """Preferences for data sources."""
    preferred_providers: List[str] = Field(
        default_factory=lambda: ["yahoo_finance", "sec_edgar"],
        description="Preferred data providers"
    )
    include_news: bool = Field(
        default=True,
        description="Include news in analysis"
    )
    news_recency_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="News recency filter (days)"
    )


class CompliancePreferences(BaseModel):
    """Preferences for compliance checks."""
    frameworks: List[str] = Field(
        default_factory=list,
        description="Compliance frameworks to check"
    )
    auto_check: bool = Field(
        default=False,
        description="Automatically run compliance checks"
    )
    risk_flags: List[str] = Field(
        default_factory=lambda: ["high_leverage", "insider_trading"],
        description="Risk flags to monitor"
    )


class UserPreferencesSchema(BaseModel):
    """Complete user preferences schema with validation."""
    analysis: AnalysisPreferences = Field(default_factory=AnalysisPreferences)
    output: OutputPreferences = Field(default_factory=OutputPreferences)
    data_sources: DataSourcePreferences = Field(default_factory=DataSourcePreferences)
    compliance: CompliancePreferences = Field(default_factory=CompliancePreferences)

    model_config = ConfigDict(
        max_anystr_length=10000,  # Prevent JSONB bloat
        json_schema_extra={
            "examples": [{
                "analysis": {
                    "risk_tolerance": "moderate",
                    "preferred_metrics": ["P/E", "ROE", "Debt/Equity"],
                    "default_timeframe": "1Y"
                },
                "output": {
                    "verbosity": "standard",
                    "include_charts": True
                }
            }]
        }
    )


class UserPreferencesUpdate(BaseModel):
    """Request to update user preferences (partial update)."""
    analysis: Optional[AnalysisPreferences] = None
    output: Optional[OutputPreferences] = None
    data_sources: Optional[DataSourcePreferences] = None
    compliance: Optional[CompliancePreferences] = None


class UserPreferencesResponse(BaseModel):
    """Response for user preferences."""
    id: UUID
    user_id: UUID
    preferences: UserPreferencesSchema
    schema_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PreferencesListResponse(BaseModel):
    """Response for listing preferences."""
    preferences: UserPreferencesResponse
    defaults_applied: bool = Field(
        False,
        description="Whether default values were applied"
    )


# =============================================================================
# Phase 4: Memory Management Schemas
# =============================================================================

class MemoryFeedbackRequest(BaseModel):
    """Request to provide feedback on a memory."""
    feedback: Literal["helpful", "not_helpful"] = Field(
        ...,
        description="User feedback: 'helpful' boosts importance, 'not_helpful' reduces it"
    )


class MemoryFeedbackResponse(BaseModel):
    """Response after providing feedback."""
    status: str = "updated"
    memory_id: UUID
    feedback: str
    new_importance: float = Field(..., description="Updated importance score after feedback")


class MemoryUpdateRequest(BaseModel):
    """Request to update memory properties and content."""
    visibility: Optional[Literal["private", "shared"]] = Field(
        None,
        description="Update visibility level. PII scan required before sharing."
    )
    tags: Optional[List[str]] = Field(
        None,
        max_length=20,
        description="Replace tags (max 20 tags)"
    )
    archived: Optional[bool] = Field(
        None,
        description="Archive or unarchive the memory"
    )
    context: Optional[str] = Field(
        None,
        max_length=5000,
        description="Update the context (what was asked)"
    )
    decision: Optional[str] = Field(
        None,
        max_length=5000,
        description="Update the decision made"
    )
    rationale: Optional[str] = Field(
        None,
        max_length=2000,
        description="Update the reasoning behind the decision"
    )
    entities: Optional[List[str]] = Field(
        None,
        max_length=50,
        description="Replace entities list (max 50)"
    )

    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v):
        if v is not None:
            for tag in v:
                if len(tag) > 50:
                    raise ValueError("Tag name too long (max 50 chars)")
                if '<' in tag or '>' in tag:
                    raise ValueError("Invalid characters in tag name")
        return v

    @field_validator('entities')
    @classmethod
    def validate_entities_update(cls, v):
        if v is not None:
            if len(v) > 50:
                raise ValueError("Maximum 50 entities allowed")
            for entity in v:
                if len(entity) > 100:
                    raise ValueError("Entity name too long (max 100 chars)")
                if '<' in entity or '>' in entity:
                    raise ValueError("Invalid characters in entity name")
        return v


class MemoryUpdateResponse(BaseModel):
    """Response after updating memory."""
    status: str = "updated"
    memory_id: UUID
    updated_fields: List[str]


class MemoryListRequest(BaseModel):
    """Request for listing memories with filters."""
    memory_type: Optional[Literal["decision", "fact", "preference", "conversation"]] = None
    visibility: Optional[Literal["private", "shared"]] = None
    feedback: Optional[Literal["helpful", "not_helpful", "none"]] = None
    tags: Optional[List[str]] = None
    agent_name: Optional[str] = None
    include_archived: bool = Field(default=False, description="Include archived memories")
    limit: int = Field(default=50, ge=1, le=200, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class MemoryListResponse(BaseModel):
    """Response for listing memories."""
    memories: List[EpisodicMemoryResponse]
    count: int
    total: int = Field(..., description="Total matching memories (for pagination)")
    has_more: bool


class MemoryDeleteResponse(BaseModel):
    """Response after deleting a memory."""
    status: str = "deleted"
    memory_id: UUID


class MemoryStatsResponse(BaseModel):
    """Response for memory statistics."""
    total_memories: int
    by_type: Dict[str, int]
    by_feedback: Dict[str, int]
    by_visibility: Dict[str, int]
    archived_count: int
    avg_importance: float


# =============================================================================
# Organization Context Schemas
# =============================================================================

class OrgProfileUpdateRequest(BaseModel):
    """Request to update the organization profile that gets injected into agent context."""
    industry: Optional[str] = Field(None, max_length=100, description="Industry vertical (e.g. 'Financial Services')")
    description: Optional[str] = Field(None, max_length=2000, description="Brief organization description")
    compliance_frameworks: Optional[List[str]] = Field(None, max_length=20, description="Applicable compliance frameworks (e.g. ['SOX', 'GDPR'])")
    default_context: Optional[str] = Field(None, max_length=5000, description="Default context injected into all agent prompts for this org")


class OrgProfileResponse(BaseModel):
    """Response for organization profile."""
    tenant_id: UUID
    industry: Optional[str] = None
    description: Optional[str] = None
    compliance_frameworks: List[str] = Field(default_factory=list)
    default_context: Optional[str] = None
    shared_memory_count: int = 0
    updated_at: Optional[datetime] = None
