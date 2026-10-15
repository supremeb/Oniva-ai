"""HTTP client for the engine's service API (plan 003, U8 / KTD12).

The backend authenticates browser users and calls the engine as a service: bearer
``ENGINE_SERVICE_TOKEN`` plus ``X-Oniva-Tenant`` / ``X-Oniva-Project`` taken from the recorded
conversation binding, and ``X-Oniva-Principal`` on approval decisions.
"""
import logging
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.engine_directory import EngineDirectory

logger = logging.getLogger(__name__)


class EngineError(Exception):
    """An engine answer the route layer maps to an HTTP status (never carries the token)."""

    def __init__(self, status: int, error: str, body: Optional[dict] = None):
        super().__init__(f"engine {status} {error}")
        self.status = status
        self.error = error
        self.body = body or {}


class EngineClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._token = settings.ENGINE_SERVICE_TOKEN or ""
        self._directory = EngineDirectory(settings)
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.ENGINE_REQUEST_TIMEOUT_S, read=None),
        )
        self._timeout = settings.ENGINE_REQUEST_TIMEOUT_S

    @property
    def directory(self) -> EngineDirectory:
        return self._directory

    def _headers(self, tenant_id: str, project_id: str, principal: Optional[str] = None) -> dict:
        h = {
            "Authorization": f"Bearer {self._token}",
            "X-Oniva-Tenant": str(tenant_id),
            "X-Oniva-Project": str(project_id),
        }
        if principal is not None:
            h["X-Oniva-Principal"] = principal
        return h

    @staticmethod
    def _raise_for(resp: httpx.Response, body: Optional[dict]) -> None:
        if resp.status_code < 400:
            return
        body = body if isinstance(body, dict) else {}
        raise EngineError(resp.status_code, str(body.get("error", "engine_error")), body)

    async def _json(self, method: str, session_id: str, path: str, *, tenant_id, project_id,
                    json: Optional[dict] = None, principal: Optional[str] = None,
                    extra_headers: Optional[dict] = None) -> dict:
        headers = self._headers(tenant_id, project_id, principal)
        headers.update(extra_headers or {})
        try:
            resp = await self._http.request(
                method, self._directory.url_for(session_id) + path, json=json, headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as e:
            raise EngineError(504, "engine_timeout") from e
        except httpx.HTTPError as e:
            raise EngineError(502, "engine_unreachable") from e
        try:
            body = resp.json()
        except ValueError:
            body = None
        self._raise_for(resp, body)
        return body if isinstance(body, dict) else {}

    async def create_conversation(self, session_id, tenant_id, project_id, conversation_id,
                                  agent: Optional[dict] = None) -> dict:
        payload: dict[str, Any] = {"id": str(conversation_id)}
        if agent is not None:
            payload["agent"] = agent
        return await self._json("POST", session_id, "/v1/conversations",
                                tenant_id=tenant_id, project_id=project_id, json=payload)

    async def submit(self, session_id, tenant_id, project_id, conversation_id, body: dict,
                     idempotency_key: str) -> dict:
        return await self._json(
            "POST", session_id, f"/v1/conversations/{conversation_id}/submissions",
            tenant_id=tenant_id, project_id=project_id, json=body,
            extra_headers={"Idempotency-Key": idempotency_key})

    async def abort(self, session_id, tenant_id, project_id, conversation_id,
                    reason: Optional[str] = None) -> dict:
        return await self._json(
            "POST", session_id, f"/v1/conversations/{conversation_id}/abort",
            tenant_id=tenant_id, project_id=project_id,
            json={"reason": reason} if reason else {})

    async def decide(self, session_id, tenant_id, project_id, conversation_id, tool_task_id,
                     decision: str, note: Optional[str], principal: str) -> dict:
        payload: dict[str, Any] = {"decision": decision}
        if note is not None:
            payload["note"] = note
        return await self._json(
            "POST", session_id,
            f"/v1/conversations/{conversation_id}/approvals/{tool_task_id}/decision",
            tenant_id=tenant_id, project_id=project_id, json=payload, principal=principal)

    async def watch_stream(self, session_id, tenant_id, project_id, conversation_id,
                           last_event_id: Optional[str] = None,
                           cursor: Optional[str] = None) -> httpx.Response:
        """Open the SSE stream. Returns an open response the caller MUST ``aclose()``."""
        headers = self._headers(tenant_id, project_id)
        headers["Accept"] = "text/event-stream"
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        params = {"cursor": cursor} if cursor else None
        url = self._directory.url_for(session_id) + f"/v1/conversations/{conversation_id}/watch"
        req = self._http.build_request("GET", url, headers=headers, params=params)
        try:
            resp = await self._http.send(req, stream=True)
        except httpx.TimeoutException as e:
            raise EngineError(504, "engine_timeout") from e
        except httpx.HTTPError as e:
            raise EngineError(502, "engine_unreachable") from e
        if resp.status_code >= 400:
            try:
                raw = await resp.aread()
                import json as _json
                body = _json.loads(raw)
            except ValueError:
                body = None
            finally:
                await resp.aclose()
            self._raise_for(resp, body)
        return resp

    async def aclose(self) -> None:
        await self._http.aclose()


_client: Optional[EngineClient] = None


def get_engine_client() -> EngineClient:
    """Shared client (FastAPI dependency; tests override it)."""
    global _client
    if _client is None:
        _client = EngineClient(get_settings())
    return _client
