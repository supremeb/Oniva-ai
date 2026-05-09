"""KYC routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer
from app.schemas import CustomerKYCSchema

router = APIRouter(tags=["kyc"])


@router.get("/{customer_id}", response_model=CustomerKYCSchema)
async def get_customer_kyc(
    customer_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerKYCSchema:
    """Return all KYC records and their documents for a given customer."""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    return CustomerKYCSchema(
        customer_id=customer.id,
        first_name=customer.first_name,
        last_name=customer.last_name,
        kyc_records=customer.kyc_records,
    )
