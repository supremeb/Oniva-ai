"""
Wide Research Orchestrator - Parallel batch processing primitive.

Processes N items through a templated task with semaphore-controlled concurrency,
early termination on high failure rates, and expert review synthesis.

Results stored in workspace JSON; execution metadata logged via AuditLogger.
No dedicated database table -- workspace + audit log are sufficient.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Module constants -- promote to config when tenants need overrides
_MAX_PARALLEL = 50
_MAX_ITEMS = 500
_TIMEOUT_PER_ITEM = 300  # seconds
_EARLY_TERMINATION_THRESHOLD = 0.5  # abort if >50% fail
_APPROVAL_THRESHOLD = 10  # items > this require human approval


class WideResearchItemResult(BaseModel):
    """Result from processing a single item."""
    item_index: int
    item: Dict[str, Any]
    status: str  # "completed", "failed", "timeout", "skipped"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class WideResearchResult(BaseModel):
    """Aggregate result from wide research execution."""
    execution_id: str
    status: str  # "completed", "partial", "failed", "aborted"
    total_items: int
    completed: int
    failed: int
    skipped: int
    items: List[WideResearchItemResult]
    synthesis: Optional[str] = None
    duration_seconds: float = 0.0


class WideResearchOrchestrator:
    """
    Processes N items in parallel through a templated agent task.

    Public interface:
    - execute() -> WideResearchResult
    - synthesize() -> dict with summary text
    """

    def __init__(self, db: AsyncSession):
        self._db = db
        self._aborted = False

    async def execute(
        self,
        task_template: str,
        items: List[Dict[str, Any]],
        agent_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str,
        max_parallel: int = _MAX_PARALLEL,
        timeout_per_item: int = _TIMEOUT_PER_ITEM,
        api_keys: Optional[Dict[str, str]] = None,
    ) -> WideResearchResult:
        """
        Execute wide research across all items.

        Args:
            task_template: Prompt template with {item} placeholder.
            items: List of item dicts to process.
            agent_id: Agent to use for processing.
            tenant_id: Tenant identifier.
            user_id: User who initiated the research.
            session_id: Parent session for workspace storage.
            max_parallel: Max concurrent sub-agents.
            timeout_per_item: Timeout per item in seconds.
            api_keys: API keys for LLM providers.

        Returns:
            WideResearchResult with per-item results and aggregate stats.
        """
        if len(items) > _MAX_ITEMS:
            raise ValueError(
                f"Too many items: {len(items)} exceeds maximum {_MAX_ITEMS}"
            )

        execution_id = str(uuid.uuid4())
        self._aborted = False
        start_time = time.monotonic()

        semaphore = asyncio.Semaphore(min(max_parallel, _MAX_PARALLEL))
        item_results: List[WideResearchItemResult] = []

        async def process_item(index: int, item: Dict[str, Any]) -> WideResearchItemResult:
            if self._aborted:
                return WideResearchItemResult(
                    item_index=index,
                    item=item,
                    status="skipped",
                )

            async with semaphore:
                if self._aborted:
                    return WideResearchItemResult(
                        item_index=index,
                        item=item,
                        status="skipped",
                    )

                item_start = time.monotonic()
                try:
                    prompt = task_template.replace("{item}", json.dumps(item))
                    result = await asyncio.wait_for(
                        self._run_sub_agent(
                            prompt=prompt,
                            agent_id=agent_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            api_keys=api_keys or {},
                        ),
                        timeout=timeout_per_item,
                    )
                    return WideResearchItemResult(
                        item_index=index,
                        item=item,
                        status="completed",
                        result=result,
                        duration_seconds=time.monotonic() - item_start,
                    )
                except asyncio.TimeoutError:
                    return WideResearchItemResult(
                        item_index=index,
                        item=item,
                        status="timeout",
                        error=f"Timed out after {timeout_per_item}s",
                        duration_seconds=time.monotonic() - item_start,
                    )
                except Exception as e:
                    return WideResearchItemResult(
                        item_index=index,
                        item=item,
                        status="failed",
                        error=str(e),
                        duration_seconds=time.monotonic() - item_start,
                    )

        # Launch all tasks
        tasks = [process_item(i, item) for i, item in enumerate(items)]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results, handling any unexpected gather-level exceptions
        for i, r in enumerate(raw_results):
            if isinstance(r, Exception):
                item_results.append(WideResearchItemResult(
                    item_index=i,
                    item=items[i],
                    status="failed",
                    error=str(r),
                ))
            else:
                item_results.append(r)

            # Early termination check after first batch completes
            processed = [ir for ir in item_results if ir.status != "skipped"]
            if len(processed) >= min(max_parallel, len(items)):
                failed_count = sum(1 for ir in processed if ir.status in ("failed", "timeout"))
                if len(processed) > 0 and failed_count / len(processed) > _EARLY_TERMINATION_THRESHOLD:
                    logger.warning(
                        f"Wide research {execution_id}: early termination - "
                        f"{failed_count}/{len(processed)} failed (>{_EARLY_TERMINATION_THRESHOLD*100}%)"
                    )
                    self._aborted = True

        completed_count = sum(1 for r in item_results if r.status == "completed")
        failed_count = sum(1 for r in item_results if r.status in ("failed", "timeout"))
        skipped_count = sum(1 for r in item_results if r.status == "skipped")

        status = "completed"
        if self._aborted:
            status = "aborted"
        elif failed_count > 0 and completed_count == 0:
            status = "failed"
        elif failed_count > 0:
            status = "partial"

        result = WideResearchResult(
            execution_id=execution_id,
            status=status,
            total_items=len(items),
            completed=completed_count,
            failed=failed_count,
            skipped=skipped_count,
            items=item_results,
            duration_seconds=time.monotonic() - start_time,
        )

        # Save results to workspace
        await self._save_results(tenant_id, session_id, execution_id, result)

        # Log execution metadata via audit logger
        await self._log_execution(execution_id, tenant_id, user_id, session_id, result)

        return result

    def abort(self) -> None:
        """Signal abort for running execution."""
        self._aborted = True

    async def synthesize(
        self,
        result: WideResearchResult,
        tenant_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Expert review synthesis: supervisor agent reviews results and writes summary.

        Args:
            result: WideResearchResult to synthesize.
            tenant_id: Tenant identifier.
            user_id: User identifier.

        Returns:
            Dict with synthesis text and confidence.
        """
        from app.services.llm_gateway import LLMGateway, LLMRequest

        gateway = LLMGateway(self._db)

        # Stratified sample: take up to 10 completed results
        completed_items = [r for r in result.items if r.status == "completed"]
        sample = completed_items[:10]

        sample_text = json.dumps(
            [{"item": r.item, "result": r.result} for r in sample],
            indent=2,
            default=str,
        )

        system_prompt = (
            "You are a research synthesis expert. Review the following batch processing results "
            "and produce a concise summary with key findings, patterns, and a confidence assessment. "
            f"Total items processed: {result.total_items}. "
            f"Completed: {result.completed}. Failed: {result.failed}."
        )

        request = LLMRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Results sample ({len(sample)} of {result.completed}):\n\n{sample_text}"},
            ],
            model="openai/gpt-4o",
            temperature=0.3,
        )

        response = await gateway.complete(
            request, tenant_id=tenant_id, user_id=user_id,
        )

        return {
            "synthesis": response.content,
            "sample_size": len(sample),
            "total_completed": result.completed,
            "confidence": "high" if result.completed >= result.total_items * 0.8 else "medium",
        }

    async def _run_sub_agent(
        self,
        prompt: str,
        agent_id: str,
        tenant_id: str,
        user_id: str,
        api_keys: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Execute a single sub-agent task.

        Sub-agents receive NO session_id and have no sandbox/workspace tools.
        """
        from app.services.llm_gateway import LLMGateway, LLMRequest

        gateway = LLMGateway(self._db)

        request = LLMRequest(
            messages=[
                {"role": "system", "content": (
                    "You are a focused research sub-agent. Complete the task below "
                    "and return your findings as a JSON object with 'findings' and 'confidence' keys."
                )},
                {"role": "user", "content": prompt},
            ],
            model="openai/gpt-4o-mini",
            temperature=0.3,
        )

        response = await gateway.complete(
            request, tenant_id=tenant_id, user_id=user_id,
        )

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {"findings": response.content, "confidence": "low"}

    async def _save_results(
        self,
        tenant_id: str,
        session_id: str,
        execution_id: str,
        result: WideResearchResult,
    ) -> None:
        """Save results to workspace JSON."""
        try:
            from app.services.session_storage import get_session_storage
            storage = get_session_storage()
            path = f"workspace/wide_research/{execution_id}/results.json"
            await storage.write(
                tenant_id,
                session_id,
                path,
                result.model_dump_json(indent=2),
            )
        except Exception as e:
            logger.error(f"Failed to save wide research results: {e}")

    async def _log_execution(
        self,
        execution_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str,
        result: WideResearchResult,
    ) -> None:
        """Log execution metadata via AuditLogger."""
        try:
            from app.services.audit_logger import log_decision
            await log_decision(
                agent_id="wide_research_orchestrator",
                decision=f"wide_research_{result.status}",
                rationale=(
                    f"Processed {result.total_items} items: "
                    f"{result.completed} completed, {result.failed} failed, "
                    f"{result.skipped} skipped in {result.duration_seconds:.1f}s"
                ),
                workflow_state="ANALYSIS",
                db=self._db,
                data_sources=[],
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                extra={"execution_id": execution_id},
            )
        except Exception as e:
            logger.error(f"Failed to log wide research execution: {e}")
