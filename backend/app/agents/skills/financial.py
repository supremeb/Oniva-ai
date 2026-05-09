"""
Financial Skills
Skills for market data, financial statements, and risk analysis

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/
"""

from typing import Dict, Any, List, Optional
from app.agents.skills.context import SkillContext


async def fetch_market_data(ctx: SkillContext, ticker: str, asset_type: str = "stock") -> Dict[str, Any]:
    """
    Fetch market data for a given ticker.
    Supported asset_types: stock, crypto, forex
    """
    # Mock implementation for now
    import random
    base_price = 100.0 + random.uniform(-10, 10)
    return {
        "ticker": ticker.upper(),
        "asset_type": asset_type,
        "price": round(base_price, 2),
        "change_percent": round(random.uniform(-5, 5), 2),
        "volume": random.randint(1000, 1000000),
        "timestamp": "2023-01-01T12:00:00Z" # Mock timestamp
    }

async def get_financial_statements(ctx: SkillContext, ticker: str, statement_type: str = "income", period: str = "annual") -> Dict[str, Any]:
    """
    Get financial statements.
    statement_type: income, balance_sheet, cash_flow
    """
    # Mock data
    return {
        "ticker": ticker.upper(),
        "type": statement_type,
        "period": period,
        "data": {
            "revenue": 1000000,
            "net_income": 50000,
            "year": 2023
        }
    }

async def analyze_risk_metrics(ctx: SkillContext, returns: List[float], benchmark_returns: Optional[List[float]] = None) -> Dict[str, float]:
    """
    Calculate risk metrics.
    """
    import math
    if not returns:
        return {}
        
    # Calculate Mean
    mean_return = sum(returns) / len(returns)
    
    # Calculate Std Dev (Volatility)
    variance = sum((x - mean_return) ** 2 for x in returns) / len(returns)
    std_dev = math.sqrt(variance)
    
    # Sharpe Ratio (Assuming 0 risk-free rate for simplicity)
    sharpe = mean_return / std_dev if std_dev != 0 else 0
    
    # VaR (95%) - Simple parametric
    # Z-score for 95% is ~1.645
    var_95 = mean_return - (1.645 * std_dev)
    
    return {
        "mean_return": round(mean_return, 4),
        "volatility": round(std_dev, 4),
        "sharpe_ratio": round(sharpe, 4),
        "var_95": round(var_95, 4)
    }

async def fetch_economic_indicators(ctx: SkillContext, country: str = "USA", indicator: str = "GDP") -> Dict[str, Any]:
    """
    Fetch economic indicators.
    """
    return {
        "country": country,
        "indicator": indicator,
        "value": 25000 if indicator == "GDP" else 3.5, # Mock
        "unit": "Billion USD" if indicator == "GDP" else "%"
    }

async def get_trading_volume(ctx: SkillContext, ticker: str, days: int = 30) -> Dict[str, Any]:
    """
    Get trading volume analysis.
    """
    return {
        "ticker": ticker.upper(),
        "average_volume": 500000,
        "trend": "up",
        "days": days
    }
