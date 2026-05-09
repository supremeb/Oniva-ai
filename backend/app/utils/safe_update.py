"""
Systemic safe_update utility for PATCH endpoints.

Derives an allowlist from the Pydantic schema's declared fields,
subtracts a deny-list of immutable/security-sensitive columns,
and only sets attributes that pass both checks.
"""

import logging
from typing import Type

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Fields that must NEVER be user-settable via PATCH
DENIED_FIELDS = frozenset({
    "id",
    "tenant_id",
    "user_id",
    "team_id",
    "row_version",
    "cloned_from_id",
    "clone_count",
    "created_at",
    "updated_at",
})


def safe_update(instance, update_data: dict, schema_class: Type[BaseModel]) -> int:
    """Apply validated updates to an ORM instance.

    Args:
        instance: SQLAlchemy ORM object to update.
        update_data: Dict from schema.model_dump(exclude_unset=True).
        schema_class: The Pydantic schema class whose fields define the allowlist.

    Returns:
        Number of fields actually updated.
    """
    allowed = set(schema_class.model_fields.keys()) - DENIED_FIELDS
    updated = 0
    for field, value in update_data.items():
        if field in allowed:
            setattr(instance, field, value)
            updated += 1
        else:
            logger.warning("safe_update: blocked field '%s' on %s", field, type(instance).__name__)
    return updated
