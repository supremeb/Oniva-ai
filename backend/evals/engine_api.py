"""Thin read-only client for the engine, used by eval trials.

Same auth as `app.services.engine_client.EngineClient`: the service token as a
bearer plus the tenant and project headers of the conversation's binding. It
only does what the evals need; it is not a second engine client for the app.
"""

from __future__ import annotations

from typing import Any

import httpx


class EngineApiError(Exception):
    def __init__(self, status: int, error: str, body: dict[str, Any] | None = None):
        super().__init__(f"engine {status}: {error}")
        self.status = status
        self.error = error
        self.body = body or {}


class EngineApi:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        tenant_id: str,
        project_id: str,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-Oniva-Tenant": str(tenant_id),
            "X-Oniva-Project": str(project_id),
        }
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Any, *, tenant_id: str, project_id: str) -> EngineApi:
        """Build from the app settings (`ENGINE_URL`, `ENGINE_SERVICE_TOKEN`)."""
        return cls(
            settings.ENGINE_URL,
            settings.ENGINE_SERVICE_TOKEN or "",
            tenant_id=tenant_id,
            project_id=project_id,
        )

    async def get_trace(
        self, conversation_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        """One page of `GET /v1/conversations/:id/trace`."""
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if cursor is not None:
            params["cursor"] = cursor
        try:
            resp = await self._http.get(
                f"{self._base}/v1/conversations/{conversation_id}/trace",
                headers=self._headers,
                params=params,
            )
        except httpx.TimeoutException as e:
            raise EngineApiError(504, "engine_timeout") from e
        except httpx.HTTPError as e:
            raise EngineApiError(502, "engine_unreachable") from e
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            err = body.get("error", "engine_error") if isinstance(body, dict) else "engine_error"
            raise EngineApiError(resp.status_code, str(err), body if isinstance(body, dict) else None)
        if not isinstance(body, dict):
            raise EngineApiError(502, "invalid_trace_response")
        return body

    async def aclose(self) -> None:
        await self._http.aclose()
