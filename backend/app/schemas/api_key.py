from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
import ipaddress


class APIKeyBase(BaseModel):
    provider: str = Field(
        ...,
        description="Provider name. Supported LLM providers: openrouter (recommended), openai, anthropic, google (Gemini), parallelai. Supported tools: exa, perplexity, tavily, e2b."
    )
    name: str = Field(..., description="User-friendly name for this API key")
    is_default: bool = Field(default=False, description="Use this key as default for the provider")
    key_metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional configuration")

    # Enterprise: Scoping and security
    scopes: List[str] = Field(
        default=["*:*"],
        description="Granted scopes (e.g., ['workflows:read', 'workflows:execute'])"
    )
    allowed_ip_addresses: Optional[List[str]] = Field(
        default=None,
        description="IP whitelist (CIDR notation supported, e.g., ['192.168.1.0/24', '10.0.0.100'])"
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Expiration date/time (null = never expires)"
    )
    rate_limit_per_hour: int = Field(
        default=1000,
        description="Max requests per hour for this key"
    )

    @field_validator('scopes', mode='before')
    @classmethod
    def normalize_scope_list(cls, v):
        """Existing rows store the default as ``'*:*'`` with literal quotes; normalise on read."""
        if isinstance(v, (list, tuple)):
            return [s for s in (str(x).strip().strip("'\"") for x in v) if s]
        return v

    @field_validator('allowed_ip_addresses')
    @classmethod
    def validate_ip_addresses(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate IP addresses and CIDR ranges."""
        if v is None:
            return None

        validated = []
        for ip_str in v:
            try:
                # Try parsing as network (CIDR) or address
                if '/' in ip_str:
                    ipaddress.ip_network(ip_str, strict=False)
                else:
                    ipaddress.ip_address(ip_str)
                validated.append(ip_str)
            except ValueError:
                raise ValueError(f"Invalid IP address or CIDR range: {ip_str}")

        return validated


class APIKeyCreate(APIKeyBase):
    api_key: str = Field(..., description="The actual API key (will be encrypted)")


class APIKeyUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    key_metadata: Optional[Dict[str, Any]] = None
    scopes: Optional[List[str]] = None
    allowed_ip_addresses: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    rate_limit_per_hour: Optional[int] = None


class APIKeyResponse(APIKeyBase):
    id: UUID
    user_id: UUID
    is_active: bool
    last_used_at: Optional[datetime]
    last_used_ip: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Status indicators
    is_expired: bool = Field(default=False, description="Whether the key has expired")
    expires_in_days: Optional[int] = Field(default=None, description="Days until expiration")

    # Masked key for display
    masked_key: str = Field(default="sk-****", description="Masked version of the API key")

    class Config:
        from_attributes = True
        populate_by_name = True


class APIKeyListResponse(BaseModel):
    api_keys: list[APIKeyResponse]
    total: int
