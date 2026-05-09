"""
OFAC SDN Connector
Provides access to OFAC Specially Designated Nationals list for compliance screening
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from app.connectors.base import BaseConnector, ConnectorMetadata
from app.connectors.ofac.client import OFACClient, get_ofac_client


class OFACConnector(BaseConnector):
    """
    OFAC SDN Connector
    Provides tools for sanctions screening against the official OFAC SDN list
    """

    metadata = ConnectorMetadata(
        id="ofac",
        name="OFAC SDN List",
        description="U.S. Treasury OFAC Specially Designated Nationals sanctions list",
        icon="https://home.treasury.gov/themes/flavor/treasury_gov/favicon.ico",
        category="compliance",
        auth_type="none",  # Public data, no auth needed
        version="1.0.0",
        enterprise_only=False
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize OFAC connector

        Args:
            config: Optional configuration
                {
                    "cache_duration_hours": 24,
                    "match_threshold": 0.85
                }
        """
        self.config = config or {}
        self.client: Optional[OFACClient] = None
        self._connected = False

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """
        OFAC SDN is public data - no authentication needed.
        This method loads the SDN data.
        """
        try:
            self.client = get_ofac_client()
            success = await self.client.load_data()
            self._connected = success
            return success
        except Exception as e:
            self._connected = False
            return False

    async def disconnect(self) -> bool:
        """Cleanup resources"""
        self._connected = False
        return True

    async def health_check(self) -> Dict[str, Any]:
        """Check connection status and data availability"""
        if not self.client:
            return {
                "status": "disconnected",
                "message": "Client not initialized",
                "timestamp": datetime.utcnow().isoformat()
            }

        if not self.client.is_loaded:
            return {
                "status": "error",
                "message": "SDN data not loaded",
                "timestamp": datetime.utcnow().isoformat()
            }

        stats = self.client.get_stats()
        return {
            "status": "healthy",
            "message": f"SDN data loaded with {stats['total_entries']} entries",
            "data_source": "U.S. Treasury OFAC",
            "entry_count": stats['total_entries'],
            "last_updated": stats.get('last_fetch'),
            "timestamp": datetime.utcnow().isoformat()
        }

    def get_tools(self) -> List[Dict[str, Any]]:
        """Return list of available OFAC tools"""
        return [
            {
                "name": "search_sdn",
                "description": "Search OFAC SDN list for sanctions matches",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name to search for"
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "Date of birth (YYYY-MM-DD format) - optional"
                        },
                        "country": {
                            "type": "string",
                            "description": "Country code or name - optional"
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Match threshold 0.0-1.0 (default 0.85)"
                        }
                    },
                    "required": ["name"]
                }
            },
            {
                "name": "get_entry",
                "description": "Get details of a specific SDN entry by UID",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {
                            "type": "string",
                            "description": "OFAC SDN entry UID"
                        }
                    },
                    "required": ["uid"]
                }
            },
            {
                "name": "get_stats",
                "description": "Get statistics about the loaded SDN data",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """Execute an OFAC tool"""
        if not self.client or not self.client.is_loaded:
            await self.authenticate({})

        if tool_name == "search_sdn":
            return await self._search_sdn(
                name=arguments.get("name", ""),
                date_of_birth=arguments.get("date_of_birth"),
                country=arguments.get("country"),
                threshold=arguments.get("threshold", 0.85)
            )
        elif tool_name == "get_entry":
            return await self._get_entry(arguments.get("uid", ""))
        elif tool_name == "get_stats":
            return self.client.get_stats() if self.client else {"error": "Client not initialized"}
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def _search_sdn(
        self,
        name: str,
        date_of_birth: Optional[str] = None,
        country: Optional[str] = None,
        threshold: float = 0.85
    ) -> Dict[str, Any]:
        """
        Search OFAC SDN list for sanctions matches

        Returns:
            Search results with matches
        """
        if not self.client:
            return {"error": "Client not initialized", "matches": []}

        results = self.client.search(
            name=name,
            date_of_birth=date_of_birth,
            country=country,
            threshold=threshold
        )

        return {
            "query": {
                "name": name,
                "date_of_birth": date_of_birth,
                "country": country,
                "threshold": threshold
            },
            "match_count": len(results),
            "has_matches": len(results) > 0,
            "matches": [
                {
                    "uid": r.entry.uid,
                    "matched_name": r.matched_name,
                    "full_name": r.entry.full_name,
                    "match_score": round(r.match_score, 4),
                    "match_type": r.match_type,
                    "entry_type": r.entry.entry_type,
                    "programs": r.entry.programs,
                    "aliases": r.entry.aliases[:5],  # Limit aliases
                    "nationalities": r.entry.nationalities,
                    "date_of_birth": r.entry.date_of_birth,
                    "remarks": r.entry.remarks[:200] if r.entry.remarks else None
                }
                for r in results
            ],
            "source": "OFAC SDN",
            "timestamp": datetime.utcnow().isoformat()
        }

    async def _get_entry(self, uid: str) -> Dict[str, Any]:
        """Get a specific SDN entry by UID"""
        if not self.client:
            return {"error": "Client not initialized"}

        entry = self.client.get_entry_by_uid(uid)
        if not entry:
            return {"error": f"Entry not found: {uid}"}

        return {
            "entry": entry.to_dict(),
            "source": "OFAC SDN",
            "timestamp": datetime.utcnow().isoformat()
        }

    # Convenience methods for direct use

    async def search(
        self,
        name: str,
        date_of_birth: Optional[str] = None,
        country: Optional[str] = None,
        threshold: float = 0.85
    ) -> Dict[str, Any]:
        """Convenience method for searching"""
        return await self._search_sdn(name, date_of_birth, country, threshold)

    async def load_data(self, force_refresh: bool = False) -> bool:
        """Load or refresh SDN data"""
        if not self.client:
            self.client = get_ofac_client()
        return await self.client.load_data(force_refresh=force_refresh)
