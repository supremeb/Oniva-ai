"""POST /internal/runs/finished: the engine's push when a Fleet or workflow run settles (plan 003 U5).

The worker's notifier posts through a durable outbox, so this endpoint sees at-least-once
delivery. It resolves the run from ``engine_conversations.run_kind`` / ``run_ref_id``, hands the
settled snapshot to the kind's registered ``on_finished`` hook and answers 200 even for a run that
is already finished. A lost post is the reconciler's job, not this endpoint's.
"""
import logging
import uuid
from collections import OrderedDict
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_system_db
from app.models.engine import EngineConversation
from app.services import run_handlers

logger = logging.getLogger(__name__)
router = APIRouter()

_DEDUPE_MAX = 2048
# (run_ref_id, submissionId) pairs whose on_finished already succeeded in this process. A cache,
# not the guarantee: the hook itself is idempotent and never moves a terminal row.
_applied: "OrderedDict[tuple[str, str], None]" = OrderedDict()


def reset_dedupe() -> None:
    _applied.clear()


class RunFinishedBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Optional[str] = None
    conversationId: str
    submissionId: str
    status: str
    reason: Optional[str] = None
    usage: Optional[dict] = None


def binding_to_ref(row: Any) -> run_handlers.RunRef:
    return run_handlers.RunRef(
        kind=row.run_kind, ref_id=row.run_ref_id, tenant_id=row.tenant_id,
        project_id=row.project_id, session_id=row.session_id, conversation_id=row.conversation_id,
    )


@router.post("/runs/finished")
async def run_finished(body: RunFinishedBody, db: AsyncSession = Depends(get_system_db)):
    try:
        conversation_id = uuid.UUID(body.conversationId)
    except ValueError:
        return JSONResponse(status_code=422, content={"error": "invalid_conversation_id"})
    row = (await db.execute(
        select(EngineConversation).where(EngineConversation.conversation_id == conversation_id)
    )).scalars().first()
    if row is None or not row.run_kind or row.run_ref_id is None:
        return JSONResponse(status_code=404, content={"error": "unknown_run"})

    ref = binding_to_ref(row)
    key = (str(ref.ref_id), body.submissionId)
    if key in _applied:
        return {"ok": True, "duplicate": True}
    handler = run_handlers.hook(ref.kind, "on_finished")
    if handler is None:
        logger.warning("run finished for kind %r with no on_finished handler (%s)", ref.kind, ref.ref_id)
        return {"ok": True, "handled": False}
    snapshot = {
        "source": "push", "conversationId": body.conversationId, "submissionId": body.submissionId,
        "status": body.status, "reason": body.reason, "usage": body.usage,
    }
    await handler(ref, snapshot)
    _applied[key] = None
    while len(_applied) > _DEDUPE_MAX:
        _applied.popitem(last=False)
    return {"ok": True, "handled": True}
