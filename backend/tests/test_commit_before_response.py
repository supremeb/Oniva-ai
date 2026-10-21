"""Write routes must commit BEFORE the response is sent (FastAPI >= 0.118 tears ``yield``
dependencies down after the response by default).

Three layers:

* a static guard over EVERY route of the real app and the /internal router: a session dependency
  (``get_db``, ``get_db_no_tenant``, ``get_system_db``) must be function-scoped; the request-scoped
  ``get_db_streaming`` is allowed only on the routes listed in ``STREAMING_ROUTES``;
* an ASGI ordering test over the real dependency functions and a real /internal route: the commit
  event precedes ``http.response.start``;
* a commit failure surfaces as a 5xx, never as a 2xx.
"""
import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.config import Settings
from app.core import database
from app.core.database import get_db, get_db_no_tenant, get_db_streaming, get_system_db
from app.internal_api.router import mount_internal_router

SESSION_DEPS = {get_db, get_db_no_tenant, get_system_db}

# Routes whose response body keeps using the session while it streams. Each one commits
# explicitly (or only reads) and must stay request-scoped. Anything else is a bug.
STREAMING_ROUTES = {
    ("POST", "/api/v1/agent-studio/agents/{agent_id}/chat/sessions/{session_id}/messages"),
    ("POST", "/api/v1/agent-studio/agents/{agent_id}/evaluate"),
    ("POST", "/api/v1/agents/generate"),
    ("POST", "/api/v1/compliance/templates/{template_id}/execute-stream"),
    ("POST", "/api/v1/workflows/execute-stream"),
    ("GET", "/api/v1/workflows/{workflow_id}/logs/stream"),
}

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"


def _internal_app() -> FastAPI:
    app = FastAPI()
    assert mount_internal_router(app, Settings(
        _env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC))
    return app


def _session_deps(dependant, found=None):
    """Every (call, scope) of the session dependencies reachable from a dependant."""
    found = [] if found is None else found
    for sub in dependant.dependencies:
        if sub.call in SESSION_DEPS or sub.call is get_db_streaming:
            found.append((sub.call, sub.scope))
        _session_deps(sub, found)
    return found


def _all_routes():
    from app.main import app

    def flatten(application):
        # FastAPI >= 0.14x keeps include_router() lazy: resolve the effective (prefixed) routes.
        for route in application.routes:
            if hasattr(route, "effective_route_contexts"):
                yield from route.effective_route_contexts()
            else:
                yield route

    for application in (app, _internal_app()):
        for route in flatten(application):
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for method in sorted(getattr(route, "methods", None) or {"WS"}):
                if method in ("HEAD", "OPTIONS"):
                    continue
                yield method, route.path, dependant


def test_every_session_dependency_is_function_scoped_except_streaming_routes():
    routes = list(_all_routes())
    assert len(routes) > 400, "route discovery broke"
    offenders, streaming_seen = [], set()
    for method, path, dependant in routes:
        for call, scope in _session_deps(dependant):
            if call is get_db_streaming:
                streaming_seen.add((method, path))
            elif scope != "function":
                offenders.append(f"{method} {path}: {call.__name__} is request-scoped")
    assert not offenders, (
        "These routes commit AFTER the response. Declare Depends(get_db, scope=\"function\") "
        "(or get_db_streaming for a streaming route):\n" + "\n".join(sorted(set(offenders))))
    assert streaming_seen == STREAMING_ROUTES, (
        f"unlisted streaming routes: {sorted(streaming_seen - STREAMING_ROUTES)}; "
        f"stale entries: {sorted(STREAMING_ROUTES - streaming_seen)}")


def test_session_dependencies_cover_the_internal_router():
    seen = {path for _, path, d in _all_routes() if path.startswith("/internal") and _session_deps(d)}
    assert {"/internal/runs/finished", "/internal/tools/authorize", "/internal/models/policy"} <= seen


# --- dynamic ordering / failure ----------------------------------------------------------------

class FakeSession:
    """Just enough AsyncSession for the dependency functions and the routes under test."""

    def __init__(self, events, fail_commit):
        self.events, self.fail_commit = events, fail_commit
        self.info = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **kw):
        class _R:
            def scalars(self):
                return self

            def first(self):
                return None

        return _R()

    async def commit(self):
        if self.fail_commit:
            self.events.append("commit_failed")
            raise RuntimeError("commit failed")
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")

    async def close(self):
        pass


async def _call(app, events, method, path, headers=(), body=b""):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"content-length", str(len(body)).encode()), *headers],
        "client": ("t", 1), "server": ("t", 80), "scheme": "http", "root_path": "",
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    statuses = []

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
            events.append("response_start")

    try:
        await app(scope, receive, send)
    except Exception:  # ServerErrorMiddleware sends the 500, then re-raises
        pass
    return statuses


def _patch_sessions(monkeypatch, events, fail_commit=False):
    factory = lambda: FakeSession(events, fail_commit)  # noqa: E731
    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    monkeypatch.setattr(database, "SystemSessionLocal", factory)


def _probe_app(dep, scope):
    router = APIRouter()
    kwargs = {"scope": scope} if scope else {}

    @router.post("/probe")
    async def probe(db=Depends(dep, **kwargs)):
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.parametrize("dep", [get_db, get_db_no_tenant, get_system_db], ids=lambda d: d.__name__)
async def test_function_scoped_session_commits_before_response_start(dep, monkeypatch):
    events = []
    _patch_sessions(monkeypatch, events)
    # Scope is applied at the Depends declaration, exactly as the routes declare it.
    statuses = await _call(_probe_app(dep, "function"), events, "POST", "/probe")
    assert statuses == [200]
    assert events == ["commit", "response_start"]


@pytest.mark.parametrize("dep", [get_db, get_db_no_tenant, get_system_db], ids=lambda d: d.__name__)
async def test_commit_failure_is_a_5xx_not_a_2xx(dep, monkeypatch):
    events = []
    _patch_sessions(monkeypatch, events, fail_commit=True)
    statuses = await _call(_probe_app(dep, "function"), events, "POST", "/probe")
    assert statuses == [500]
    assert "commit_failed" in events


async def test_plain_request_scope_acknowledges_before_it_commits(monkeypatch):
    """Documents why the guard exists: a plain Depends(get_db) responds, then commits."""
    events = []
    _patch_sessions(monkeypatch, events)
    statuses = await _call(_probe_app(get_db, None), events, "POST", "/probe")
    assert statuses == [200]
    assert events == ["response_start", "commit"]


async def test_streaming_dependency_keeps_the_session_open_through_the_response(monkeypatch):
    events = []
    _patch_sessions(monkeypatch, events)
    statuses = await _call(_probe_app(get_db_streaming, None), events, "POST", "/probe")
    assert statuses == [200]
    # the session outlives the response: that is what a streaming generator relies on
    assert events == ["response_start", "commit"]


async def test_internal_run_finished_commits_before_the_engine_sees_the_response(monkeypatch):
    """The real /internal route with the real get_system_db: the engine's outbox only records
    success on a 2xx, so the commit has to be durable first."""
    import uuid

    events = []
    _patch_sessions(monkeypatch, events)
    app = _internal_app()
    body = ('{"kind":"run_finished","conversationId":"%s","submissionId":"s1","status":"done"}'
            % uuid.uuid4()).encode()
    headers = [(b"content-type", b"application/json"), (b"authorization", f"Bearer {GATEWAY}".encode())]

    statuses = await _call(app, events, "POST", "/internal/runs/finished", headers, body)
    assert statuses == [404]  # unknown run: the handler ran and the dependency tore down first
    assert events == ["commit", "response_start"]

    events.clear()
    _patch_sessions(monkeypatch, events, fail_commit=True)
    statuses = await _call(app, events, "POST", "/internal/runs/finished", headers, body)
    assert statuses == [500]
