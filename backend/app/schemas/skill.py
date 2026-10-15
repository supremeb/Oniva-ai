from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from uuid import UUID

class SkillBase(BaseModel):
    name: str = Field(..., description="Unique name of the skill")
    description: str = Field(..., description="Description of what the skill does")
    category: str = Field("custom", description="Category of the skill (e.g., 'web', 'code', 'custom')")
    author: str = Field("unknown", description="Author of the skill")
    version: str = Field("1.0.0", description="Semantic version of the skill")
    skill_dependencies: List[str] = Field(default_factory=list, alias="requires", description="List of other skills this skill depends on")
    mcp_dependencies: List[str] = Field(default_factory=list, description="List of MCP servers this skill depends on")
    config_schema: Optional[Dict[str, Any]] = Field(None, description="JSON schema for configuration")
    mock: bool = Field(False, description="True if the skill returns simulated data")

    @field_validator("name")
    def validate_name(cls, v):
        import re
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("Skill name must be alphanumeric with underscores")
        return v
    
    @field_validator("version")
    def validate_version(cls, v):
        import re
        if not re.match(r"^\d+\.\d+\.\d+$", v):
            raise ValueError("Version must be in semantic format (X.Y.Z)")
        return v

class SkillCreate(SkillBase):
    code: Optional[str] = Field(None, description="Python code for custom skills")
    is_custom: bool = True
    config: Optional[Dict[str, Any]] = None

class SkillUpdate(BaseModel):
    description: Optional[str] = None
    category: Optional[str] = None
    version: Optional[str] = None
    code: Optional[str] = None
    requires: Optional[List[str]] = Field(None, alias="skill_dependencies")
    mcp_dependencies: Optional[List[str]] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None

class SkillRead(SkillBase):
    id: UUID
    author_id: Optional[UUID] = None
    is_custom: bool
    is_system: bool = False
    enabled: bool
    code: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
