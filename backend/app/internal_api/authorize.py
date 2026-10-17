"""POST /internal/tools/authorize: per-call tool policy for the engine (Approvals & Policy plan, U3).

The engine asks before every tool call; Python answers ``allow``, ``block`` or ``requireApproval``
(KTD2, KTD6) and indexes each approval in the ledger before the engine commits the item (KTD3).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.registry import _registry
from app.core.database import get_system_db
from app.internal_api.fence import GatewayError
from app.internal_api.tool_specs import spec_for
from app.internal_api.tools import ensure_skills_loaded
from app.models.compliance_audit_log import AuditAction
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.services.approval_service import AUDIT_AGENT_ID, ApprovalService
from app.services.audit_chain import AuditChain
from app.services.tool_policy import TenantPolicy, ToolCallInfo, decide_tool_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools")


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class TaintSource(_Camel):
    kind: str = ""
    name: str = ""
    ref: Optional[str] = None
    at: Optional[Any] = None


class Taint(_Camel):
    tainted: bool = False
    sources: List[TaintSource] = Field(default_factory=list)


class AuthorizeRequest(_Camel):
    request_id: str = Field(alias="requestId", min_length=1, max_length=255)
    tenant_id: uuid.UUID = Field(alias="tenantId")
    project_id: uuid.UUID = Field(alias="projectId")
    conversation_id: uuid.UUID = Field(alias="conversationId")  # the root conversation
    engine_conversation_id: Optional[str] = Field(None, alias="engineConversationId")
    agent_role: Optional[str] = Field(None, alias="agentRole")
    run_id: str = Field(alias="runId", min_length=1, max_length=255)
    tool_call_id: str = Field(alias="toolCallId", min_length=1, max_length=255)
    tool_task_id: str = Field(alias="toolTaskId", min_length=1, max_length=255)
    tool_name: str = Field(alias="toolName", min_length=1, max_length=255)
    tool_source: Literal["gateway", "native"] = Field(alias="toolSource")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    arguments_hash: Optional[str] = Field(None, alias="argumentsHash", max_length=64)
    side_effect: Optional[Literal["none", "write"]] = Field(None, alias="sideEffect")
    replay: Optional[Any] = None
    taint: Taint = Field(default_factory=Taint)


def make_audit(db: AsyncSession) -> AuditChain:
    """Seam for tests: the real chain needs Postgres."""
    return AuditChain(db)


def is_attended(conv: EngineConversation) -> bool:
    """A human owns the root conversation (no owner means an unattended run, KTD6 row 4)."""
    return conv.user_id is not None


def _no_store(body: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def authorize(db: AsyncSession, req: AuthorizeRequest) -> Dict[str, Any]:
    conv = (await db.execute(select(EngineConversation).where(
        EngineConversation.conversation_id == req.conversation_id,
        EngineConversation.tenant_id == req.tenant_id,
        EngineConversation.project_id == req.project_id))).scalar_one_or_none()
    if conv is None:
        raise GatewayError(404, {"error": "conversation_not_found"})
    tenant = (await db.execute(select(Tenant).where(Tenant.id == req.tenant_id))).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        raise GatewayError(404, {"error": "tenant_not_found"})
    policy = TenantPolicy.from_settings(tenant.settings)

    await ensure_skills_loaded()
    spec = spec_for(req.tool_name) if req.tool_name in _registry else None
    info = ToolCallInfo(tool_name=req.tool_name, tool_source=req.tool_source,
                        tainted=req.taint.tainted, agent_role=req.agent_role)
    verdict = decide_tool_call(spec, info, policy, is_attended(conv))

    svc = ApprovalService(db, audit=make_audit(db))
    sources = [s.model_dump(mode="json") for s in req.taint.sources] or None

    if req.tool_name == "ask_user" and verdict.decision == "allow":
        # Indexed so the inbox can list it; the engine's own document stays the source of truth (KTD10).
        await svc.record_pending(
            tenant_id=req.tenant_id, conversation_id=req.conversation_id, tool_task_id=req.tool_task_id,
            engine_conversation_id=req.engine_conversation_id, run_id=req.run_id,
            tool_call_id=req.tool_call_id, tool_name=req.tool_name, arguments=req.arguments,
            arguments_hash=req.arguments_hash, rule="question", approver_role="member",
            requested_by=conv.user_id, project_id=req.project_id, taint_sources=sources, kind="question")
        await svc._commit(req.tenant_id)
        return {"decision": "allow"}

    if verdict.decision == "allow":
        return {"decision": "allow"}

    if verdict.decision == "block":
        await svc.audit.append(
            tenant_id=req.tenant_id, agent_id=AUDIT_AGENT_ID, action=AuditAction.TOOL_POLICY_BLOCK,
            decision="block", rationale=verdict.reason or verdict.code or "blocked",
            tools_called=[req.tool_name],
            extra_metadata={"code": verdict.code, "tool_name": req.tool_name, "run_id": req.run_id,
                            "tool_call_id": req.tool_call_id, "conversation_id": str(req.conversation_id),
                            "agent_role": req.agent_role, "tainted": req.taint.tainted})
        await svc._commit(req.tenant_id)
        return {"decision": "block", "code": verdict.code, "reason": verdict.reason}

    expires_at = (datetime.now(timezone.utc) + timedelta(milliseconds=verdict.ttl_ms)
                  if verdict.ttl_ms is not None else None)
    created_before = await svc.get_by_call(req.tenant_id, req.run_id, req.tool_call_id)
    row = await svc.record_pending(
        tenant_id=req.tenant_id, conversation_id=req.conversation_id, tool_task_id=req.tool_task_id,
        engine_conversation_id=req.engine_conversation_id, run_id=req.run_id,
        tool_call_id=req.tool_call_id, tool_name=req.tool_name, arguments=req.arguments,
        arguments_hash=req.arguments_hash, reason=verdict.reason, rule=verdict.rule or "requires_approval",
        approver_role=verdict.approver_role, requested_by=conv.user_id, expires_at=expires_at,
        project_id=req.project_id, taint_sources=sources)
    if created_before is None:
        await svc._audit(row, AuditAction.APPROVAL_REQUESTED, "requested", None,
                         rationale=verdict.reason or f"{req.tool_name} approval requested",
                         extra={"rule": row.rule, "approver_role": row.approver_role,
                                "requested_by": str(row.requested_by) if row.requested_by else None,
                                "agent_role": req.agent_role})
    await svc._commit(req.tenant_id)
    return {"decision": "requireApproval", "reason": verdict.reason, "approvalId": str(row.id),
            "approvalTtlMs": verdict.ttl_ms, "requiredRole": verdict.approver_role}


@router.post("/authorize")
async def authorize_tool(req: AuthorizeRequest, db: AsyncSession = Depends(get_system_db)) -> JSONResponse:
    return _no_store(await authorize(db, req))
