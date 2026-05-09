"""
Get Financial Statements Skill
Get financial statements for a company.
"""

from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def get_financial_statements(
    ctx: SkillContext,
    ticker: str,
    statement_type: str = "income",
    period: str = "annual",
) -> Dict[str, Any]:
    """
    Get financial statements.

    Args:
        ctx: Skill execution context
        ticker: Ticker symbol
        statement_type: Type (income, balance_sheet, cash_flow)
        period: Period (annual, quarterly)

    Returns:
        Financial statement data
    """
    # Mock data
    return {
        "ticker": ticker.upper(),
        "type": statement_type,
        "period": period,
        "data": {
            "revenue": 1000000,
            "net_income": 50000,
            "year": 2023,
        },
    }
