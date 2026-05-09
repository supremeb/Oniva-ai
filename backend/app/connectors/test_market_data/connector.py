"""
Test Market Data Connector

Hardcoded test connector with canned financial data for AAPL, GOOGL, MSFT.
No external API calls — used for integration testing.
Auto-discovered by ConnectorRegistry.auto_discover().
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.connectors.base import BaseConnector, ConnectorMetadata


_CANNED_DATA = {
    "AAPL": {
        "price": 185.92,
        "currency": "USD",
        "pe_ratio": 28.5,
        "revenue": "$383B",
        "market_cap": "$2.87T",
        "sector": "Technology",
    },
    "GOOGL": {
        "price": 141.80,
        "currency": "USD",
        "pe_ratio": 25.1,
        "revenue": "$307B",
        "market_cap": "$1.87T",
        "sector": "Technology",
    },
    "MSFT": {
        "price": 420.55,
        "currency": "USD",
        "pe_ratio": 35.2,
        "revenue": "$227B",
        "market_cap": "$3.10T",
        "sector": "Technology",
    },
}


class TestMarketDataConnector(BaseConnector):
    """Test connector with canned market data for AAPL, GOOGL, MSFT."""

    metadata = ConnectorMetadata(
        id="test_market_data",
        name="Test Market Data",
        description="Hardcoded test connector with canned financial data",
        icon="",
        category="financial",
        auth_type="none",
        version="1.0.0",
        enterprise_only=False,
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._connected = False

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> bool:
        self._connected = False
        return True

    async def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "tickers": list(_CANNED_DATA.keys()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_price",
                "description": "Get current stock price for a ticker",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Stock ticker symbol (e.g. AAPL)",
                        }
                    },
                    "required": ["ticker"],
                },
            },
            {
                "name": "get_financials",
                "description": "Get financial metrics for a ticker",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Stock ticker symbol",
                        }
                    },
                    "required": ["ticker"],
                },
            },
            {
                "name": "list_tickers",
                "description": "List all available ticker symbols",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Any:
        if tool_name == "get_price":
            return self._get_price(arguments.get("ticker", ""))
        elif tool_name == "get_financials":
            return self._get_financials(arguments.get("ticker", ""))
        elif tool_name == "list_tickers":
            return list(_CANNED_DATA.keys())
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    def _get_price(self, ticker: str) -> Dict[str, Any]:
        ticker = ticker.upper()
        if ticker not in _CANNED_DATA:
            return {"error": f"Unknown ticker: {ticker}"}
        data = _CANNED_DATA[ticker]
        return {
            "ticker": ticker,
            "price": data["price"],
            "currency": data["currency"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _get_financials(self, ticker: str) -> Dict[str, Any]:
        ticker = ticker.upper()
        if ticker not in _CANNED_DATA:
            return {"error": f"Unknown ticker: {ticker}"}
        data = _CANNED_DATA[ticker]
        return {
            "ticker": ticker,
            "pe_ratio": data["pe_ratio"],
            "revenue": data["revenue"],
            "market_cap": data["market_cap"],
            "sector": data["sector"],
        }
