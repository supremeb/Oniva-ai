"""Holdings (portfolio positions) routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Account
from app.schemas import AccountHoldingsSchema

router = APIRouter(tags=["holdings"])


@router.get("/{account_id}", response_model=AccountHoldingsSchema)
async def get_account_holdings(
    account_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AccountHoldingsSchema:
    """Return all portfolio positions for a given investment account."""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return AccountHoldingsSchema(
        account_id=account.id,
        account_number=account.account_number,
        account_type=account.account_type,
        holdings=account.holdings,
    )
