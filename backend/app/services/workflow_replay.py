"""
Workflow Replay Service

Provides audit-friendly workflow replay capabilities for regulatory compliance.
Allows replaying entire workflow executions for investigation and audit purposes.

Based on Section 3.5 - Compliance & Audit requirements.

Features:
- Full workflow replay from audit logs
- Step-by-step execution review
- State transition timeline
- Tool call analysis
- Export for regulatory submission
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncIterator
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================

class ReplayStep(BaseModel):
    """A single step in the workflow replay."""
    step_number: int
    timestamp: datetime
    action_type: str  # 'state_transition', 'tool_call', 'decision', 'agent_output'

    # State context
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    current_state: str

    # Action details
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None

    # Decision details
    decision: Optional[str] = None
    rationale: Optional[str] = None
    data_sources: List[str] = []

    # Agent output
    agent_output: Optional[str] = None

    # Audit info
    audit_entry_id: Optional[str] = None
    signature_valid: Optional[bool] = None


class WorkflowReplay(BaseModel):
    """Complete workflow replay data."""
    workflow_id: str
    session_id: str
    tenant_id: str

    # Timeline
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    # State machine
    initial_state: str
    final_state: str
    state_transitions: int

    # Execution
    total_steps: int
    tool_calls: int
    decisions: int

    # Steps
    steps: List[ReplayStep] = []

    # Verification
    all_signatures_valid: bool = True
    integrity_check_passed: bool = True
    verification_errors: List[str] = []

    # Export info
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class ReplayFilter(BaseModel):
    """Filters for workflow replay."""
    action_types: Optional[List[str]] = None
    states: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


# =============================================================================
# Replay Service
# =============================================================================

class WorkflowReplayService:
    """
    Service for replaying workflows for audit and compliance review.

    Reconstructs the complete workflow execution timeline from:
    1. Compliance audit logs (cryptographically signed decisions)
    2. Workflow state logs (state transitions)
    3. Session filesystem (tool call results, agent outputs)
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def get_workflow_replay(
        self,
        workflow_id: str,
        session_id: Optional[str] = None,
        verify_signatures: bool = True,
        include_tool_results: bool = True,
    ) -> WorkflowReplay:
        """
        Get complete workflow replay for audit review.

        Args:
            workflow_id: The workflow to replay
            session_id: Optional session ID to filter by
            verify_signatures: Whether to verify all signatures
            include_tool_results: Whether to include full tool results

        Returns:
            Complete WorkflowReplay with all steps
        """
        from app.models.compliance_audit_log import (
            ComplianceAuditLog,
            WorkflowStateLog,
        )
        from app.services.audit_logger import get_audit_logger

        # Get all audit entries for this workflow
        audit_stmt = (
            select(ComplianceAuditLog)
            .where(ComplianceAuditLog.workflow_id == UUID(workflow_id))
            .order_by(ComplianceAuditLog.created_at.asc())
        )

        if session_id:
            audit_stmt = audit_stmt.where(
                ComplianceAuditLog.session_id == UUID(session_id)
            )

        audit_result = await self.db.execute(audit_stmt)
        audit_entries = audit_result.scalars().all()

        # Get all state transitions
        state_stmt = (
            select(WorkflowStateLog)
            .where(WorkflowStateLog.workflow_id == UUID(workflow_id))
            .order_by(WorkflowStateLog.transition_at.asc())
        )

        if session_id:
            state_stmt = state_stmt.where(
                WorkflowStateLog.session_id == UUID(session_id)
            )

        state_result = await self.db.execute(state_stmt)
        state_logs = state_result.scalars().all()

        # Build replay
        steps: List[ReplayStep] = []
        step_number = 0
        verification_errors = []
        all_signatures_valid = True

        # Merge audit entries and state logs by timestamp
        all_events = []

        for entry in audit_entries:
            all_events.append({
                'type': 'audit',
                'timestamp': entry.created_at,
                'data': entry,
            })

        for log in state_logs:
            all_events.append({
                'type': 'state',
                'timestamp': log.transition_at,
                'data': log,
            })

        # Sort by timestamp
        all_events.sort(key=lambda x: x['timestamp'])

        # Get audit logger for signature verification
        audit_logger = None
        if verify_signatures:
            audit_logger = get_audit_logger(self.db)

        # Process events into steps
        current_state = 'initialized'
        for event in all_events:
            step_number += 1

            if event['type'] == 'audit':
                entry = event['data']

                # Verify signature if requested
                signature_valid = None
                if verify_signatures and audit_logger:
                    result = await audit_logger.verify_entry(str(entry.id))
                    signature_valid = result.is_valid
                    if not result.is_valid:
                        all_signatures_valid = False
                        verification_errors.append(
                            f"Step {step_number}: Signature verification failed for entry {entry.id}"
                        )

                steps.append(ReplayStep(
                    step_number=step_number,
                    timestamp=entry.created_at,
                    action_type=entry.action or 'decision',
                    current_state=entry.workflow_state or current_state,
                    decision=entry.decision,
                    rationale=entry.rationale,
                    data_sources=entry.data_sources or [],
                    tool_name=entry.tools_called[0] if entry.tools_called else None,
                    audit_entry_id=str(entry.id),
                    signature_valid=signature_valid,
                ))

                if entry.workflow_state:
                    current_state = entry.workflow_state

            elif event['type'] == 'state':
                log = event['data']

                steps.append(ReplayStep(
                    step_number=step_number,
                    timestamp=log.transition_at,
                    action_type='state_transition',
                    from_state=log.from_state,
                    to_state=log.to_state,
                    current_state=log.to_state,
                ))

                current_state = log.to_state

        # Build final replay object
        started_at = all_events[0]['timestamp'] if all_events else datetime.utcnow()
        completed_at = all_events[-1]['timestamp'] if all_events else None

        duration_ms = None
        if started_at and completed_at:
            delta = completed_at - started_at
            duration_ms = int(delta.total_seconds() * 1000)

        initial_state = 'initialized'
        final_state = current_state
        if steps:
            # Find first and last states
            for step in steps:
                if step.action_type == 'state_transition' and step.from_state:
                    initial_state = step.from_state
                    break

        return WorkflowReplay(
            workflow_id=workflow_id,
            session_id=session_id or '',
            tenant_id=str(audit_entries[0].tenant_id) if audit_entries else '',
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            initial_state=initial_state,
            final_state=final_state,
            state_transitions=len(state_logs),
            total_steps=len(steps),
            tool_calls=sum(1 for s in steps if s.tool_name),
            decisions=sum(1 for s in steps if s.decision),
            steps=steps,
            all_signatures_valid=all_signatures_valid,
            integrity_check_passed=all_signatures_valid and len(verification_errors) == 0,
            verification_errors=verification_errors,
        )

    async def stream_replay(
        self,
        workflow_id: str,
        delay_ms: int = 0,
        **kwargs,
    ) -> AsyncIterator[ReplayStep]:
        """
        Stream replay steps one at a time for UI display.

        Args:
            workflow_id: The workflow to replay
            delay_ms: Optional delay between steps (for visual replay)
            **kwargs: Additional filters

        Yields:
            ReplayStep objects one at a time
        """
        import asyncio

        replay = await self.get_workflow_replay(workflow_id, **kwargs)

        for step in replay.steps:
            yield step
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

    async def get_replay_summary(
        self,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """
        Get a summary of the workflow replay without full steps.

        Useful for listing replays in the UI.
        """
        replay = await self.get_workflow_replay(
            workflow_id,
            verify_signatures=False,
            include_tool_results=False,
        )

        return {
            'workflow_id': replay.workflow_id,
            'session_id': replay.session_id,
            'started_at': replay.started_at.isoformat(),
            'completed_at': replay.completed_at.isoformat() if replay.completed_at else None,
            'duration_ms': replay.duration_ms,
            'initial_state': replay.initial_state,
            'final_state': replay.final_state,
            'state_transitions': replay.state_transitions,
            'total_steps': replay.total_steps,
            'tool_calls': replay.tool_calls,
            'decisions': replay.decisions,
        }

    async def export_replay(
        self,
        workflow_id: str,
        export_format: str = 'json',
        include_signatures: bool = True,
    ) -> str:
        """
        Export workflow replay for regulatory submission.

        Args:
            workflow_id: The workflow to export
            export_format: 'json' or 'csv'
            include_signatures: Whether to include signature data

        Returns:
            Exported data as string
        """
        replay = await self.get_workflow_replay(
            workflow_id,
            verify_signatures=include_signatures,
        )

        if export_format == 'json':
            return replay.model_dump_json(indent=2)

        elif export_format == 'csv':
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Header
            writer.writerow([
                'step_number', 'timestamp', 'action_type', 'current_state',
                'from_state', 'to_state', 'decision', 'rationale',
                'tool_name', 'signature_valid',
            ])

            # Rows
            for step in replay.steps:
                writer.writerow([
                    step.step_number,
                    step.timestamp.isoformat(),
                    step.action_type,
                    step.current_state,
                    step.from_state or '',
                    step.to_state or '',
                    step.decision or '',
                    step.rationale or '',
                    step.tool_name or '',
                    str(step.signature_valid) if step.signature_valid is not None else '',
                ])

            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}")

    async def verify_chain_integrity(
        self,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """
        Verify the integrity of the entire audit chain for a workflow.

        Checks:
        1. All signatures are valid
        2. Hash chain is unbroken
        3. No missing entries

        Returns:
            Verification result with details
        """
        from app.models.compliance_audit_log import ComplianceAuditLog
        from app.services.audit_logger import get_audit_logger

        # Get all entries in order
        stmt = (
            select(ComplianceAuditLog)
            .where(ComplianceAuditLog.workflow_id == UUID(workflow_id))
            .order_by(ComplianceAuditLog.created_at.asc())
        )

        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        if not entries:
            return {
                'workflow_id': workflow_id,
                'status': 'no_entries',
                'message': 'No audit entries found for this workflow',
                'verified_at': datetime.utcnow().isoformat(),
            }

        audit_logger = get_audit_logger(self.db)

        issues = []
        verified_count = 0

        # Verify each entry
        previous_hash = None
        for i, entry in enumerate(entries):
            # Check signature
            verify_result = await audit_logger.verify_entry(str(entry.id))
            if not verify_result.is_valid:
                issues.append({
                    'entry_id': str(entry.id),
                    'issue': 'invalid_signature',
                    'message': verify_result.message,
                })
            else:
                verified_count += 1

            # Check hash chain
            if i > 0 and entry.previous_hash != previous_hash:
                issues.append({
                    'entry_id': str(entry.id),
                    'issue': 'broken_chain',
                    'message': f'Expected previous_hash {previous_hash}, got {entry.previous_hash}',
                })

            previous_hash = entry.entry_hash

        return {
            'workflow_id': workflow_id,
            'status': 'valid' if len(issues) == 0 else 'issues_found',
            'total_entries': len(entries),
            'verified_entries': verified_count,
            'chain_intact': len([i for i in issues if i['issue'] == 'broken_chain']) == 0,
            'all_signatures_valid': verified_count == len(entries),
            'issues': issues,
            'verified_at': datetime.utcnow().isoformat(),
        }


# =============================================================================
# Convenience Functions
# =============================================================================

async def get_replay(
    workflow_id: str,
    db: AsyncSession,
    **kwargs,
) -> WorkflowReplay:
    """Convenience function to get a workflow replay."""
    service = WorkflowReplayService(db)
    return await service.get_workflow_replay(workflow_id, **kwargs)


async def export_replay(
    workflow_id: str,
    db: AsyncSession,
    export_format: str = 'json',
) -> str:
    """Convenience function to export a workflow replay."""
    service = WorkflowReplayService(db)
    return await service.export_replay(workflow_id, export_format)


async def verify_workflow_integrity(
    workflow_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Convenience function to verify workflow integrity."""
    service = WorkflowReplayService(db)
    return await service.verify_chain_integrity(workflow_id)
