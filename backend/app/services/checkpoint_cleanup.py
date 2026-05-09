"""
Checkpoint Cleanup Service - Removes stale workflow checkpoints.

Part of Phase 4: Crash Recovery.

Checkpoints are retained for 24 hours after workflow completion to allow:
- Debugging failed workflows
- Audit replay for compliance
- Recovery from server restarts

After retention period, checkpoints are deleted to prevent database bloat.
"""

from datetime import datetime, timedelta
from typing import Optional
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Default retention period: 24 hours
DEFAULT_CHECKPOINT_RETENTION_HOURS = 24


async def cleanup_stale_checkpoints(
    db: AsyncSession,
    retention_hours: int = DEFAULT_CHECKPOINT_RETENTION_HOURS,
    tenant_id: Optional[UUID] = None,
    dry_run: bool = False,
) -> dict:
    """
    Delete workflow checkpoints older than retention period.

    Finds completed workflows where all checkpoints have status "completed"
    and the most recent checkpoint is older than the retention period.

    Args:
        db: Database session
        retention_hours: Hours to retain checkpoints (default: 24)
        tenant_id: Optional tenant ID to scope cleanup
        dry_run: If True, only count checkpoints without deleting

    Returns:
        Dict with cleanup statistics:
        - workflows_cleaned: Number of workflows whose checkpoints were deleted
        - checkpoints_deleted: Total checkpoints deleted
        - oldest_checkpoint: Timestamp of oldest deleted checkpoint
    """
    cutoff_time = datetime.utcnow() - timedelta(hours=retention_hours)

    logger.info(
        f"Starting checkpoint cleanup",
        extra={
            "retention_hours": retention_hours,
            "cutoff_time": cutoff_time.isoformat(),
            "tenant_id": str(tenant_id) if tenant_id else "all",
            "dry_run": dry_run,
        }
    )

    # Find workflows with all completed checkpoints older than cutoff
    # Subquery: workflows where latest checkpoint is completed and old
    subquery = (
        select(
            WorkflowCheckpoint.workflow_uuid,
            func.max(WorkflowCheckpoint.completed_at).label("latest_completed"),
            func.count().filter(WorkflowCheckpoint.status == "completed").label("completed_count"),
            func.count().label("total_count"),
        )
        .group_by(WorkflowCheckpoint.workflow_uuid)
        .having(
            and_(
                # All checkpoints must be completed
                func.count().filter(WorkflowCheckpoint.status == "completed") == func.count(),
                # Latest completion must be older than cutoff
                func.max(WorkflowCheckpoint.completed_at) < cutoff_time,
            )
        )
    )

    if tenant_id:
        subquery = subquery.where(WorkflowCheckpoint.tenant_id == tenant_id)

    subquery = subquery.subquery()

    # Get list of workflow UUIDs to clean up
    workflow_query = select(subquery.c.workflow_uuid)
    result = await db.execute(workflow_query)
    workflow_uuids = [row[0] for row in result.fetchall()]

    if not workflow_uuids:
        logger.info("No stale workflows found for cleanup")
        return {
            "workflows_cleaned": 0,
            "checkpoints_deleted": 0,
            "oldest_checkpoint": None,
        }

    # Count checkpoints to be deleted
    count_query = select(func.count()).select_from(WorkflowCheckpoint).where(
        WorkflowCheckpoint.workflow_uuid.in_(workflow_uuids)
    )
    count_result = await db.execute(count_query)
    checkpoint_count = count_result.scalar() or 0

    # Get oldest checkpoint time for reporting
    oldest_query = select(func.min(WorkflowCheckpoint.created_at)).where(
        WorkflowCheckpoint.workflow_uuid.in_(workflow_uuids)
    )
    oldest_result = await db.execute(oldest_query)
    oldest_checkpoint = oldest_result.scalar()

    if dry_run:
        logger.info(
            f"Dry run: would delete {checkpoint_count} checkpoints from {len(workflow_uuids)} workflows",
            extra={
                "workflows": len(workflow_uuids),
                "checkpoints": checkpoint_count,
                "oldest": oldest_checkpoint.isoformat() if oldest_checkpoint else None,
            }
        )
        return {
            "workflows_cleaned": len(workflow_uuids),
            "checkpoints_deleted": checkpoint_count,
            "oldest_checkpoint": oldest_checkpoint.isoformat() if oldest_checkpoint else None,
            "dry_run": True,
        }

    # Delete checkpoints for stale workflows
    delete_stmt = delete(WorkflowCheckpoint).where(
        WorkflowCheckpoint.workflow_uuid.in_(workflow_uuids)
    )
    await db.execute(delete_stmt)
    await db.commit()

    logger.info(
        f"Checkpoint cleanup complete: {checkpoint_count} checkpoints deleted from {len(workflow_uuids)} workflows",
        extra={
            "workflows_cleaned": len(workflow_uuids),
            "checkpoints_deleted": checkpoint_count,
            "oldest_checkpoint": oldest_checkpoint.isoformat() if oldest_checkpoint else None,
        }
    )

    return {
        "workflows_cleaned": len(workflow_uuids),
        "checkpoints_deleted": checkpoint_count,
        "oldest_checkpoint": oldest_checkpoint.isoformat() if oldest_checkpoint else None,
    }


async def cleanup_failed_workflow_checkpoints(
    db: AsyncSession,
    retention_hours: int = DEFAULT_CHECKPOINT_RETENTION_HOURS * 7,  # 7 days for failed
    tenant_id: Optional[UUID] = None,
    dry_run: bool = False,
) -> dict:
    """
    Delete checkpoints from failed workflows older than retention period.

    Failed workflows have at least one checkpoint with status "failed".
    These are kept longer for debugging purposes.

    Args:
        db: Database session
        retention_hours: Hours to retain failed checkpoints (default: 168 = 7 days)
        tenant_id: Optional tenant ID to scope cleanup
        dry_run: If True, only count checkpoints without deleting

    Returns:
        Dict with cleanup statistics
    """
    cutoff_time = datetime.utcnow() - timedelta(hours=retention_hours)

    logger.info(
        f"Starting failed workflow checkpoint cleanup",
        extra={
            "retention_hours": retention_hours,
            "cutoff_time": cutoff_time.isoformat(),
            "tenant_id": str(tenant_id) if tenant_id else "all",
        }
    )

    # Find workflows with failed checkpoints older than cutoff
    subquery = (
        select(
            WorkflowCheckpoint.workflow_uuid,
            func.max(WorkflowCheckpoint.created_at).label("latest_created"),
        )
        .where(WorkflowCheckpoint.status == "failed")
        .group_by(WorkflowCheckpoint.workflow_uuid)
        .having(func.max(WorkflowCheckpoint.created_at) < cutoff_time)
    )

    if tenant_id:
        subquery = subquery.where(WorkflowCheckpoint.tenant_id == tenant_id)

    subquery = subquery.subquery()

    # Get workflow UUIDs
    workflow_query = select(subquery.c.workflow_uuid)
    result = await db.execute(workflow_query)
    workflow_uuids = [row[0] for row in result.fetchall()]

    if not workflow_uuids:
        logger.info("No stale failed workflows found for cleanup")
        return {
            "workflows_cleaned": 0,
            "checkpoints_deleted": 0,
            "oldest_checkpoint": None,
        }

    # Count and delete
    count_query = select(func.count()).select_from(WorkflowCheckpoint).where(
        WorkflowCheckpoint.workflow_uuid.in_(workflow_uuids)
    )
    count_result = await db.execute(count_query)
    checkpoint_count = count_result.scalar() or 0

    if dry_run:
        return {
            "workflows_cleaned": len(workflow_uuids),
            "checkpoints_deleted": checkpoint_count,
            "oldest_checkpoint": None,
            "dry_run": True,
        }

    delete_stmt = delete(WorkflowCheckpoint).where(
        WorkflowCheckpoint.workflow_uuid.in_(workflow_uuids)
    )
    await db.execute(delete_stmt)
    await db.commit()

    logger.info(
        f"Failed workflow cleanup complete: {checkpoint_count} checkpoints deleted",
        extra={
            "workflows_cleaned": len(workflow_uuids),
            "checkpoints_deleted": checkpoint_count,
        }
    )

    return {
        "workflows_cleaned": len(workflow_uuids),
        "checkpoints_deleted": checkpoint_count,
        "oldest_checkpoint": None,
    }


async def get_checkpoint_statistics(
    db: AsyncSession,
    tenant_id: Optional[UUID] = None,
) -> dict:
    """
    Get statistics about workflow checkpoints for monitoring.

    Returns:
        Dict with statistics:
        - total_checkpoints: Total number of checkpoints
        - completed_workflows: Workflows with all checkpoints completed
        - failed_workflows: Workflows with at least one failed checkpoint
        - running_workflows: Workflows with running checkpoints
        - oldest_checkpoint_age_hours: Age of oldest checkpoint in hours
        - storage_estimate_mb: Estimated storage used (rough)
    """
    # Total checkpoints
    total_query = select(func.count()).select_from(WorkflowCheckpoint)
    if tenant_id:
        total_query = total_query.where(WorkflowCheckpoint.tenant_id == tenant_id)
    total_result = await db.execute(total_query)
    total_checkpoints = total_result.scalar() or 0

    # Workflow status counts
    status_query = (
        select(
            WorkflowCheckpoint.workflow_uuid,
            func.bool_or(WorkflowCheckpoint.status == "failed").label("has_failed"),
            func.bool_or(WorkflowCheckpoint.status == "running").label("has_running"),
        )
        .group_by(WorkflowCheckpoint.workflow_uuid)
    )
    if tenant_id:
        status_query = status_query.where(WorkflowCheckpoint.tenant_id == tenant_id)

    status_result = await db.execute(status_query)
    workflow_statuses = status_result.fetchall()

    completed_workflows = sum(1 for r in workflow_statuses if not r.has_failed and not r.has_running)
    failed_workflows = sum(1 for r in workflow_statuses if r.has_failed)
    running_workflows = sum(1 for r in workflow_statuses if r.has_running)

    # Oldest checkpoint
    oldest_query = select(func.min(WorkflowCheckpoint.created_at))
    if tenant_id:
        oldest_query = oldest_query.where(WorkflowCheckpoint.tenant_id == tenant_id)
    oldest_result = await db.execute(oldest_query)
    oldest_checkpoint = oldest_result.scalar()

    oldest_age_hours = None
    if oldest_checkpoint:
        oldest_age_hours = (datetime.utcnow() - oldest_checkpoint).total_seconds() / 3600

    return {
        "total_checkpoints": total_checkpoints,
        "total_workflows": len(workflow_statuses),
        "completed_workflows": completed_workflows,
        "failed_workflows": failed_workflows,
        "running_workflows": running_workflows,
        "oldest_checkpoint_age_hours": round(oldest_age_hours, 2) if oldest_age_hours else None,
        # Rough storage estimate: ~1KB per checkpoint average
        "storage_estimate_mb": round(total_checkpoints / 1024, 2),
    }
