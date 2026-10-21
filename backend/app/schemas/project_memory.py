"""Pydantic schemas for the project memory API (plan U32)."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryFileOut(BaseModel):
    id: UUID
    path: str
    content: str
    version: int
    status: str
    source_kind: str
    source_conversation_id: Optional[UUID] = None
    flags: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MemoryBulletOut(BaseModel):
    id: UUID
    bullet_key: str
    section: str
    text: str
    helpful: int
    harmful: int
    status: str
    source_kind: str
    source_conversation_id: Optional[UUID] = None
    source_run_id: Optional[str] = None
    flags: Optional[Any] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MemoryOverview(BaseModel):
    """Everything a reviewer sees: all files and bullets (pending ones included) and the rendered playbook."""
    project_id: UUID
    files: List[MemoryFileOut]
    bullets: List[MemoryBulletOut]
    playbook: Optional[str] = None
    pending_count: int = 0


class MemoryFilePut(BaseModel):
    content: str
    expected_version: Optional[int] = Field(None, ge=1, description="Optimistic check; 409 on mismatch")


class MemoryBulletPatch(BaseModel):
    text: Optional[str] = Field(None, min_length=1)
    section: Optional[str] = Field(None, min_length=1)


class MemoryDecisionOut(BaseModel):
    id: UUID
    kind: str  # "file" | "bullet"
    status: str  # "active" | "rejected"


class MemoryPurgeOut(BaseModel):
    files_deleted: int
    bullets_deleted: int


class MemoryExport(BaseModel):
    project_id: UUID
    exported_at: datetime
    files: List[MemoryFileOut]
    bullets: List[MemoryBulletOut]
    playbook: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
