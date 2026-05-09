"""Agent Deploy API endpoints for Agent Studio deployment gates.

Manages version deployments with quality gates.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from uuid import UUID
from datetime import datetime

from app.models.agent_version import AgentVersion
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.agent_test_case import AgentTestCase
from app.models.deploy_history import DeployHistory
from app.core.database import get_db
from app.auth.dependencies import get_current_tenant_id, get_current_user
from app.auth.providers.base import AuthUser
from app.api.dependencies import verify_agent_access, AgentInfo
from app.schemas.agent_studio import (
    DeployRequest,
    DeployResult,
    DeployGateCheck,
    DeployGateStatus,
    RollbackRequest,
    RollbackResult,
    DeployHistoryEntry,
)

router = APIRouter()

# Default gate thresholds
DEFAULT_ACCURACY_THRESHOLD = 0.8  # 80%
DEFAULT_REGRESSION_ALLOWED = 0.05  # 5%
MIN_TEST_CASES = 5


async def check_deploy_gates(
    db: AsyncSession,
    agent_id: UUID,
    version_id: UUID,
    accuracy_threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    regression_allowed: float = DEFAULT_REGRESSION_ALLOWED,
) -> DeployGateStatus:
    """
    Check all deployment gates for a version.

    Returns status with individual check results.
    """
    checks: List[DeployGateCheck] = []

    # Gate 0: Minimum test cases
    test_count_result = await db.execute(
        select(AgentTestCase)
        .where(AgentTestCase.agent_id == agent_id, AgentTestCase.is_active == True)
    )
    test_case_count = len(test_count_result.scalars().all())
    has_enough_tests = test_case_count >= MIN_TEST_CASES
    checks.append(DeployGateCheck(
        name="Minimum Test Cases",
        passed=has_enough_tests,
        value=str(test_case_count),
        threshold=str(MIN_TEST_CASES),
        message=f"{test_case_count} test cases" if has_enough_tests
        else f"Need at least {MIN_TEST_CASES} test cases (have {test_case_count})",
    ))

    # Get version info
    version_result = await db.execute(
        select(AgentVersion).where(AgentVersion.id == version_id)
    )
    version = version_result.scalar_one_or_none()
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found"
        )

    # Get current deployed version
    deployed_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_deployed == True
        )
    )
    current_deployed = deployed_result.scalar_one_or_none()

    # Get latest accuracy for this version
    accuracy_result = await db.execute(
        select(AgentAccuracyHistory)
        .where(
            AgentAccuracyHistory.agent_id == agent_id,
            AgentAccuracyHistory.agent_version_id == version_id
        )
        .order_by(desc(AgentAccuracyHistory.evaluated_at))
        .limit(1)
    )
    version_accuracy_entry = accuracy_result.scalar_one_or_none()

    # Get accuracy for current deployed version
    current_accuracy = None
    if current_deployed:
        current_accuracy_result = await db.execute(
            select(AgentAccuracyHistory)
            .where(
                AgentAccuracyHistory.agent_id == agent_id,
                AgentAccuracyHistory.agent_version_id == current_deployed.id
            )
            .order_by(desc(AgentAccuracyHistory.evaluated_at))
            .limit(1)
        )
        current_accuracy_entry = current_accuracy_result.scalar_one_or_none()
        if current_accuracy_entry:
            current_accuracy = current_accuracy_entry.accuracy

    # Calculate new accuracy
    new_accuracy = version_accuracy_entry.accuracy if version_accuracy_entry else None
    accuracy_delta = None
    if new_accuracy is not None and current_accuracy is not None:
        accuracy_delta = new_accuracy - current_accuracy

    # Gate 1: Evaluation exists
    eval_exists = version_accuracy_entry is not None
    checks.append(DeployGateCheck(
        name="Evaluation Complete",
        passed=eval_exists,
        value="Yes" if eval_exists else "No",
        threshold=None,
        message="Version has been evaluated" if eval_exists else "Run evaluation before deploying"
    ))

    # Gate 2: Accuracy threshold
    accuracy_passed = False
    if new_accuracy is not None:
        accuracy_passed = new_accuracy >= accuracy_threshold
    checks.append(DeployGateCheck(
        name="Accuracy Threshold",
        passed=accuracy_passed,
        value=f"{new_accuracy * 100:.1f}%" if new_accuracy is not None else "N/A",
        threshold=f"{accuracy_threshold * 100:.0f}%",
        message="Meets accuracy threshold" if accuracy_passed else f"Below {accuracy_threshold * 100:.0f}% threshold"
    ))

    # Gate 3: Regression check (only if there's a current deployed version)
    regression_passed = True
    if current_accuracy is not None and accuracy_delta is not None:
        regression_passed = accuracy_delta >= -regression_allowed
    checks.append(DeployGateCheck(
        name="Regression Check",
        passed=regression_passed,
        value=f"{accuracy_delta * 100:+.1f}%" if accuracy_delta is not None else "N/A",
        threshold=f"Max {regression_allowed * 100:.0f}% regression",
        message="No significant regression" if regression_passed else f"Accuracy dropped by more than {regression_allowed * 100:.0f}%"
    ))

    # Overall status
    can_deploy = all(c.passed for c in checks)

    return DeployGateStatus(
        can_deploy=can_deploy,
        checks=checks,
        current_accuracy=current_accuracy,
        new_accuracy=new_accuracy,
        accuracy_delta=accuracy_delta,
        current_version=current_deployed.version if current_deployed else None,
        new_version=version.version,
    )


@router.get("/agents/{agent_id}/deploy-gate/status", response_model=DeployGateStatus)
async def get_deploy_gate_status(
    agent_id: UUID,
    version_id: UUID,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a version can be deployed.

    Returns gate check results and recommendations.
    Deploy operations are only available for custom agents.
    """
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deploy operations are only available for custom agents"
        )
    return await check_deploy_gates(db, agent_id, version_id)


@router.post("/agents/{agent_id}/versions/{version_id}/deploy", response_model=DeployResult)
async def deploy_version(
    agent_id: UUID,
    version_id: UUID,
    request: DeployRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Deploy a version with gate checks.

    Gate checks:
    1. Evaluation has been run
    2. Accuracy >= 80% threshold
    3. Accuracy regression <= 5% from current deployed version

    If force=True, bypasses gates (logged for audit).
    Deploy operations are only available for custom agents.
    """
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deploy operations are only available for custom agents"
        )
    agent = agent_info.custom_agent
    # Get user_id from agent ownership (already validated by dependency)
    user_id = agent.user_id

    # Get version
    version_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.agent_id == agent_id
        )
    )
    version = version_result.scalar_one_or_none()
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found"
        )

    # Check gates
    gate_status = await check_deploy_gates(db, agent_id, version_id)

    # If force deploy, require admin/owner role and a reason
    if request.force and not gate_status.can_deploy:
        from app.auth.permissions import has_permission
        if not has_permission(user.role, "agents:delete"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admin or owner roles can force-deploy",
            )
        if not request.force_reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Force reason is required when bypassing gates"
            )

    # Block if gates failed and not force
    if not gate_status.can_deploy and not request.force:
        # Record blocked deploy
        block_record = DeployHistory.create_block_record(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            deployed_by=user_id,
            block_reason="Gate checks failed",
            accuracy_at_deploy=gate_status.new_accuracy,
            previous_accuracy=gate_status.current_accuracy,
        )
        db.add(block_record)

        # Update version
        version.deploy_blocked = True
        version.block_reason = "Gate checks failed"

        await db.commit()

        return DeployResult(
            success=False,
            version_id=version_id,
            version=version.version,
            action="blocked",
            gate_checks=gate_status.checks,
            accuracy_at_deploy=gate_status.new_accuracy,
            block_reason="Deployment blocked: gate checks failed",
            deployed_at=None,
        )

    # Get current deployed version to undeploy
    current_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_deployed == True
        )
    )
    current_deployed = current_result.scalar_one_or_none()
    previous_version_id = current_deployed.id if current_deployed else None

    # Undeploy current version
    if current_deployed:
        current_deployed.is_deployed = False

    # Deploy new version
    version.is_deployed = True
    version.deployed_at = datetime.utcnow()
    version.deployed_by = user_id
    version.accuracy_at_deploy = gate_status.new_accuracy
    version.deploy_blocked = False
    version.block_reason = None

    # Record deployment
    deploy_record = DeployHistory.create_deploy_record(
        tenant_id=tenant_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        deployed_by=user_id,
        gate_check_passed=gate_status.can_deploy,
        accuracy_at_deploy=gate_status.new_accuracy,
        previous_version_id=previous_version_id,
        previous_accuracy=gate_status.current_accuracy,
        notes=request.notes,
        force_deployed=request.force and not gate_status.can_deploy,
        force_reason=request.force_reason if request.force else None,
    )
    db.add(deploy_record)

    await db.commit()

    return DeployResult(
        success=True,
        version_id=version_id,
        version=version.version,
        action="deploy",
        gate_checks=gate_status.checks,
        accuracy_at_deploy=gate_status.new_accuracy,
        block_reason=None,
        deployed_at=version.deployed_at,
    )


@router.post("/agents/{agent_id}/versions/{version_id}/rollback", response_model=RollbackResult)
async def rollback_version(
    agent_id: UUID,
    version_id: UUID,
    request: RollbackRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Rollback to a previous version.
    Rollback operations are only available for custom agents.
    """
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rollback operations are only available for custom agents"
        )
    agent = agent_info.custom_agent
    # Get user_id from agent ownership (already validated by dependency)
    user_id = agent.user_id

    # Get target version
    target_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == request.target_version_id,
            AgentVersion.agent_id == agent_id
        )
    )
    target_version = target_result.scalar_one_or_none()
    if not target_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target version not found"
        )

    # Get current deployed version
    current_result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_deployed == True
        )
    )
    current_deployed = current_result.scalar_one_or_none()
    if not current_deployed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No version currently deployed"
        )

    if current_deployed.id == request.target_version_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target version is already deployed"
        )

    # Undeploy current
    current_deployed.is_deployed = False

    # Deploy target
    target_version.is_deployed = True
    target_version.deployed_at = datetime.utcnow()
    target_version.deployed_by = user_id

    # Record rollback
    rollback_record = DeployHistory.create_rollback_record(
        tenant_id=tenant_id,
        agent_id=agent_id,
        target_version_id=request.target_version_id,
        rolled_back_from_id=current_deployed.id,
        deployed_by=user_id,
        reason=request.reason,
    )
    db.add(rollback_record)

    await db.commit()

    return RollbackResult(
        success=True,
        previous_version_id=current_deployed.id,
        current_version_id=request.target_version_id,
        reason=request.reason,
        rolled_back_at=datetime.utcnow(),
    )


@router.get("/agents/{agent_id}/deploy-history", response_model=List[DeployHistoryEntry])
async def get_deploy_history(
    agent_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """
    Get deployment history for an agent.
    Deploy history is only available for custom agents.
    """
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deploy history is only available for custom agents"
        )
    # Get history
    result = await db.execute(
        select(DeployHistory)
        .where(DeployHistory.agent_id == agent_id)
        .order_by(desc(DeployHistory.created_at))
        .limit(limit)
    )
    entries = result.scalars().all()

    # Get version info
    version_ids = [e.agent_version_id for e in entries]
    versions_result = await db.execute(
        select(AgentVersion).where(AgentVersion.id.in_(version_ids))
    )
    versions = versions_result.scalars().all()
    version_map = {v.id: v.version for v in versions}

    return [
        DeployHistoryEntry(
            id=e.id,
            version_id=e.agent_version_id,
            version=version_map.get(e.agent_version_id, "Unknown"),
            action=e.action,
            status=e.status,
            gate_check_passed=e.gate_check_passed,
            accuracy_at_deploy=e.accuracy_at_deploy,
            previous_accuracy=e.previous_accuracy,
            accuracy_delta=e.accuracy_delta,
            block_reason=e.block_reason,
            deployed_by=e.deployed_by,
            notes=e.notes,
            force_deployed=e.force_deployed,
            force_reason=e.force_reason,
            created_at=e.created_at,
        )
        for e in entries
    ]
