"""
Synthetic Bank Connector

Native connector that queries the synthetic bank API for E2E QA testing.
Makes HTTP calls to BANK_API_URL (default: http://localhost:8001).
Auto-discovered by ConnectorRegistry.auto_discover().
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.connectors.base import BaseConnector, ConnectorMetadata


BANK_API_URL = os.getenv("BANK_API_URL", "http://localhost:8001")


class BankConnector(BaseConnector):
    """Native connector for the synthetic bank API."""

    metadata = ConnectorMetadata(
        id="synthetic_bank",
        name="Synthetic Bank",
        description="Synthetic banking data for E2E QA testing — customers, accounts, transactions, KYC, compliance, risk scores",
        icon="",
        category="banking",
        auth_type="none",
        version="1.0.0",
        enterprise_only=False,
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._connected = False
        self._base_url = self.config.get("base_url", BANK_API_URL)

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> bool:
        self._connected = False
        return True

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(f"{self._base_url}/health")
                resp.raise_for_status()
                return {
                    "status": "healthy",
                    "bank_api_url": self._base_url,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "bank_api_url": self._base_url,
            }

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_customers",
                "description": "Search and list bank customers with optional filters",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Search by name (partial match)"},
                        "entity_type": {"type": "string", "description": "Filter: individual or corporate"},
                        "risk_level": {"type": "string", "description": "Filter: low, medium, high, critical"},
                        "status": {"type": "string", "description": "Filter: active, inactive, frozen, closed"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_customer",
                "description": "Get a specific customer profile by ID",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer UUID"},
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "name": "get_accounts",
                "description": "List bank accounts, optionally filtered by customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Filter by customer UUID"},
                        "account_type": {"type": "string", "description": "Filter: checking, savings, investment, loan"},
                        "status": {"type": "string", "description": "Filter: active, dormant, frozen, closed"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_transactions",
                "description": "List transactions for an account with optional date and amount filters",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Filter by account UUID"},
                        "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                        "min_amount": {"type": "number", "description": "Minimum transaction amount"},
                        "max_amount": {"type": "number", "description": "Maximum transaction amount"},
                        "transaction_type": {"type": "string", "description": "Filter: deposit, withdrawal, transfer, payment, fee"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_loans",
                "description": "List loans, optionally filtered by customer or status",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Filter by customer UUID"},
                        "status": {"type": "string", "description": "Filter: active, paid_off, defaulted, delinquent"},
                        "loan_type": {"type": "string", "description": "Filter: mortgage, personal, auto, business"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_kyc",
                "description": "Get KYC records and documents for a customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer UUID"},
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "name": "get_compliance_alerts",
                "description": "List compliance alerts with optional filters",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Filter by customer UUID"},
                        "severity": {"type": "string", "description": "Filter: low, medium, high, critical"},
                        "status": {"type": "string", "description": "Filter: open, investigating, resolved, dismissed"},
                        "alert_type": {"type": "string", "description": "Filter: suspicious_activity, sanctions_match, pep_match, etc."},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_risk_score",
                "description": "Get risk score breakdown for a customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer UUID"},
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "name": "screen_watchlist",
                "description": "Screen an entity name against sanctions and watchlists. Returns matching entries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_name": {"type": "string", "description": "Name to screen against watchlists"},
                        "country": {"type": "string", "description": "Optional country filter"},
                    },
                    "required": ["entity_name"],
                },
            },
            {
                "name": "get_beneficial_ownership",
                "description": "Get beneficial ownership structure for a customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer UUID"},
                    },
                    "required": ["customer_id"],
                },
            },
            {
                "name": "get_incidents",
                "description": "List security/operational incidents",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Filter by customer UUID"},
                        "severity": {"type": "string", "description": "Filter: low, medium, high, critical"},
                        "status": {"type": "string", "description": "Filter: open, investigating, resolved, escalated"},
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            },
            {
                "name": "get_holdings",
                "description": "Get portfolio holdings/positions for an investment account",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Account UUID"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "name": "get_scenarios",
                "description": "List all available test scenarios with their expected outcomes",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "batch_get_kyc",
                "description": "Get KYC records for multiple customers in one call. Much more efficient than calling get_kyc repeatedly.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of Customer UUIDs to fetch KYC for",
                        },
                    },
                    "required": ["customer_ids"],
                },
            },
            {
                "name": "batch_screen_watchlist",
                "description": "Screen multiple entities against sanctions/watchlists in one call. Much more efficient than calling screen_watchlist repeatedly.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entities": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "List of objects with 'entity_name' (required) and optional 'country'",
                        },
                    },
                    "required": ["entities"],
                },
            },
        ]

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Any:
        handler = {
            "search_customers": self._search_customers,
            "get_customer": self._get_customer,
            "get_accounts": self._get_accounts,
            "get_transactions": self._get_transactions,
            "get_loans": self._get_loans,
            "get_kyc": self._get_kyc,
            "get_compliance_alerts": self._get_compliance_alerts,
            "get_risk_score": self._get_risk_score,
            "screen_watchlist": self._screen_watchlist,
            "get_beneficial_ownership": self._get_beneficial_ownership,
            "get_incidents": self._get_incidents,
            "get_holdings": self._get_holdings,
            "get_scenarios": self._get_scenarios,
            "batch_get_kyc": self._batch_get_kyc,
            "batch_screen_watchlist": self._batch_screen_watchlist,
        }.get(tool_name)

        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}
        return await handler(arguments)

    # ------------------------------------------------------------------
    # Tool implementations — each makes an HTTP call to bank-api
    # ------------------------------------------------------------------

    async def _fetch(self, path: str, params: Optional[Dict] = None) -> Any:
        """Make a GET request to the bank API."""
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(f"{self._base_url}{path}", params=params)
                if resp.status_code >= 400:
                    return {"error": f"Bank API returned {resp.status_code}: {resp.text[:200]}"}
                return resp.json()
        except Exception as e:
            return {"error": f"Failed to reach bank API at {self._base_url}{path}: {str(e)}"}

    async def _search_customers(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/customers", params)

    async def _get_customer(self, args: Dict) -> Any:
        return await self._fetch(f"/customers/{args['customer_id']}")

    async def _get_accounts(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/accounts", params)

    async def _get_transactions(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/transactions", params)

    async def _get_loans(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/loans", params)

    async def _get_kyc(self, args: Dict) -> Any:
        return await self._fetch(f"/kyc/{args['customer_id']}")

    async def _get_compliance_alerts(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/compliance-alerts", params)

    async def _get_risk_score(self, args: Dict) -> Any:
        return await self._fetch(f"/risk-scores/{args['customer_id']}")

    async def _screen_watchlist(self, args: Dict) -> Any:
        params = {"entity_name": args["entity_name"]}
        if args.get("country"):
            params["country"] = args["country"]
        return await self._fetch("/watchlist/screen", params)

    async def _get_beneficial_ownership(self, args: Dict) -> Any:
        return await self._fetch(f"/customers/{args['customer_id']}")

    async def _get_incidents(self, args: Dict) -> Any:
        params = {k: v for k, v in args.items() if v is not None}
        return await self._fetch("/incidents", params)

    async def _get_holdings(self, args: Dict) -> Any:
        return await self._fetch(f"/holdings/{args['account_id']}")

    async def _get_scenarios(self, args: Dict) -> Any:
        return await self._fetch("/scenarios")

    async def _batch_get_kyc(self, args: Dict) -> Any:
        import asyncio
        customer_ids = args.get("customer_ids", [])
        if isinstance(customer_ids, str):
            customer_ids = json.loads(customer_ids)
        tasks = [self._fetch(f"/kyc/{cid}") for cid in customer_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            "total": len(customer_ids),
            "results": [
                {"customer_id": cid, "kyc": r if not isinstance(r, Exception) else {"error": str(r)}}
                for cid, r in zip(customer_ids, results)
            ],
        }

    async def _batch_screen_watchlist(self, args: Dict) -> Any:
        import asyncio
        entities = args.get("entities", [])
        if isinstance(entities, str):
            entities = json.loads(entities)
        tasks = []
        for entity in entities:
            name = entity.get("entity_name", "") if isinstance(entity, dict) else str(entity)
            params = {"entity_name": name}
            if isinstance(entity, dict) and entity.get("country"):
                params["country"] = entity["country"]
            tasks.append(self._fetch("/watchlist/screen", params))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        flagged = sum(1 for r in results if isinstance(r, dict) and r.get("matches"))
        return {
            "total_screened": len(entities),
            "flagged_count": flagged,
            "results": [
                r if not isinstance(r, Exception) else {"error": str(r)}
                for r in results
            ],
        }
