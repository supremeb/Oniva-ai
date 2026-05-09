"""Watchlist screening routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WatchlistEntry
from app.schemas import PaginatedResponse, WatchlistEntrySchema

router = APIRouter(tags=["watchlist"])


@router.get("/screen", response_model=PaginatedResponse[WatchlistEntrySchema])
async def screen_watchlist(
    db: Annotated[AsyncSession, Depends(get_db)],
    entity_name: str | None = Query(None, description="Case-insensitive partial match on entity name and aliases"),
    country: str | None = Query(None, description="Filter by country (exact, case-insensitive)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[WatchlistEntrySchema]:
    """Screen an entity against the watchlist using fuzzy name matching."""
    query = select(WatchlistEntry)
    count_query = select(func.count()).select_from(WatchlistEntry)

    if entity_name:
        pattern = f"%{entity_name}%"
        # Match against entity_name OR aliases (comma-separated stored as Text)
        name_filter = or_(
            WatchlistEntry.entity_name.ilike(pattern),
            WatchlistEntry.aliases.ilike(pattern),
        )
        query = query.where(name_filter)
        count_query = count_query.where(name_filter)

    if country:
        country_filter = WatchlistEntry.country.ilike(country)
        query = query.where(country_filter)
        count_query = count_query.where(country_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(WatchlistEntry.entity_name).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    return PaginatedResponse(
        items=[WatchlistEntrySchema.model_validate(e) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
