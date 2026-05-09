
import sys
import os
import unittest
from unittest.mock import MagicMock

# Ensure backend path is in pythonpath
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.agents.skills.financial import (
    fetch_market_data,
    get_financial_statements,
    analyze_risk_metrics,
    fetch_economic_indicators,
    get_trading_volume
)
from pydantic_ai import RunContext
from app.agents.base import AgentDependencies

class TestFinancialSkills(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = MagicMock(spec=RunContext)
        self.ctx.deps = MagicMock(spec=AgentDependencies)

    async def test_fetch_market_data(self):
        result = await fetch_market_data(self.ctx, ticker="AAPL")
        self.assertEqual(result["ticker"], "AAPL")
        self.assertIn("price", result)
        self.assertIn("volume", result)
        
    async def test_get_financial_statements(self):
        result = await get_financial_statements(self.ctx, ticker="GOOGL", statement_type="income")
        self.assertEqual(result["ticker"], "GOOGL")
        self.assertEqual(result["type"], "income")
        
    async def test_analyze_risk_metrics(self):
        returns = [0.01, 0.02, -0.01, 0.05, -0.02]
        result = await analyze_risk_metrics(self.ctx, returns=returns)
        self.assertIn("sharpe_ratio", result)
        self.assertIn("var_95", result)
        
    async def test_fetch_economic_indicators(self):
        result = await fetch_economic_indicators(self.ctx, country="USA", indicator="GDP")
        self.assertEqual(result["country"], "USA")
        self.assertEqual(result["indicator"], "GDP")
        
    async def test_get_trading_volume(self):
        result = await get_trading_volume(self.ctx, ticker="TSLA")
        self.assertEqual(result["ticker"], "TSLA")
        self.assertIn("average_volume", result)

if __name__ == "__main__":
    unittest.main()
