"""Customer routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Customer
from app.schemas import CustomerDetailSchema, CustomerSchema, PaginatedResponse

router = APIRouter(tags=["customers"])


@router.get("/", response_model=PaginatedResponse[CustomerSchema])
async def list_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    entity_type: str | None = Query(None, description="Filter by entity type: individual, corporate"),
    risk_level: str | None = Query(None, description="Filter by risk level: low, medium, high, critical"),
    status: str | None = Query(None, description="Filter by status: active, inactive, frozen, closed"),
    search: str | None = Query(None, description="Case-insensitive search on first_name or last_name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[CustomerSchema]:
    query = select(Customer)
    count_query = select(func.count()).select_from(Customer)

    if entity_type:
        query = query.where(Customer.entity_type == entity_type)
        count_query = count_query.where(Customer.entity_type == entity_type)
    if risk_level:
        query = query.where(Customer.risk_level == risk_level)
        count_query = count_query.where(Customer.risk_level == risk_level)
    if status:
        query = query.where(Customer.status == status)
        count_query = count_query.where(Customer.status == status)
    if search:
        pattern = f"%{search}%"
        # Match first_name, last_name, or full name (first + last)
        full_name = func.concat(Customer.first_name, " ", Customer.last_name)
        search_filter = (
            Customer.first_name.ilike(pattern)
            | Customer.last_name.ilike(pattern)
            | full_name.ilike(pattern)
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Customer.last_name, Customer.first_name).offset(offset).limit(limit)
    result = await db.execute(query)
    customers = result.scalars().all()

    return PaginatedResponse(
        items=[CustomerSchema.model_validate(c) for c in customers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{customer_id}", response_model=CustomerDetailSchema)
async def get_customer(
    customer_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CustomerDetailSchema:
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return CustomerDetailSchema.model_validate(customer)
