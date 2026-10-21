"""Agent Evaluation API endpoints for Agent Studio.

Manages evaluation runs and accuracy metrics.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional, List
from uuid import UUID
import json
import asyncio

from app.models.agent_test_case import AgentTestCase
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.agent_version import AgentVersion
from app.core.database import get_db, get_db_streaming
from app.auth.dependencies import get_current_tenant_id
from app.api.dependencies import verify_agent_access, AgentInfo, parse_agent_id
from app.api.dependencies.quota import require_quota
from app.models.usage_quota import QuotaType
from app.services.agent_evaluator import AgentEvaluator
from app.schemas.agent_studio import (
    EvaluationRequest,
    AccuracySummary,
    AccuracyHistoryEntry,
    TestRunResult,
    EvaluationResult,
    CategoryBreakdown,
)

router = APIRouter()


@router.post("/agents/{agent_id}/evaluate")
async def run_evaluation(
    agent_id: str,
    request: EvaluationRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
    # Streaming route: the evaluation generator keeps using this session.
    db: AsyncSession = Depends(get_db_streaming),
    tenant_id: UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Run evaluation against test cases.

    Returns a streaming response with progress updates.

    Note: Only supported for custom agents.
    """
    # Evaluation only works for custom agents, not built-in ones
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evaluation is only available for custom agents"
        )
    agent = agent_info.custom_agent
    agent_uuid, _ = parse_agent_id(agent_id)

    # Check if there are test cases
    count_result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.agent_id == agent_uuid,
            AgentTestCase.is_active.is_(True)
        ).limit(1)
    )
    if not count_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No test cases found for evaluation"
        )

    async def generate_evaluation():
        """Generate streaming evaluation response."""
        evaluator = AgentEvaluator(db, tenant_id=tenant_id)

        # Use a queue for progress updates
        progress_queue: asyncio.Queue = asyncio.Queue()
        evaluation_complete = asyncio.Event()
        evaluation_result = {"results": None, "accuracy_entry": None, "cost_summary": None, "error": None}

        async def progress_callback(progress: dict):
            """Put progress updates into queue for streaming."""
            await progress_queue.put(progress)

        async def run_evaluation():
            """Run evaluation in background and put results in shared dict."""
            try:
                results, accuracy_entry, cost_summary = await evaluator.evaluate_all(
                    agent_id=agent_uuid,
                    tenant_id=tenant_id,
                    version_id=request.version_id,
                    baseline_only=request.baseline_only,
                    test_case_ids=request.test_case_ids,
                    progress_callback=progress_callback,
                )
                evaluation_result["results"] = results
                evaluation_result["accuracy_entry"] = accuracy_entry
                evaluation_result["cost_summary"] = cost_summary
            except Exception as e:
                evaluation_result["error"] = str(e)
            finally:
                evaluation_complete.set()

        # Start evaluation in background task
        eval_task = asyncio.create_task(run_evaluation())

        try:
            # Stream progress updates while evaluation runs
            while not evaluation_complete.is_set():
                try:
                    progress = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                    yield f"data: {json.dumps({'type': 'progress', 'data': progress})}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining progress updates
            while not progress_queue.empty():
                progress = progress_queue.get_nowait()
                yield f"data: {json.dumps({'type': 'progress', 'data': progress})}\n\n"

            # Check for errors
            if evaluation_result["error"]:
                error_msg = evaluation_result["error"]
                yield f"data: {json.dumps({'type': 'error', 'message': f'Evaluation failed: {error_msg}'})}\n\n"
                return

            results = evaluation_result["results"]
            accuracy_entry = evaluation_result["accuracy_entry"]

            # Build category breakdown
            category_breakdown = {}
            if accuracy_entry.category_breakdown:
                for cat, data in accuracy_entry.category_breakdown.items():
                    category_breakdown[cat] = CategoryBreakdown(
                        accuracy=data.get('accuracy', 0.0),
                        passed=data.get('passed', 0),
                        failed=data.get('failed', 0),
                        total=data.get('total', 0),
                    ).model_dump()

            # Build test results
            test_results = [
                TestRunResult(
                    test_case_id=r.test_case_id,
                    passed=r.passed,
                    actual_output=r.actual_output,
                    expected_output=r.expected_output,
                    score=r.score,
                    evaluation_type=r.evaluation_type,
                    duration_ms=r.duration_ms,
                    tokens_used=r.tokens_used,
                ).model_dump()
                for r in results
            ]

            # Final result
            final_result = EvaluationResult(
                evaluation_run_id=accuracy_entry.evaluation_run_id,
                agent_id=agent_uuid,
                version_id=request.version_id,
                accuracy=accuracy_entry.accuracy,
                total_tests=accuracy_entry.total_tests,
                passed_tests=accuracy_entry.passed_tests,
                failed_tests=accuracy_entry.failed_tests,
                duration_ms=accuracy_entry.duration_ms,
                category_breakdown=category_breakdown,
                test_results=test_results,
                evaluated_at=accuracy_entry.evaluated_at,
            )

            yield f"data: {json.dumps({'type': 'result', 'data': final_result.model_dump(mode='json')})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Evaluation failed: {str(e)}'})}\n\n"
        finally:
            # Ensure the task is cleaned up
            if not eval_task.done():
                eval_task.cancel()
                try:
                    await eval_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        generate_evaluation(),
        media_type="text/event-stream"
    )


@router.get("/agents/{agent_id}/accuracy", response_model=AccuracySummary)
async def get_accuracy_summary(
    agent_id: str,
    version_id: Optional[UUID] = None,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get current accuracy metrics for an agent.

    Note: Built-in agents return empty metrics as they use predefined behavior.
    """
    # Built-in agents don't have stored accuracy metrics
    if agent_info.is_builtin:
        return AccuracySummary(
            agent_id=UUID('00000000-0000-0000-0000-000000000000'),  # Placeholder
            version_id=version_id,
            accuracy=0.0,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            category_breakdown={},
            last_evaluated_at=None,
        )

    agent_uuid, _ = parse_agent_id(agent_id)

    # Get latest accuracy entry
    query = select(AgentAccuracyHistory).where(
        AgentAccuracyHistory.agent_id == agent_uuid
    )
    if version_id:
        query = query.where(AgentAccuracyHistory.agent_version_id == version_id)

    query = query.order_by(desc(AgentAccuracyHistory.evaluated_at)).limit(1)

    result = await db.execute(query)
    accuracy_entry = result.scalar_one_or_none()

    if not accuracy_entry:
        # Return empty metrics if no evaluation has been run
        return AccuracySummary(
            agent_id=agent_uuid,
            version_id=version_id,
            accuracy=0.0,
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            category_breakdown={},
            last_evaluated_at=None,
        )

    # Build category breakdown (consistent with run_evaluation which uses model_dump())
    category_breakdown = {}
    if accuracy_entry.category_breakdown:
        for cat, data in accuracy_entry.category_breakdown.items():
            category_breakdown[cat] = CategoryBreakdown(
                accuracy=data.get('accuracy', 0.0),
                passed=data.get('passed', 0),
                failed=data.get('failed', 0),
                total=data.get('total', 0),
            ).model_dump()

    return AccuracySummary(
        agent_id=agent_uuid,
        version_id=accuracy_entry.agent_version_id,
        accuracy=accuracy_entry.accuracy,
        total_tests=accuracy_entry.total_tests,
        passed_tests=accuracy_entry.passed_tests,
        failed_tests=accuracy_entry.failed_tests,
        category_breakdown=category_breakdown,
        last_evaluated_at=accuracy_entry.evaluated_at,
    )


@router.get("/agents/{agent_id}/accuracy/history", response_model=List[AccuracyHistoryEntry])
async def get_accuracy_history(
    agent_id: str,
    limit: int = Query(30, ge=1, le=100),
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get accuracy trend over time.

    Note: Built-in agents return empty history as they use predefined behavior.
    """
    # Built-in agents don't have stored accuracy history
    if agent_info.is_builtin:
        return []

    agent_uuid, _ = parse_agent_id(agent_id)

    # Get history entries
    result = await db.execute(
        select(AgentAccuracyHistory)
        .where(AgentAccuracyHistory.agent_id == agent_uuid)
        .order_by(desc(AgentAccuracyHistory.evaluated_at))
        .limit(limit)
    )
    entries = result.scalars().all()

    # Get version info for entries with version_id
    version_map = {}
    version_ids = [e.agent_version_id for e in entries if e.agent_version_id]
    if version_ids:
        versions_result = await db.execute(
            select(AgentVersion).where(AgentVersion.id.in_(version_ids))
        )
        versions = versions_result.scalars().all()
        version_map = {v.id: v.version for v in versions}

    return [
        AccuracyHistoryEntry(
            id=e.id,
            version_id=e.agent_version_id,
            version=version_map.get(e.agent_version_id) if e.agent_version_id else None,
            accuracy=e.accuracy,
            total_tests=e.total_tests,
            passed_tests=e.passed_tests,
            failed_tests=e.failed_tests,
            accuracy_delta=e.accuracy_delta,
            evaluated_at=e.evaluated_at,
        )
        for e in entries
    ]


@router.post("/agents/{agent_id}/test-cases/{test_case_id}/run", response_model=TestRunResult)
async def run_single_test(
    agent_id: str,
    test_case_id: UUID,
    agent_info: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Run a single test case and return the result.

    Note: Only supported for custom agents.
    """
    # Evaluation only works for custom agents, not built-in ones
    if agent_info.is_builtin or agent_info.custom_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test execution is only available for custom agents"
        )
    agent = agent_info.custom_agent
    agent_uuid, _ = parse_agent_id(agent_id)

    # Get test case
    test_case_result = await db.execute(
        select(AgentTestCase).where(
            AgentTestCase.id == test_case_id,
            AgentTestCase.agent_id == agent_uuid
        )
    )
    test_case = test_case_result.scalar_one_or_none()
    if not test_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test case not found"
        )

    # Run evaluation
    evaluator = AgentEvaluator(db, tenant_id=tenant_id)
    result = await evaluator.evaluate_test_case(test_case, agent)

    await db.commit()

    return TestRunResult(
        test_case_id=result.test_case_id,
        passed=result.passed,
        actual_output=result.actual_output,
        expected_output=result.expected_output,
        score=result.score,
        evaluation_type=result.evaluation_type,
        duration_ms=result.duration_ms,
        tokens_used=result.tokens_used,
    )
