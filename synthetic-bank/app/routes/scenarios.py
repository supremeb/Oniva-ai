"""Test scenario routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TestScenario
from app.schemas import PaginatedResponse, TestScenarioSchema

router = APIRouter(tags=["scenarios"])


@router.get("/", response_model=PaginatedResponse[TestScenarioSchema])
async def list_scenarios(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[TestScenarioSchema]:
    """List all test scenarios available in the synthetic bank."""
    count_result = await db.execute(select(func.count()).select_from(TestScenario))
    total = count_result.scalar_one()

    query = select(TestScenario).order_by(TestScenario.scenario_key).offset(offset).limit(limit)
    result = await db.execute(query)
    scenarios = result.scalars().all()

    return PaginatedResponse(
        items=[TestScenarioSchema.model_validate(s) for s in scenarios],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{scenario_id}", response_model=TestScenarioSchema)
async def get_scenario(
    scenario_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TestScenarioSchema:
    result = await db.execute(select(TestScenario).where(TestScenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return TestScenarioSchema.model_validate(scenario)
