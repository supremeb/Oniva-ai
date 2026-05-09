"""Task schema for session-scoped task tracking."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Task(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    title: str
    description: str | None = None
    status: Literal["pending", "in_progress", "completed"] = "pending"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
