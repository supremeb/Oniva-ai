
import httpx
from typing import Dict, Any

class TeamsClient:
    BASE_URL = "https://graph.microsoft.com/v1.0"
    
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
    async def _request(self, method: str, endpoint: str, json: Dict = None, params: Dict = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method, 
                url, 
                headers=self.headers, 
                json=json, 
                params=params, 
                timeout=30.0
            )
            response.raise_for_status()
            # Some graph endpoints return empty 204
            if response.status_code == 204:
                return {}
            return response.json()

    async def verify_connection(self) -> bool:
        try:
            # Get user profile
            await self._request("GET", "/me")
            return True
        except Exception:
            return False

    async def send_message(self, team_id: str, channel_id: str, message: str) -> Dict[str, Any]:
        endpoint = f"/teams/{team_id}/channels/{channel_id}/messages"
        payload = {
            "body": {
                "content": message
            }
        }
        return await self._request("POST", endpoint, json=payload)

    async def list_channels(self, team_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/teams/{team_id}/channels")

    async def create_channel(self, team_id: str, display_name: str, description: str = "") -> Dict[str, Any]:
        payload = {
            "displayName": display_name,
            "description": description
        }
        return await self._request("POST", f"/teams/{team_id}/channels", json=payload)

    async def get_messages(self, team_id: str, channel_id: str, limit: int = 20) -> Dict[str, Any]:
        params = {"$top": limit}
        return await self._request("GET", f"/teams/{team_id}/channels/{channel_id}/messages", params=params)
