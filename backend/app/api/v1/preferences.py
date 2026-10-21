"""
User Preferences API Endpoints for Oniva Enhanced Memory System.

Provides endpoints for:
- Getting user preferences (with defaults merged)
- Setting/updating user preferences
- Deleting preferences (reset to defaults)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.config import get_settings
from app.services.user_preferences import get_user_preferences_service
from app.schemas.memory import (
    UserPreferencesSchema,
    UserPreferencesUpdate,
    UserPreferencesResponse,
    PreferencesListResponse,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _check_feature_enabled():
    """Check if user preferences feature is enabled."""
    settings = get_settings()
    if not getattr(settings, 'USE_USER_PREFERENCES', False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User preferences feature is not enabled"
        )


@router.get("", response_model=PreferencesListResponse)
async def get_preferences(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get current user preferences.

    Returns preferences merged with defaults.
    If user has no preferences set, returns defaults only.
    """
    _check_feature_enabled()

    service = get_user_preferences_service()
    user_id = UUID(user.id)

    # Get raw record to check if defaults were applied
    record = await service.get_preferences_record(db, tenant_id, user_id)
    defaults_applied = record is None

    # Get merged preferences
    prefs_dict = await service.get_preferences(db, tenant_id, user_id)
    prefs_schema = UserPreferencesSchema(**prefs_dict)

    # If no record exists, create a mock response
    if record is None:
        from datetime import datetime
        return PreferencesListResponse(
            preferences=UserPreferencesResponse(
                id=UUID('00000000-0000-0000-0000-000000000000'),
                user_id=user_id,
                preferences=prefs_schema,
                schema_version=service.SCHEMA_VERSION,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ),
            defaults_applied=True,
        )

    return PreferencesListResponse(
        preferences=UserPreferencesResponse(
            id=record.id,
            user_id=record.user_id,
            preferences=prefs_schema,
            schema_version=record.schema_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        ),
        defaults_applied=defaults_applied,
    )


@router.put("", response_model=UserPreferencesResponse)
async def set_preferences(
    preferences: UserPreferencesSchema,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Set user preferences (full replace).

    Replaces all preferences with the provided values.
    Use PATCH for partial updates.
    """
    _check_feature_enabled()

    service = get_user_preferences_service()
    user_id = UUID(user.id)

    record = await service.set_preferences(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        preferences=preferences.model_dump(),
    )

    return UserPreferencesResponse(
        id=record.id,
        user_id=record.user_id,
        preferences=UserPreferencesSchema(**record.preferences),
        schema_version=record.schema_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.patch("", response_model=UserPreferencesResponse)
async def update_preferences(
    updates: UserPreferencesUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Update user preferences (partial update).

    Merges provided preferences with existing ones.
    Only specified fields are updated.
    """
    _check_feature_enabled()

    service = get_user_preferences_service()
    user_id = UUID(user.id)

    # Build updates dict from non-None fields
    updates_dict = {}
    if updates.analysis is not None:
        updates_dict["analysis"] = updates.analysis.model_dump()
    if updates.output is not None:
        updates_dict["output"] = updates.output.model_dump()
    if updates.data_sources is not None:
        updates_dict["data_sources"] = updates.data_sources.model_dump()
    if updates.compliance is not None:
        updates_dict["compliance"] = updates.compliance.model_dump()

    if not updates_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No preferences to update"
        )

    record = await service.update_preferences(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        updates=updates_dict,
    )

    return UserPreferencesResponse(
        id=record.id,
        user_id=record.user_id,
        preferences=UserPreferencesSchema(**record.preferences),
        schema_version=record.schema_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preferences(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Delete user preferences (reset to defaults).

    After deletion, get_preferences will return default values.
    """
    _check_feature_enabled()

    service = get_user_preferences_service()
    user_id = UUID(user.id)

    deleted = await service.delete_preferences(db, tenant_id, user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No preferences found to delete"
        )
