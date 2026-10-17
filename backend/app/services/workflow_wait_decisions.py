"""Deciding a workflow human-in-loop wait (plan 003 U21, KTD7).

Phase 5 owns the decision route, the ledger and RBAC. A ledger row of kind ``review`` whose conversation is a
workflow run is a node parked on a human: ``ApprovalService.decide`` has already authorised the decider and
won the claim, and then calls ``signal_node`` here instead of ``EngineClient.decide``. The hook signals the
node's engine task with the decision, which wakes it into ``finish``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.services.approval_service import register_decision_hook

RUN_KIND = "workflow"
LEDGER_KIND = "review"
SIGNAL_PREFIX = "decision:"


def signal_key(node_id: str) -> str:
    return f"{SIGNAL_PREFIX}{node_id}"


def option_ids(options: Any) -> list[str]:
    ids = [str(o.get("id")) for o in (options or []) if isinstance(o, dict) and o.get("id")]
    return ids or ["approve", "reject"]


def option_for(decision: str, ids: list[str]) -> str:
    """The node option a ledger decision (``approve`` or ``deny``) stands for."""
    if decision == "approve":
        return "approve" if "approve" in ids else ids[0]
    for preferred in ("reject", "deny"):
        if preferred in ids:
            return preferred
    return ids[1] if len(ids) > 1 else "reject"


async def signal_node(*, engine: Any, conv: Any, row: Any, user: Any, decision: str,
                      note: Optional[str]) -> dict:
    """Signal the parked node task with the decision. Raises ``EngineError`` when the engine refuses."""
    preview = row.arguments_preview or {}
    key = preview.get("signal") or signal_key(str(preview.get("nodeId") or ""))
    ids = option_ids(preview.get("options"))
    payload = {
        "decision": decision,
        "optionId": option_for(decision, ids),
        "decidedBy": str(user.id),
        "decidedAt": datetime.now(timezone.utc).isoformat(),
        "comment": note,
        "approvalId": str(row.id),
    }
    return await engine.signal_task(
        conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id, row.tool_task_id, key, payload)


register_decision_hook(LEDGER_KIND, RUN_KIND, signal_node)
