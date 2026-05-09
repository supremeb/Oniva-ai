"""Output gating for background agent results.

Evaluates background agent output using the existing AgentEvaluatorService
(LLM-as-judge) before delivering results. Failed evaluations are routed
to the review queue for human approval.

Wire this at the end of background agent execution:
    await evaluate_and_deliver(job, agent, result_text, db)
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.webhook_delivery import deliver_webhook

logger = logging.getLogger(__name__)

# Universal quality criteria (from harness brainstorm §3)
DEFAULT_EVALUATION_CRITERIA = """Evaluate the agent's output against these criteria:
1. DEPTH — Is the analysis thorough? Are sources/evidence cited?
2. ACCURACY — Are claims factually correct? No hallucinated data?
3. COMPLETENESS — Does it cover the full scope requested?
4. ACTIONABILITY — Can the stakeholder act on this output?

Score 0-100. Pass threshold: 80."""


async def evaluate_and_deliver(
    job_id: UUID,
    agent_id: UUID,
    agent_name: str,
    result_text: str,
    input_prompt: str,
    background_config: dict,
    db: AsyncSession,
    tenant_id: UUID,
) -> dict:
    """Evaluate background agent output and deliver if passing.

    Args:
        job_id: BackgroundJob UUID
        agent_id: CustomAgent UUID
        agent_name: Agent display name
        result_text: The agent's output text
        input_prompt: The original input/prompt
        background_config: Agent's background_config JSONB
        db: Database session
        tenant_id: Tenant UUID

    Returns:
        Dict with evaluation result: {"passed": bool, "score": int, "action": str}
    """
    evaluation_enabled = background_config.get("evaluation_enabled", False)
    evaluation_threshold = background_config.get("evaluation_threshold", 80)

    if not evaluation_enabled:
        # No evaluation — deliver directly
        await _deliver_results(agent_name, result_text, background_config)
        return {"passed": True, "score": None, "action": "delivered_without_evaluation"}

    # Evaluate using existing AgentEvaluatorService
    try:
        from app.services.agent_evaluator import AgentEvaluatorService
        evaluator = AgentEvaluatorService(db)

        score_result = await evaluator.evaluate_with_llm_judge(
            user_input=input_prompt,
            actual_response=result_text,
            expected_response=DEFAULT_EVALUATION_CRITERIA,
            is_positive=True,
            agent_id=str(agent_id),
        )

        score = score_result.get("score", 0)
        passed = score >= evaluation_threshold

        logger.info(
            f"Background agent {agent_name} evaluation: score={score}, "
            f"threshold={evaluation_threshold}, passed={passed}"
        )

        if passed:
            await _deliver_results(agent_name, result_text, background_config)
            return {"passed": True, "score": score, "action": "delivered"}
        else:
            # Route to review queue
            await _route_to_review(
                job_id, agent_id, agent_name, result_text,
                score, evaluation_threshold, db, tenant_id,
            )
            return {"passed": False, "score": score, "action": "routed_to_review"}

    except Exception as e:
        logger.error(f"Evaluation failed for agent {agent_name}: {e}")
        # On evaluation failure, deliver anyway with warning
        await _deliver_results(agent_name, result_text, background_config)
        return {"passed": True, "score": None, "action": "delivered_evaluation_error"}


async def _deliver_results(agent_name: str, result_text: str, background_config: dict):
    """Deliver agent results via configured channels."""
    webhook_url = background_config.get("webhook_url")
    if webhook_url:
        webhook_headers = background_config.get("webhook_headers", {})
        await deliver_webhook(
            url=webhook_url,
            payload={
                "agent_name": agent_name,
                "result": result_text,
                "status": "completed",
            },
            headers=webhook_headers,
        )


async def _route_to_review(
    job_id: UUID,
    agent_id: UUID,
    agent_name: str,
    result_text: str,
    score: int,
    threshold: int,
    db: AsyncSession,
    tenant_id: UUID,
):
    """Route failed evaluation to the existing review queue."""
    try:
        from app.services.review_queue import ReviewQueueService
        review_service = ReviewQueueService(db)

        await review_service.create_review_item(
            tenant_id=tenant_id,
            entity_type="background_agent_output",
            entity_id=str(job_id),
            title=f"{agent_name}: Output below quality threshold",
            description=(
                f"Evaluator scored this output {score}/100 "
                f"(threshold: {threshold}). Review and approve or reject."
            ),
            data={
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "result_preview": result_text[:500],
                "score": score,
                "threshold": threshold,
            },
            risk_level="medium",
        )
        logger.info(f"Background agent {agent_name} output routed to review queue (score={score})")
    except Exception as e:
        logger.error(f"Failed to route to review queue: {e}")
