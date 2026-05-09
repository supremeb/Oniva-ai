"""
Fetch Market Data Skill
Fetch real-time market data for a ticker.
"""

import random
from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def fetch_market_data(
    ctx: SkillContext,
    ticker: str,
    asset_type: str = "stock",
) -> Dict[str, Any]:
    """
    Fetch market data for a given ticker.

    Args:
        ctx: Skill execution context
        ticker: Ticker symbol
        asset_type: Type of asset (stock, crypto, forex)

    Returns:
        Market data including price, volume, and change
    """
    # Mock implementation
    base_price = 100.0 + random.uniform(-10, 10)
    return {
        "ticker": ticker.upper(),
        "asset_type": asset_type,
        "price": round(base_price, 2),
        "change_percent": round(random.uniform(-5, 5), 2),
        "volume": random.randint(1000, 1000000),
        "timestamp": "2023-01-01T12:00:00Z",  # Mock timestamp
    }
