"""Account routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Account
from app.schemas import AccountDetailSchema, AccountSchema, PaginatedResponse

router = APIRouter(tags=["accounts"])


@router.get("/", response_model=PaginatedResponse[AccountSchema])
async def list_accounts(
    db: Annotated[AsyncSession, Depends(get_db)],
    customer_id: UUID | None = Query(None, description="Filter by customer ID"),
    account_type: str | None = Query(None, description="Filter by account type: checking, savings, investment, loan"),
    status: str | None = Query(None, description="Filter by status: active, dormant, frozen, closed"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AccountSchema]:
    query = select(Account)
    count_query = select(func.count()).select_from(Account)

    if customer_id:
        query = query.where(Account.customer_id == customer_id)
        count_query = count_query.where(Account.customer_id == customer_id)
    if account_type:
        query = query.where(Account.account_type == account_type)
        count_query = count_query.where(Account.account_type == account_type)
    if status:
        query = query.where(Account.status == status)
        count_query = count_query.where(Account.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Account.opened_date.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    accounts = result.scalars().all()

    return PaginatedResponse(
        items=[AccountSchema.model_validate(a) for a in accounts],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{account_id}", response_model=AccountDetailSchema)
async def get_account(
    account_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AccountDetailSchema:
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountDetailSchema.model_validate(account)
