"""Tenant feature flag management API.

Two tiers of access:
  1. Tenant-scoped (any authenticated user): read own tenant's features
  2. Platform operator (is_platform_operator=True): read/write any tenant's features
"""

import logging
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    require_platform_operator,
)
from app.auth.providers.base import AuthUser
from app.core.database import get_db, get_db_no_tenant, get_system_db
from app.models.compliance_audit_log import AuditAction
from app.models.tenant import Tenant
from app.services.audit_chain import AuditChain
from app.services.tool_policy import TenantPolicy
from app.services.feature_flags import (
    FLAG_METADATA,
    get_feature,
    get_flag_metadata,
    get_tenant_features,
    invalidate_tenant_cache,
    is_known_flag,
    is_operator_only,
    validate_flag_dependencies,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ========================================
# Schemas
# ========================================

class FeatureToggleRequest(BaseModel):
    """Request to toggle a feature flag."""
    enabled: bool = Field(..., description="Whether to enable or disable the flag")


class FeatureMapResponse(BaseModel):
    """Resolved feature flags for a tenant."""
    features: Dict[str, bool]


class AdminFeatureMapResponse(BaseModel):
    """Resolved feature flags with source info (for platform operators)."""
    features: Dict[str, bool]
    overrides: Dict[str, bool]  # Only the explicitly set flags
    metadata: Dict[str, dict]   # Flag metadata (group, description, operator_only)


class TenantSummary(BaseModel):
    """Tenant summary for admin list."""
    id: str
    name: str
    slug: str
    is_active: bool
    feature_count: int  # Number of explicitly overridden features


# ========================================
# Tenant-Scoped Endpoints (any authenticated user)
# ========================================

@router.get(
    "/tenant/features",
    response_model=FeatureMapResponse,
    summary="Get resolved feature flags for current tenant",
)
async def get_my_tenant_features(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns the resolved feature map for the authenticated user's tenant.
    No source information is exposed — just flag name → boolean.
    """
    tenant = await _get_tenant(db, UUID(user.tenant_id))
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return FeatureMapResponse(features=get_tenant_features(tenant))


@router.put(
    "/tenant/features/{flag_name}",
    response_model=FeatureMapResponse,
    summary="Toggle a feature flag for current tenant (non-operator-only flags)",
)
async def toggle_my_tenant_feature(
    flag_name: str,
    body: FeatureToggleRequest,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_system_db),
):
    """Tenant admin/owner can toggle non-operator-only flags for their own tenant.

    ``db`` is the system session: the tenants table has no RLS UPDATE policy for the app role.
    The role check above gates the write and it only ever touches the caller's own tenant row.
    """
    if user.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires owner or admin role")

    if not is_known_flag(flag_name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown feature flag: {flag_name}")

    if is_operator_only(flag_name):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Flag '{flag_name}' requires platform operator access"
        )

    tenant = await _get_tenant(db, UUID(user.tenant_id))
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    # Validate dependencies
    dep_error = validate_flag_dependencies(flag_name, body.enabled, tenant)
    if dep_error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, dep_error)

    # Write ONLY to tenant.settings["features"][flag_name]
    _set_tenant_feature(tenant, flag_name, body.enabled)
    await db.flush()
    await db.refresh(tenant)
    invalidate_tenant_cache(str(tenant.id))

    logger.info(f"Tenant {tenant.slug} feature '{flag_name}' set to {body.enabled} by {user.email}")
    return FeatureMapResponse(features=get_tenant_features(tenant))


# ========================================
# Approval policy (Approvals & Policy plan, U3)
# ========================================

class ApprovalSettings(BaseModel):
    ttl_hours: int = Field(..., ge=0, le=24 * 365)  # 0 = never expires
    require_separate_approver: bool
    rule_of_two: bool


class ApprovalSettingsUpdate(BaseModel):
    ttl_hours: Optional[int] = Field(None, ge=0, le=24 * 365)
    require_separate_approver: Optional[bool] = None
    rule_of_two: Optional[bool] = None


def _approval_settings(tenant: Tenant) -> ApprovalSettings:
    p = TenantPolicy.from_settings(tenant.settings)
    return ApprovalSettings(ttl_hours=p.ttl_hours, require_separate_approver=p.require_separate_approver,
                            rule_of_two=p.rule_of_two)


def get_audit_chain(db: AsyncSession) -> AuditChain:
    """Seam for tests: the real chain needs Postgres."""
    return AuditChain(db)


@router.get("/tenant/approvals", response_model=ApprovalSettings, summary="Approval policy for the current tenant")
async def get_approval_settings(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant = await _get_tenant(db, UUID(user.tenant_id))
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return _approval_settings(tenant)


@router.put("/tenant/approvals", response_model=ApprovalSettings, summary="Update the approval policy")
async def update_approval_settings(
    body: ApprovalSettingsUpdate,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    system_db: AsyncSession = Depends(get_system_db),
):
    """Admins change TTL and separate-approver; only an owner changes the Rule of Two (audited)."""
    if user.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requires owner or admin role")
    if body.rule_of_two is not None and user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Changing the Rule of Two requires the owner role")

    # Write through the system session (no RLS UPDATE policy on tenants for the app role);
    # only the caller's own tenant row is loaded and changed, after the role checks above.
    tenant = await _get_tenant(system_db, UUID(user.tenant_id))
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    before = _approval_settings(tenant)
    settings = dict(tenant.settings or {})
    approvals = dict(settings.get("approvals") or {})
    for key, value in body.model_dump(exclude_none=True).items():
        approvals[key] = value
    settings["approvals"] = approvals
    tenant.settings = settings
    await system_db.flush()

    after = _approval_settings(tenant)
    if after.rule_of_two != before.rule_of_two:
        await get_audit_chain(db).append(
            tenant_id=tenant.id, agent_id="approvals", action=AuditAction.RULE_OF_TWO_CHANGED,
            decision="enabled" if after.rule_of_two else "disabled",
            rationale=f"Rule of Two {'enabled' if after.rule_of_two else 'disabled'} by {user.email}",
            user_id=UUID(user.id), actor_email=user.email, actor_name=getattr(user, "name", None),
            extra_metadata={"actor_id": user.id, "actor_role": user.role,
                            "previous": before.rule_of_two, "current": after.rule_of_two})
    for key in ("ttl_hours", "require_separate_approver"):
        previous, current = getattr(before, key), getattr(after, key)
        if previous != current:
            await get_audit_chain(db).append(
                tenant_id=tenant.id, agent_id="approvals", action=AuditAction.APPROVAL_SETTINGS_CHANGED,
                decision="changed", rationale=f"Approval setting {key} changed by {user.email}",
                user_id=UUID(user.id), actor_email=user.email, actor_name=getattr(user, "name", None),
                extra_metadata={"actor_id": user.id, "actor_role": user.role, "setting": key,
                                "previous": previous, "current": current})
    logger.info(f"Tenant {tenant.slug} approval settings updated by {user.email}")
    return after


# ========================================
# Platform Operator Endpoints (cross-tenant)
# ========================================

@router.get(
    "/admin/tenants",
    response_model=list[TenantSummary],
    summary="List all tenants (platform operator only)",
)
async def list_tenants(
    operator: AuthUser = Depends(require_platform_operator()),
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """List all tenants with feature override counts."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    tenants = result.scalars().all()
    return [
        TenantSummary(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            is_active=t.is_active,
            feature_count=len((t.settings or {}).get("features", {})),
        )
        for t in tenants
    ]


@router.get(
    "/admin/tenants/{tenant_id}/features",
    response_model=AdminFeatureMapResponse,
    summary="Get features for any tenant with source info (platform operator only)",
)
async def get_tenant_features_admin(
    tenant_id: UUID,
    operator: AuthUser = Depends(require_platform_operator()),
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """Returns resolved features + explicit overrides + flag metadata."""
    tenant = await _get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    return AdminFeatureMapResponse(
        features=get_tenant_features(tenant),
        overrides=(tenant.settings or {}).get("features", {}),
        metadata=get_flag_metadata(),
    )


@router.put(
    "/admin/tenants/{tenant_id}/features/{flag_name}",
    response_model=AdminFeatureMapResponse,
    summary="Toggle any feature for any tenant (platform operator only)",
)
async def toggle_tenant_feature_admin(
    tenant_id: UUID,
    flag_name: str,
    body: FeatureToggleRequest,
    operator: AuthUser = Depends(require_platform_operator()),
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """Platform operator can toggle any flag (including operator-only) for any tenant."""
    if not is_known_flag(flag_name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown feature flag: {flag_name}")

    tenant = await _get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    # Validate dependencies
    dep_error = validate_flag_dependencies(flag_name, body.enabled, tenant)
    if dep_error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, dep_error)

    _set_tenant_feature(tenant, flag_name, body.enabled)
    await db.flush()
    await db.refresh(tenant)
    invalidate_tenant_cache(str(tenant.id))

    logger.info(
        f"Platform operator {operator.email} set '{flag_name}' to {body.enabled} "
        f"for tenant {tenant.slug}"
    )

    return AdminFeatureMapResponse(
        features=get_tenant_features(tenant),
        overrides=(tenant.settings or {}).get("features", {}),
        metadata=get_flag_metadata(),
    )


@router.get(
    "/admin/feature-metadata",
    summary="Get all known feature flag metadata",
)
async def get_feature_metadata(
    operator: AuthUser = Depends(require_platform_operator()),
):
    """Returns flag groups, descriptions, and operator_only classification."""
    return get_flag_metadata()


# ========================================
# Helpers
# ========================================

async def _get_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant | None:
    """Load a tenant by ID."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


def _set_tenant_feature(tenant: Tenant, flag_name: str, enabled: bool):
    """Write a feature flag to tenant.settings.features ONLY.

    Never modifies sibling keys (signup_mode, org_profile, etc.).
    """
    settings = dict(tenant.settings or {})
    features = dict(settings.get("features", {}))
    features[flag_name] = enabled
    settings["features"] = features
    tenant.settings = settings
