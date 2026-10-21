"""Per-conversation cost summary from the engine usage ledger (llm_usage_records)."""
from decimal import Decimal
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_usage import LLMUsageRecord


# Deep Work stage for a subagent tool name (agent_role); null/"root" is the orchestrator.
_STAGE_BY_ROLE = {"research_worker": "worker", "reflector": "reflection", "output_agent": "output"}
_STAGE_ORDER = ["orchestrator", "worker", "reflection", "output", "compaction"]


def cache_hit_ratio(input_tokens: int, cache_read_tokens: int) -> float | None:
    """cacheRead / (input + cacheRead); None when the ledger has no cache reads to report."""
    total = (input_tokens or 0) + (cache_read_tokens or 0)
    if not cache_read_tokens or total <= 0:
        return None
    return round(cache_read_tokens / total, 4)


def _with_ratio(row: Dict[str, Any]) -> Dict[str, Any]:
    return {**row, "cacheHitRatio": cache_hit_ratio(row["inputTokens"], row["cacheReadTokens"])}


def stage_for(purpose: str | None, agent_role: str | None) -> str:
    """Compaction is its own stage; otherwise the stage comes from the subagent role."""
    if purpose == "compaction":
        return "compaction"
    if agent_role is None or agent_role in ("", "root"):
        return "orchestrator"
    return _STAGE_BY_ROLE.get(agent_role, agent_role)


async def conversation_cost(db: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> Dict[str, Any]:
    """Totals, per-run and per-purpose and per-stage breakdown. Subagent usage is stored under the root conversation id. Unpriced rows add tokens but no dollars and flag the run."""
    rows = (
        await db.execute(
            select(
                LLMUsageRecord.run_id,
                LLMUsageRecord.cost_usd,
                LLMUsageRecord.input_tokens,
                LLMUsageRecord.output_tokens,
                LLMUsageRecord.cache_read_tokens,
                LLMUsageRecord.status,
                LLMUsageRecord.purpose,
                LLMUsageRecord.agent_role,
                LLMUsageRecord.created_at,
            )
            .where(LLMUsageRecord.tenant_id == tenant_id, LLMUsageRecord.conversation_id == conversation_id)
            .order_by(LLMUsageRecord.created_at)
        )
    ).all()

    runs: Dict[str, Dict[str, Any]] = {}
    purposes: Dict[str, Dict[str, Any]] = {}
    stages: Dict[str, Dict[str, Any]] = {}
    for run_id, cost, tin, tout, cache_read, status, purpose, agent_role, created in rows:
        key = run_id or ""
        r = runs.setdefault(key, {"runId": key, "usd": Decimal(0), "inputTokens": 0, "outputTokens": 0,
                                  "cacheReadTokens": 0, "unpriced": False, "startedAt": created})
        r["inputTokens"] += tin or 0
        r["outputTokens"] += tout or 0
        r["cacheReadTokens"] += cache_read or 0
        if status == "unpriced":
            r["unpriced"] = True
        else:
            r["usd"] += Decimal(cost or 0)
        p = purposes.setdefault(purpose or "generation", {"usd": Decimal(0), "inputTokens": 0, "outputTokens": 0,
                                                          "cacheReadTokens": 0, "unpriced": False})
        p["inputTokens"] += tin or 0
        p["outputTokens"] += tout or 0
        p["cacheReadTokens"] += cache_read or 0
        if status == "unpriced":
            p["unpriced"] = True
        else:
            p["usd"] += Decimal(cost or 0)
        st = stages.setdefault(stage_for(purpose, agent_role), {"usd": Decimal(0), "inputTokens": 0,
                                                                "outputTokens": 0, "cacheReadTokens": 0,
                                                                "unpriced": False})
        st["inputTokens"] += tin or 0
        st["outputTokens"] += tout or 0
        st["cacheReadTokens"] += cache_read or 0
        if status == "unpriced":
            st["unpriced"] = True
        else:
            st["usd"] += Decimal(cost or 0)
        if created is not None and (r["startedAt"] is None or created < r["startedAt"]):
            r["startedAt"] = created

    out: List[Dict[str, Any]] = []
    for r in sorted(runs.values(), key=lambda x: (x["startedAt"] is None, x["startedAt"])):
        out.append(_with_ratio({**r, "usd": float(r["usd"]),
                    "startedAt": r["startedAt"].isoformat() if r["startedAt"] else None}))
    total_in = sum(r["inputTokens"] for r in out)
    total_cache = sum(r["cacheReadTokens"] for r in out)
    return {
        "cacheReadTokens": total_cache,
        "cacheHitRatio": cache_hit_ratio(total_in, total_cache),
        "totalUsd": float(sum((Decimal(str(r["usd"])) for r in out), Decimal(0))),
        "inputTokens": sum(r["inputTokens"] for r in out),
        "outputTokens": sum(r["outputTokens"] for r in out),
        "unpriced": any(r["unpriced"] for r in out),
        "runs": out,
        "byPurpose": [_with_ratio({"purpose": k, **v, "usd": float(v["usd"])}) for k, v in sorted(purposes.items())],
        "byStage": [
            _with_ratio({"stage": k, **v, "usd": float(v["usd"])})
            for k, v in sorted(stages.items(), key=lambda kv: (
                _STAGE_ORDER.index(kv[0]) if kv[0] in _STAGE_ORDER else len(_STAGE_ORDER), kv[0]))
        ],
    }
