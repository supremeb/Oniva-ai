"""Transaction routes."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Transaction
from app.schemas import PaginatedResponse, TransactionSchema

router = APIRouter(tags=["transactions"])


@router.get("/", response_model=PaginatedResponse[TransactionSchema])
async def list_transactions(
    db: Annotated[AsyncSession, Depends(get_db)],
    account_id: UUID | None = Query(None, description="Filter by account ID"),
    date_from: datetime | None = Query(None, description="Filter transactions on or after this datetime"),
    date_to: datetime | None = Query(None, description="Filter transactions on or before this datetime"),
    min_amount: Decimal | None = Query(None, description="Minimum transaction amount"),
    max_amount: Decimal | None = Query(None, description="Maximum transaction amount"),
    transaction_type: str | None = Query(
        None, description="Filter by type: deposit, withdrawal, transfer, payment, fee"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[TransactionSchema]:
    query = select(Transaction)
    count_query = select(func.count()).select_from(Transaction)

    if account_id:
        query = query.where(Transaction.account_id == account_id)
        count_query = count_query.where(Transaction.account_id == account_id)
    if date_from:
        query = query.where(Transaction.transaction_date >= date_from)
        count_query = count_query.where(Transaction.transaction_date >= date_from)
    if date_to:
        query = query.where(Transaction.transaction_date <= date_to)
        count_query = count_query.where(Transaction.transaction_date <= date_to)
    if min_amount is not None:
        query = query.where(Transaction.amount >= min_amount)
        count_query = count_query.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        query = query.where(Transaction.amount <= max_amount)
        count_query = count_query.where(Transaction.amount <= max_amount)
    if transaction_type:
        query = query.where(Transaction.transaction_type == transaction_type)
        count_query = count_query.where(Transaction.transaction_type == transaction_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Transaction.transaction_date.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    transactions = result.scalars().all()

    return PaginatedResponse(
        items=[TransactionSchema.model_validate(t) for t in transactions],
        total=total,
        limit=limit,
        offset=offset,
    )
