"""Pydantic schemas for ChatSession API."""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


class ChatMessageBase(BaseModel):
    """Schema for individual chat messages."""
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(None, description="When the message was sent")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional message metadata")


class ChatMessageCreate(BaseModel):
    """Schema for creating a new message in a session."""
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content")
    metadata: Optional[Dict[str, Any]] = None


class ChatSessionBase(BaseModel):
    """Base schema for chat sessions."""
    title: Optional[str] = Field(None, max_length=255, description="Session title")
    context_type: Literal["agent", "workflow"] = Field(..., description="Context: agent or workflow")
    is_draft: bool = Field(False, description="Whether this is a draft session")


class ChatSessionCreate(ChatSessionBase):
    """Schema for creating a new chat session."""
    agent_id: Optional[UUID] = Field(None, description="Associated agent ID")
    workflow_id: Optional[UUID] = Field(None, description="Associated workflow ID")
    messages: Optional[List[ChatMessageCreate]] = Field(default_factory=list, description="Initial messages")


class ChatSessionUpdate(BaseModel):
    """Schema for updating a chat session."""
    title: Optional[str] = Field(None, max_length=255)
    is_draft: Optional[bool] = None
    is_pinned: Optional[bool] = None


class ChatSessionResponse(ChatSessionBase):
    """Schema for chat session API responses."""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    agent_id: Optional[UUID] = None
    workflow_id: Optional[UUID] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    message_count: int = 0
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatSessionListResponse(BaseModel):
    """Schema for paginated chat session list."""
    sessions: List[ChatSessionResponse]
    total: int
    page: int = 1
    page_size: int = 20


class ChatSessionDetailResponse(ChatSessionResponse):
    """Schema for detailed chat session with agent/workflow info."""
    agent_name: Optional[str] = None
    workflow_name: Optional[str] = None


class AddMessageRequest(BaseModel):
    """Request to add a message to an existing session."""
    message: ChatMessageCreate


class AddMessageResponse(BaseModel):
    """Response after adding a message."""
    session_id: UUID
    message: Dict[str, Any]
    message_count: int
