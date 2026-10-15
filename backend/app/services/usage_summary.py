"""Per-conversation cost summary from the engine usage ledger (llm_usage_records)."""
from decimal import Decimal
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_usage import LLMUsageRecord


async def conversation_cost(db: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> Dict[str, Any]:
    """Totals and per-run breakdown. Unpriced rows add tokens but no dollars and flag the run."""
    rows = (
        await db.execute(
            select(
                LLMUsageRecord.run_id,
                LLMUsageRecord.cost_usd,
                LLMUsageRecord.input_tokens,
                LLMUsageRecord.output_tokens,
                LLMUsageRecord.status,
                LLMUsageRecord.created_at,
            )
            .where(LLMUsageRecord.tenant_id == tenant_id, LLMUsageRecord.conversation_id == conversation_id)
            .order_by(LLMUsageRecord.created_at)
        )
    ).all()

    runs: Dict[str, Dict[str, Any]] = {}
    for run_id, cost, tin, tout, status, created in rows:
        key = run_id or ""
        r = runs.setdefault(key, {"runId": key, "usd": Decimal(0), "inputTokens": 0, "outputTokens": 0,
                                  "unpriced": False, "startedAt": created})
        r["inputTokens"] += tin or 0
        r["outputTokens"] += tout or 0
        if status == "unpriced":
            r["unpriced"] = True
        else:
            r["usd"] += Decimal(cost or 0)
        if created is not None and (r["startedAt"] is None or created < r["startedAt"]):
            r["startedAt"] = created

    out: List[Dict[str, Any]] = []
    for r in sorted(runs.values(), key=lambda x: (x["startedAt"] is None, x["startedAt"])):
        out.append({**r, "usd": float(r["usd"]),
                    "startedAt": r["startedAt"].isoformat() if r["startedAt"] else None})
    return {
        "totalUsd": float(sum((Decimal(str(r["usd"])) for r in out), Decimal(0))),
        "inputTokens": sum(r["inputTokens"] for r in out),
        "outputTokens": sum(r["outputTokens"] for r in out),
        "unpriced": any(r["unpriced"] for r in out),
        "runs": out,
    }
