"""
Analyze Risk Metrics Skill
Calculate risk metrics given a set of returns.
"""

import math
from typing import Dict, List, Optional

from app.agents.skills.context import SkillContext


async def analyze_risk_metrics(
    ctx: SkillContext,
    returns: List[float],
    benchmark_returns: Optional[List[float]] = None,
) -> Dict[str, float]:
    """
    Calculate risk metrics.

    Args:
        ctx: Skill execution context
        returns: List of returns
        benchmark_returns: Optional benchmark returns for beta calculation

    Returns:
        Risk metrics including VaR, Sharpe ratio, volatility
    """
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
        "var_95": round(var_95, 4),
    }
