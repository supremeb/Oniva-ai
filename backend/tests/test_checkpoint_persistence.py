"""
Tests for WorkflowCheckpoint database persistence.

Part of Phase 4: Crash Recovery.

Tests checkpoint CRUD operations, cleanup jobs, and recovery scenarios.
"""

import pytest
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.workflow_checkpoint import WorkflowCheckpoint
from app.services.checkpoint_cleanup import (
    cleanup_stale_checkpoints,
    get_checkpoint_statistics,
)


# Test fixtures
@pytest.fixture
def tenant_id():
    """Create a test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
def workflow_uuid():
    """Create a test workflow UUID."""
    return uuid.uuid4()


class TestWorkflowCheckpointModel:
    """Tests for WorkflowCheckpoint SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_create_checkpoint(self, test_db: AsyncSession, tenant_id, workflow_uuid):
        """Test creating a checkpoint in database."""
        checkpoint = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="llm:node-1",
            node_id="node-1",
            input_data={"prompt": "test"},
            status="running",
        )

        test_db.add(checkpoint)
        await test_db.commit()
        await test_db.refresh(checkpoint)

        assert checkpoint.id is not None
        assert checkpoint.status == "running"
        assert checkpoint.input_data == {"prompt": "test"}

    @pytest.mark.asyncio
    async def test_mark_completed(self, test_db: AsyncSession, tenant_id, workflow_uuid):
        """Test marking checkpoint as completed."""
        checkpoint = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="llm:node-1",
            node_id="node-1",
            input_data={},
            status="running",
            started_at=datetime.utcnow(),
        )

        test_db.add(checkpoint)
        await test_db.commit()

        # Mark completed
        checkpoint.mark_completed({"result": "success"})
        await test_db.commit()

        assert checkpoint.status == "completed"
        assert checkpoint.output_data == {"result": "success"}
        assert checkpoint.completed_at is not None
        assert checkpoint.elapsed_ms is not None
        assert checkpoint.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_mark_failed(self, test_db: AsyncSession, tenant_id, workflow_uuid):
        """Test marking checkpoint as failed."""
        checkpoint = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="llm:node-1",
            node_id="node-1",
            input_data={},
            status="running",
            started_at=datetime.utcnow(),
        )

        test_db.add(checkpoint)
        await test_db.commit()

        # Mark failed
        checkpoint.mark_failed("Connection timeout")
        await test_db.commit()

        assert checkpoint.status == "failed"
        assert checkpoint.error_message == "Connection timeout"
        assert checkpoint.completed_at is not None

    @pytest.mark.asyncio
    async def test_to_dict(self, test_db: AsyncSession, tenant_id, workflow_uuid):
        """Test checkpoint serialization to dict."""
        checkpoint = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=1,
            step_name="code:node-2",
            node_id="node-2",
            input_data={"code": "print('hello')"},
            status="completed",
            output_data={"stdout": "hello"},
        )

        test_db.add(checkpoint)
        await test_db.commit()

        result = checkpoint.to_dict()

        assert result["step_number"] == 1
        assert result["step_name"] == "code:node-2"
        assert result["status"] == "completed"
        assert result["has_output"] is True

    @pytest.mark.asyncio
    async def test_unique_constraint(self, test_db: AsyncSession, tenant_id, workflow_uuid):
        """Test unique constraint on workflow_uuid + step_number."""
        checkpoint1 = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="node-1",
            node_id="node-1",
            input_data={},
        )

        test_db.add(checkpoint1)
        await test_db.commit()

        # Try to create another checkpoint with same workflow_uuid and step_number
        checkpoint2 = WorkflowCheckpoint(
            tenant_id=tenant_id,
            workflow_uuid=workflow_uuid,
            step_number=0,  # Same step number - should fail
            step_name="node-1-duplicate",
            node_id="node-1",
            input_data={},
        )

        test_db.add(checkpoint2)

        with pytest.raises(Exception):  # IntegrityError
            await test_db.commit()


class TestCheckpointCleanup:
    """Tests for checkpoint cleanup service."""

    @pytest.mark.asyncio
    async def test_cleanup_stale_checkpoints(
        self, test_db: AsyncSession, tenant_id
    ):
        """Test cleaning up old completed checkpoints."""
        old_workflow_uuid = uuid.uuid4()
        new_workflow_uuid = uuid.uuid4()

        # Create old checkpoints (completed 48 hours ago)
        old_time = datetime.utcnow() - timedelta(hours=48)
        for i in range(3):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=old_workflow_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status="completed",
                completed_at=old_time,
            )
            test_db.add(checkpoint)

        # Create new checkpoints (completed 1 hour ago)
        new_time = datetime.utcnow() - timedelta(hours=1)
        for i in range(2):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=new_workflow_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status="completed",
                completed_at=new_time,
            )
            test_db.add(checkpoint)

        await test_db.commit()

        # Run cleanup
        result = await cleanup_stale_checkpoints(
            test_db,
            retention_hours=24,
            tenant_id=tenant_id,
        )

        assert result["workflows_cleaned"] == 1
        assert result["checkpoints_deleted"] == 3

        # Verify new checkpoints still exist
        count_query = select(func.count()).select_from(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_uuid == new_workflow_uuid
        )
        count_result = await test_db.execute(count_query)
        assert count_result.scalar() == 2

    @pytest.mark.asyncio
    async def test_cleanup_dry_run(
        self, test_db: AsyncSession, tenant_id
    ):
        """Test dry run mode doesn't delete anything."""
        workflow_uuid = uuid.uuid4()
        old_time = datetime.utcnow() - timedelta(hours=48)

        for i in range(2):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=workflow_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status="completed",
                completed_at=old_time,
            )
            test_db.add(checkpoint)

        await test_db.commit()

        # Run dry run
        result = await cleanup_stale_checkpoints(
            test_db,
            retention_hours=24,
            tenant_id=tenant_id,
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["checkpoints_deleted"] == 2

        # Verify checkpoints still exist
        count_query = select(func.count()).select_from(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_uuid == workflow_uuid
        )
        count_result = await test_db.execute(count_query)
        assert count_result.scalar() == 2

    @pytest.mark.asyncio
    async def test_no_cleanup_running_workflows(
        self, test_db: AsyncSession, tenant_id
    ):
        """Test that running workflows are not cleaned up."""
        workflow_uuid = uuid.uuid4()
        old_time = datetime.utcnow() - timedelta(hours=48)

        # Create checkpoints with one still running
        for i, status in enumerate(["completed", "completed", "running"]):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=workflow_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status=status,
                completed_at=old_time if status == "completed" else None,
            )
            test_db.add(checkpoint)

        await test_db.commit()

        # Run cleanup
        result = await cleanup_stale_checkpoints(
            test_db,
            retention_hours=24,
            tenant_id=tenant_id,
        )

        # Should not clean up because workflow is still running
        assert result["workflows_cleaned"] == 0


class TestCheckpointStatistics:
    """Tests for checkpoint statistics."""

    @pytest.mark.asyncio
    async def test_get_statistics(
        self, test_db: AsyncSession, tenant_id
    ):
        """Test getting checkpoint statistics."""
        # Create completed workflow
        completed_uuid = uuid.uuid4()
        for i in range(3):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=completed_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status="completed",
            )
            test_db.add(checkpoint)

        # Create failed workflow
        failed_uuid = uuid.uuid4()
        for i, status in enumerate(["completed", "failed"]):
            checkpoint = WorkflowCheckpoint(
                tenant_id=tenant_id,
                workflow_uuid=failed_uuid,
                step_number=i,
                step_name=f"step-{i}",
                node_id=f"node-{i}",
                input_data={},
                status=status,
            )
            test_db.add(checkpoint)

        await test_db.commit()

        # Get statistics
        stats = await get_checkpoint_statistics(test_db, tenant_id)

        assert stats["total_checkpoints"] == 5
        assert stats["total_workflows"] == 2
        assert stats["completed_workflows"] == 1
        assert stats["failed_workflows"] == 1
        assert stats["running_workflows"] == 0


# Conftest for database session fixture
# This should be in conftest.py but included here for reference
"""
@pytest.fixture
async def test_db():
    '''Create test database session.'''
    from app.core.database import get_test_db
    async with get_test_db() as session:
        yield session
        await session.rollback()
"""
