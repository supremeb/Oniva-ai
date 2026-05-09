"""
Skill Audit Service
Provides logging and querying of skill executions with full audit trail support.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skills import SkillExecution

logger = logging.getLogger(__name__)


class SkillAuditService:
    """
    Service for logging and querying skill executions.

    Provides:
    - Audit logging for all skill executions
    - Tenant-isolated queries
    - Integrity verification via hashes
    - Query methods for compliance and debugging
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _compute_hash(data: Any) -> Optional[str]:
        """Compute SHA-256 hash of data for integrity verification."""
        if data is None:
            return None
        try:
            # Serialize to JSON with sorted keys for deterministic hashing
            serialized = json.dumps(data, sort_keys=True, default=str)
            return hashlib.sha256(serialized.encode()).hexdigest()
        except Exception:
            return None

    async def log_execution(
        self,
        *,
        skill_name: str,
        tenant_id: Union[str, UUID],
        status: str,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        user_id: Optional[Union[str, UUID]] = None,
        session_id: Optional[str] = None,
        workflow_id: Optional[Union[str, UUID]] = None,
        workflow_run_id: Optional[Union[str, UUID]] = None,
        node_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        skill_id: Optional[Union[str, UUID]] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        audit_metadata: Optional[Dict[str, Any]] = None,
    ) -> SkillExecution:
        """
        Log a skill execution for audit purposes.

        Args:
            skill_name: Name of the skill executed
            tenant_id: Tenant ID for multi-tenancy
            status: Execution status ('success', 'failed', 'timeout')
            inputs: Input parameters (will be hashed for integrity)
            outputs: Output results (will be hashed for integrity)
            user_id: User who triggered the execution
            session_id: Session identifier
            workflow_id: Workflow ID if part of a workflow
            workflow_run_id: Specific workflow run ID
            node_id: Workflow node ID
            agent_id: Agent that executed the skill
            skill_id: Database skill ID (if applicable)
            duration_ms: Execution duration in milliseconds
            error_message: Error message if failed
            error_type: Exception class name if failed
            audit_metadata: Additional context (IP, user agent, etc.)

        Returns:
            Created SkillExecution record
        """
        # Convert string UUIDs if needed
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
        if isinstance(user_id, str):
            user_id = UUID(user_id)
        if isinstance(workflow_id, str):
            workflow_id = UUID(workflow_id)
        if isinstance(workflow_run_id, str):
            workflow_run_id = UUID(workflow_run_id)
        if isinstance(skill_id, str):
            skill_id = UUID(skill_id)

        # Compute hashes for integrity verification
        input_hash = self._compute_hash(inputs)
        output_hash = self._compute_hash(outputs)

        # Determine completed_at
        completed_at = datetime.now(timezone.utc) if status in ('success', 'failed', 'timeout') else None

        execution = SkillExecution(
            skill_name=skill_name,
            skill_id=skill_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            agent_id=agent_id,
            inputs=inputs,
            outputs=outputs,
            status=status,
            duration_ms=duration_ms,
            error_message=error_message,
            error_type=error_type,
            input_hash=input_hash,
            output_hash=output_hash,
            audit_metadata=audit_metadata,
            completed_at=completed_at,
        )

        self.db.add(execution)
        await self.db.commit()
        await self.db.refresh(execution)

        logger.info(
            f"Logged skill execution: {skill_name} "
            f"(status={status}, tenant={tenant_id}, duration={duration_ms}ms)"
        )

        return execution

    async def get_executions(
        self,
        tenant_id: Union[str, UUID],
        *,
        skill_name: Optional[str] = None,
        user_id: Optional[Union[str, UUID]] = None,
        session_id: Optional[str] = None,
        workflow_id: Optional[Union[str, UUID]] = None,
        workflow_run_id: Optional[Union[str, UUID]] = None,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[SkillExecution]:
        """
        Query skill executions with tenant isolation.

        Args:
            tenant_id: Required tenant ID for isolation
            skill_name: Filter by skill name
            user_id: Filter by user
            session_id: Filter by session
            workflow_id: Filter by workflow
            workflow_run_id: Filter by specific workflow run
            status: Filter by status
            since: Filter by created_at >= since
            until: Filter by created_at <= until
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            List of matching SkillExecution records
        """
        # Convert string UUIDs if needed
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
        if isinstance(user_id, str):
            user_id = UUID(user_id)
        if isinstance(workflow_id, str):
            workflow_id = UUID(workflow_id)
        if isinstance(workflow_run_id, str):
            workflow_run_id = UUID(workflow_run_id)

        # Build query with tenant isolation
        conditions = [SkillExecution.tenant_id == tenant_id]

        if skill_name:
            conditions.append(SkillExecution.skill_name == skill_name)
        if user_id:
            conditions.append(SkillExecution.user_id == user_id)
        if session_id:
            conditions.append(SkillExecution.session_id == session_id)
        if workflow_id:
            conditions.append(SkillExecution.workflow_id == workflow_id)
        if workflow_run_id:
            conditions.append(SkillExecution.workflow_run_id == workflow_run_id)
        if status:
            conditions.append(SkillExecution.status == status)
        if since:
            conditions.append(SkillExecution.created_at >= since)
        if until:
            conditions.append(SkillExecution.created_at <= until)

        query = (
            select(SkillExecution)
            .where(and_(*conditions))
            .order_by(desc(SkillExecution.created_at))
            .limit(limit)
            .offset(offset)
        )

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_execution_by_id(
        self,
        execution_id: Union[str, UUID],
        tenant_id: Union[str, UUID],
    ) -> Optional[SkillExecution]:
        """
        Get a single execution by ID with tenant isolation.

        Args:
            execution_id: The execution ID
            tenant_id: Required tenant ID for isolation

        Returns:
            SkillExecution if found and belongs to tenant, None otherwise
        """
        if isinstance(execution_id, str):
            execution_id = UUID(execution_id)
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)

        query = select(SkillExecution).where(
            and_(
                SkillExecution.id == execution_id,
                SkillExecution.tenant_id == tenant_id,
            )
        )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def verify_integrity(
        self,
        execution_id: Union[str, UUID],
        tenant_id: Union[str, UUID],
    ) -> Dict[str, Any]:
        """
        Verify the integrity of a skill execution record.

        Recomputes hashes and compares with stored values.

        Args:
            execution_id: The execution ID to verify
            tenant_id: Required tenant ID for isolation

        Returns:
            Dict with verification results
        """
        execution = await self.get_execution_by_id(execution_id, tenant_id)

        if not execution:
            return {
                "verified": False,
                "error": "Execution not found",
            }

        # Recompute hashes
        current_input_hash = self._compute_hash(execution.inputs)
        current_output_hash = self._compute_hash(execution.outputs)

        input_valid = (execution.input_hash == current_input_hash) if execution.input_hash else True
        output_valid = (execution.output_hash == current_output_hash) if execution.output_hash else True

        return {
            "verified": input_valid and output_valid,
            "execution_id": str(execution.id),
            "skill_name": execution.skill_name,
            "input_integrity": input_valid,
            "output_integrity": output_valid,
            "stored_input_hash": execution.input_hash,
            "computed_input_hash": current_input_hash,
            "stored_output_hash": execution.output_hash,
            "computed_output_hash": current_output_hash,
        }

    async def get_execution_stats(
        self,
        tenant_id: Union[str, UUID],
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get execution statistics for a tenant.

        Args:
            tenant_id: Required tenant ID for isolation
            since: Start of time range
            until: End of time range

        Returns:
            Dict with execution statistics
        """
        from sqlalchemy import func

        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)

        conditions = [SkillExecution.tenant_id == tenant_id]
        if since:
            conditions.append(SkillExecution.created_at >= since)
        if until:
            conditions.append(SkillExecution.created_at <= until)

        # Total count
        total_query = select(func.count(SkillExecution.id)).where(and_(*conditions))
        total_result = await self.db.execute(total_query)
        total_count = total_result.scalar() or 0

        # Success count
        success_conditions = conditions + [SkillExecution.status == 'success']
        success_query = select(func.count(SkillExecution.id)).where(and_(*success_conditions))
        success_result = await self.db.execute(success_query)
        success_count = success_result.scalar() or 0

        # Failed count
        failed_conditions = conditions + [SkillExecution.status == 'failed']
        failed_query = select(func.count(SkillExecution.id)).where(and_(*failed_conditions))
        failed_result = await self.db.execute(failed_query)
        failed_count = failed_result.scalar() or 0

        # Average duration
        avg_query = select(func.avg(SkillExecution.duration_ms)).where(and_(*conditions))
        avg_result = await self.db.execute(avg_query)
        avg_duration = avg_result.scalar()

        return {
            "tenant_id": str(tenant_id),
            "total_executions": total_count,
            "successful": success_count,
            "failed": failed_count,
            "success_rate": (success_count / total_count * 100) if total_count > 0 else 0.0,
            "average_duration_ms": float(avg_duration) if avg_duration else None,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        }
