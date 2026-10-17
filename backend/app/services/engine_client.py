"""HTTP client for the engine's service API (plan 003, U8 / KTD12).

The backend authenticates browser users and calls the engine as a service: bearer
``ENGINE_SERVICE_TOKEN`` plus ``X-Oniva-Tenant`` / ``X-Oniva-Project`` taken from the recorded
conversation binding, and ``X-Oniva-Principal`` on approval decisions.
"""
import logging
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.engine_directory import EngineDirectory

logger = logging.getLogger(__name__)

# Capability gate (plan 003, KTD16): the engine reports ``apiVersion`` and ``taskKinds`` on /v1/health.
REQUIRED_API_VERSION = 1
FLEET_TASK_KINDS: tuple[str, ...] = ()
WORKFLOW_TASK_KINDS = ("workflow_run", "workflow_node", "workflow_fanout_lane", "workflow_gate")
_CAPS_TTL_OK_S = 60.0
_CAPS_TTL_FAIL_S = 10.0
_PROBE_TIMEOUT_S = 2.0

_monotonic = time.monotonic  # seams for tests
_probe_transport: Optional[httpx.BaseTransport] = None
_caps_cache: dict[str, tuple[float, Optional[dict]]] = {}  # engine url -> (expires_at, caps or None)
_caps_warned: set[str] = set()


def reset_capability_cache() -> None:
    _caps_cache.clear()
    _caps_warned.clear()


def _parse_caps(body: Any) -> Optional[dict]:
    """``{apiVersion, taskKinds}`` from a health body, or None when the fields are missing/invalid."""
    if not isinstance(body, dict):
        return None
    version, kinds = body.get("apiVersion"), body.get("taskKinds", [])
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        return None
    return {"apiVersion": version, "taskKinds": kinds}


def _store_caps(url: str, caps: Optional[dict]) -> Optional[dict]:
    ttl = _CAPS_TTL_OK_S if caps is not None else _CAPS_TTL_FAIL_S
    _caps_cache[url] = (_monotonic() + ttl, caps)
    if caps is None and url not in _caps_warned:
        _caps_warned.add(url)
        logger.warning("engine capability probe failed or incomplete; engine run flags read off")
    return caps


def _cached_caps(url: str) -> tuple[bool, Optional[dict]]:
    hit = _caps_cache.get(url)
    if hit is not None and hit[0] > _monotonic():
        return True, hit[1]
    return False, None


def engine_has_capability(settings: Settings, kinds: tuple[str, ...] = ()) -> bool:
    """True when the engine's cached /v1/health shows ``REQUIRED_API_VERSION`` and every ``kinds``.

    Sync (the flag helpers are): one short-timeout probe on a miss, cached 60 s (10 s on failure).
    An unreachable engine or a missing field reads as capability absent.
    """
    url = (settings.ENGINE_URL or "").rstrip("/")
    if not url:
        return False
    fresh, caps = _cached_caps(url)
    if not fresh:
        try:
            with httpx.Client(transport=_probe_transport, timeout=_PROBE_TIMEOUT_S) as http:
                resp = http.get(url + "/v1/health")
            caps = _parse_caps(resp.json()) if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            caps = None
        _store_caps(url, caps)
    return (caps is not None and caps["apiVersion"] >= REQUIRED_API_VERSION
            and all(k in caps["taskKinds"] for k in kinds))


def run_start_body(kind: str, conversation_id: Any, run_input: Any, *, agent: Optional[dict] = None) -> dict:
    """The ``POST /v1/runs`` body for a host task run: ``{ id, kind, input, agent? }`` (plan 003, U15)."""
    body: dict[str, Any] = {"id": str(conversation_id), "kind": kind, "input": run_input}
    if agent is not None:
        body["agent"] = agent
    return body


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

    async def update_agent(self, session_id, tenant_id, project_id, conversation_id,
                           agent: dict) -> dict:
        """Partial agent config update (engine ``PUT /v1/conversations/:id/agent``).

        Returns ``{conversationId, agent}``; takes effect from the next run.
        """
        return await self._json(
            "PUT", session_id, f"/v1/conversations/{conversation_id}/agent",
            tenant_id=tenant_id, project_id=project_id, json=agent)

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

    async def cancel_submission(self, session_id, tenant_id, project_id, conversation_id,
                                submission_id: str) -> dict:
        """Cancel a still-queued input (engine ``DELETE .../submissions/:id``).

        Raises ``EngineError`` 409 ``not_queued`` once it started, 404 if unknown.
        """
        return await self._json(
            "DELETE", session_id,
            f"/v1/conversations/{conversation_id}/submissions/{submission_id}",
            tenant_id=tenant_id, project_id=project_id)

    async def decide(self, session_id, tenant_id, project_id, conversation_id, tool_task_id,
                     decision: str, note: Optional[str], principal: str) -> dict:
        payload: dict[str, Any] = {"decision": decision}
        if note is not None:
            payload["note"] = note
        return await self._json(
            "POST", session_id,
            f"/v1/conversations/{conversation_id}/approvals/{tool_task_id}/decision",
            tenant_id=tenant_id, project_id=project_id, json=payload, principal=principal)

    async def start_run(self, session_id, tenant_id, project_id, body: dict,
                        idempotency_key: str) -> dict:
        """Start a host task run (engine ``POST /v1/runs``). No retry here: callers own that."""
        return await self._json(
            "POST", session_id, "/v1/runs", tenant_id=tenant_id, project_id=project_id,
            json=body, extra_headers={"Idempotency-Key": idempotency_key})

    async def signal_task(self, session_id, tenant_id, project_id, conversation_id, task_id,
                          key: str, payload: Any = None) -> dict:
        """Deliver a signal to a waiting task (engine ``POST .../tasks/:taskId/signal``)."""
        return await self._json(
            "POST", session_id, f"/v1/conversations/{conversation_id}/tasks/{task_id}/signal",
            tenant_id=tenant_id, project_id=project_id, json={"key": key, "payload": payload})

    async def get_task(self, session_id, tenant_id, project_id, conversation_id, task_id) -> dict:
        """``{id, kind, status, phase, outcome?}`` for polling and reconcile."""
        return await self._json(
            "GET", session_id, f"/v1/conversations/{conversation_id}/tasks/{task_id}",
            tenant_id=tenant_id, project_id=project_id)

    async def capabilities(self) -> Optional[dict]:
        """Cached ``{apiVersion, taskKinds}`` from ``GET /v1/health`` (shares the flag-gate cache)."""
        url = self._directory.url_for(self._directory.default_session_id).rstrip("/")
        fresh, caps = _cached_caps(url)
        if fresh:
            return caps
        try:
            resp = await self._http.get(url + "/v1/health", timeout=_PROBE_TIMEOUT_S)
            caps = _parse_caps(resp.json()) if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            caps = None
        return _store_caps(url, caps)
    async def approvals(self, session_id, tenant_id, project_id, conversation_id) -> list[dict]:
        """The root conversation's approval items (engine ``GET .../approvals``, children included)."""
        body = await self._json(
            "GET", session_id, f"/v1/conversations/{conversation_id}/approvals",
            tenant_id=tenant_id, project_id=project_id)
        items = body.get("approvals")
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    async def answer(self, session_id, tenant_id, project_id, conversation_id, tool_task_id,
                     answer: dict, principal: str) -> dict:
        """Answer an ``ask_user`` question (engine ``POST .../questions/:id/answer``)."""
        return await self._json(
            "POST", session_id,
            f"/v1/conversations/{conversation_id}/questions/{tool_task_id}/answer",
            tenant_id=tenant_id, project_id=project_id, json={"answer": answer}, principal=principal)

    async def questions(self, session_id, tenant_id, project_id, conversation_id) -> list[dict]:
        """The conversation's question items (engine ``GET .../questions``)."""
        body = await self._json(
            "GET", session_id, f"/v1/conversations/{conversation_id}/questions",
            tenant_id=tenant_id, project_id=project_id)
        doc = body.get("questions")
        items = doc.get("items") if isinstance(doc, dict) else doc
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

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
