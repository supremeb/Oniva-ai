"""``GET /api/v1/inbox`` and ``/inbox/count``: everything waiting on the current user (U7 / KTD8)."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.services.engine_client import EngineClient, get_engine_client
from app.services.inbox_service import InboxService, InvalidInboxQuery, parse_kinds

router = APIRouter()


class InboxSource(BaseModel):
    type: str
    id: str


class InboxItemModel(BaseModel):
    id: str
    kind: str
    title: str
    summary: str
    severity: str
    createdAt: Optional[str] = None
    dueAt: Optional[str] = None
    source: InboxSource
    actions: list[dict[str, Any]]
    href: str


class InboxResponse(BaseModel):
    items: list[InboxItemModel]
    nextCursor: Optional[str] = None


class InboxCountResponse(BaseModel):
    total: int
    byKind: dict[str, int]


@router.get("/inbox", response_model=InboxResponse)
async def list_inbox(
    kinds: Optional[str] = Query(None, description="Comma list: approval,review,question,compliance,fleet,deep_work"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    try:
        items, nxt = await InboxService(db, engine).list_for_user(
            user, kinds=parse_kinds(kinds), limit=limit, cursor=cursor)
    except InvalidInboxQuery as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"items": items, "nextCursor": nxt}


@router.get("/inbox/count", response_model=InboxCountResponse)
async def inbox_count(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    return await InboxService(db).count_for_user(user)
