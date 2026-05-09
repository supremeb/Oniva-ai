"""Loan routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Loan
from app.schemas import LoanSchema, PaginatedResponse

router = APIRouter(tags=["loans"])


@router.get("/", response_model=PaginatedResponse[LoanSchema])
async def list_loans(
    db: Annotated[AsyncSession, Depends(get_db)],
    customer_id: UUID | None = Query(None, description="Filter by customer ID"),
    status: str | None = Query(None, description="Filter by status: active, paid_off, defaulted, delinquent"),
    loan_type: str | None = Query(None, description="Filter by type: mortgage, personal, auto, business"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[LoanSchema]:
    query = select(Loan)
    count_query = select(func.count()).select_from(Loan)

    if customer_id:
        query = query.where(Loan.customer_id == customer_id)
        count_query = count_query.where(Loan.customer_id == customer_id)
    if status:
        query = query.where(Loan.status == status)
        count_query = count_query.where(Loan.status == status)
    if loan_type:
        query = query.where(Loan.loan_type == loan_type)
        count_query = count_query.where(Loan.loan_type == loan_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Loan.origination_date.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    loans = result.scalars().all()

    return PaginatedResponse(
        items=[LoanSchema.model_validate(lo) for lo in loans],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{loan_id}", response_model=LoanSchema)
async def get_loan(
    loan_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoanSchema:
    result = await db.execute(select(Loan).where(Loan.id == loan_id))
    loan = result.scalar_one_or_none()
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    return LoanSchema.model_validate(loan)
