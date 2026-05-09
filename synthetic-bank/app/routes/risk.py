"""Risk score routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer
from app.schemas import CustomerRiskSchema

router = APIRouter(tags=["risk"])


@router.get("/{customer_id}", response_model=CustomerRiskSchema)
async def get_customer_risk(
    customer_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerRiskSchema:
    """Return the full risk score breakdown for a customer."""
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    return CustomerRiskSchema(
        customer_id=customer.id,
        first_name=customer.first_name,
        last_name=customer.last_name,
        risk_level=customer.risk_level,
        pep_status=customer.pep_status,
        risk_score=customer.risk_score,
        beneficial_owners=customer.beneficial_owners,
    )
