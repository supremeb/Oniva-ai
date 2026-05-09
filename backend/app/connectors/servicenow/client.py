
import httpx
from typing import Dict, Any

class ServiceNowClient:
    def __init__(self, instance_url: str, username: str = None, password: str = None, token: str = None):
        self.base_url = instance_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self.auth = None
        
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        elif username and password:
            self.auth = (username, password)
            
    async def _request(self, method: str, endpoint: str, json: Dict = None, params: Dict = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/now/{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method, 
                url, 
                headers=self.headers, 
                auth=self.auth, 
                json=json, 
                params=params,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def create_incident(self, short_description: str, description: str = "", urgency: int = 3, impact: int = 3) -> Dict[str, Any]:
        data = {
            "short_description": short_description,
            "description": description,
            "urgency": str(urgency),
            "impact": str(impact)
        }
        return await self._request("POST", "table/incident", json=data)

    async def get_incident(self, sys_id: str = None, number: str = None) -> Dict[str, Any]:
        if sys_id:
            return await self._request("GET", f"table/incident/{sys_id}")
        elif number:
            # Search by number
            result = await self.search_incidents(f"number={number}", limit=1)
            if result.get("result") and len(result["result"]) > 0:
                return {"result": result["result"][0]}
            return {"result": None}
        else:
            raise ValueError("Either sys_id or number must be provided")

    async def update_incident(self, sys_id: str, **kwargs) -> Dict[str, Any]:
        return await self._request("PATCH", f"table/incident/{sys_id}", json=kwargs)

    async def search_incidents(self, query: str, limit: int = 10, offset: int = 0) -> Dict[str, Any]:
        params = {
            "sysparm_query": query,
            "sysparm_limit": limit,
            "sysparm_offset": offset
        }
        return await self._request("GET", "table/incident", params=params)

    async def verify_connection(self) -> bool:
        try:
            # Just try checking current user or a simple query
            # table/sys_user/me is not always available, try querying incident with limit 1
            await self._request("GET", "table/incident", params={"sysparm_limit": 1})
            return True
        except Exception:
            return False
