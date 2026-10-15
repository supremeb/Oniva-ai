"""Deep Work on the new engine: tenant gate and session API (plan 2026-10-15-002, U1 + U7).

The routes under /api/v1/deep-work/ keep their URLs. When the tenant flag is on, the legacy
handlers in deep_work.py delegate here; engine-only routes use `require_engine_deep_work`.

A session is one engine conversation (KTD5) bound to a ``deep_work_runs`` row; the client's
``session_id`` is the run id and ``conversation_id`` the engine conversation. Nothing here holds
process state (R10): every handler reads the run row and talks to the engine, so any backend pod
can serve any request. Live state is the engine watch stream; the routes here start, steer,
cancel and describe the session.
"""
import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.engine_conversations import _engine_http_error
from app.auth.dependencies import get_current_tenant_id, get_current_user_id, get_current_user_with_query_token
from app.auth.providers.base import AuthUser
from app.config import get_settings
from app.core.database import get_db, get_db_for_tenant
from app.models.background_job import BackgroundJob, JobStatus, JobType, JobTrigger
from app.models.deep_work_run import DeepWorkRun
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.services import deep_work_state as dws
from app.services import engine_deep_work_agent as edw
from app.services.deep_work import engine_view as ev
from app.services.deep_work.engine_sandbox import reap_if_terminal, sandbox_available, teardown_all
from app.services.deep_work.models import DeepWorkPlan, IntakeResult
from app.services.engine_chat_agent import Selections, load_chat_inputs
from app.services.engine_client import EngineClient, EngineError, get_engine_client
from app.services.feature_flags import engine_deep_work_enabled
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)

ENGINE_JOB_MARK = "oniva-engine"  # background_jobs.input_data["engine"] for engine sessions
ANSWER_TIMEOUT_S = 180.0
SEARCH_PROVIDERS = ("tavily", "parallelai", "exa", "serpapi")
SEARCH_ENV = {"tavily": "TAVILY_API_KEY", "parallelai": "PARALLEL_API_KEY", "exa": "EXA_API_KEY", "serpapi": "SERP_API_KEY"}


# --- tenant gate (U1) ---------------------------------------------------------------------------


async def tenant_uses_engine_deep_work(db: AsyncSession, tenant_id: UUID, request: Request | None = None) -> bool:
    """True when this tenant's Deep Work runs on the engine (evaluated per request, server-side)."""
    tenant = await db.get(Tenant, UUID(str(tenant_id)))
    if tenant is None:
        return False
    settings = getattr(getattr(request, "app", None), "state", None)
    settings = getattr(settings, "engine_deep_work_settings", None)
    return engine_deep_work_enabled(tenant, settings)


async def require_engine_deep_work_for(db: AsyncSession, tenant_id: UUID, request: Request | None = None) -> None:
    """Gate for engine-only routes: 404 unless the tenant has engine Deep Work on."""
    if not await tenant_uses_engine_deep_work(db, tenant_id, request):
        raise HTTPException(status_code=404, detail="Not found")


async def require_engine_deep_work(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> None:
    """FastAPI dependency form of the gate, for engine-only routes (e.g. steer)."""
    await require_engine_deep_work_for(db, tenant_id, request)


# --- seams ---------------------------------------------------------------------------------------


def _engine() -> EngineClient:
    return get_engine_client()


def tenant_factory(tenant_id: Any):
    """Own tenant-scoped session per call (R17); tests replace this with their database."""
    return functools.partial(get_db_for_tenant, str(tenant_id))


def reading(db: AsyncSession):
    """A read-only "factory" over the request session, for lookups made while dispatching."""

    @asynccontextmanager
    async def use():
        yield db

    return use


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- run lookup -----------------------------------------------------------------------------------


async def owned_run(factory, tenant_id: Any, user_id: Any, session_id: str) -> dws.RunSnapshot:
    """The run for ``session_id``: 404 when it is not in the caller's tenant, 403 when not theirs."""
    try:
        rid = uuid.UUID(str(session_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    run = await dws.load_run_by_id(factory, tenant_id, rid)
    if run is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(run.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")
    return run


async def find_engine_run(factory, tenant_id: Any, session_id: str) -> Optional[dws.RunSnapshot]:
    """The run behind ``session_id`` or ``None`` (legacy sessions have no run row); no ownership check."""
    try:
        rid = uuid.UUID(str(session_id))
    except ValueError:
        return None
    return await dws.load_run_by_id(factory, tenant_id, rid)


async def _session_id_of(factory, run: dws.RunSnapshot) -> str:
    async with factory() as db:
        conv = await db.get(EngineConversation, run.conversation_id)
    if conv is None or str(conv.tenant_id) != str(run.tenant_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return conv.session_id


def _ids(engine_session: str, run: dws.RunSnapshot) -> tuple:
    return (engine_session, run.tenant_id, run.project_id, run.conversation_id)


# --- reconcile (KTD11) ---------------------------------------------------------------------------------


async def refresh_run(engine: EngineClient, factory, run: dws.RunSnapshot, *, reap: bool = False) -> dws.RunSnapshot:
    """Lazy reconcile a started, still ``running`` run with the engine; optionally reap a terminal run's sandboxes.

    An engine that cannot be reached leaves the row as it is (the next read tries again).
    """
    session = None
    if run.status == "running" and run.job_id is not None:
        try:
            session = await _session_id_of(factory, run)

            async def read_state() -> dws.EngineRunState:
                return ev.engine_run_state(await ev.read_snapshot(engine, *_ids(session, run)))

            run = await dws.reconcile(factory, run.tenant_id, run.conversation_id, read_state) or run
        except HTTPException:
            raise
        except (EngineError, asyncio.TimeoutError, OSError):
            logger.warning("Deep Work reconcile skipped for run %s (engine unavailable)", run.id, exc_info=True)
    if reap and run.status != "running":
        try:
            await reap_if_terminal(factory, run.tenant_id, run.conversation_id)
        except Exception:
            logger.warning("Sandbox reap failed for run %s", run.id, exc_info=True)
    return run


# --- engine calls --------------------------------------------------------------------------------------


async def _ask(engine: EngineClient, ids: tuple, text: str, key: str) -> str:
    """Submit ``text`` and wait for the committed answer of that submission."""
    try:
        await engine.submit(*ids, {"text": text, "requestId": key}, key)
        view = await ev.follow(
            engine, *ids, until=lambda v: ev.answer_for(v, key) is not None, timeout=ANSWER_TIMEOUT_S)
    except EngineError as e:
        raise _engine_http_error(e) from None
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The model took too long to answer") from None
    kind, value = ev.answer_for(view, key)  # type: ignore[misc]
    if kind == "error":
        raise HTTPException(status_code=502, detail=f"The model run failed: {value}")
    return value


async def _agent_config_for(db: AsyncSession, tenant_id: Any) -> Optional[dict]:
    from app.api.v1.deep_work import _get_general_assistant_config

    return await _get_general_assistant_config(db, _uuid(tenant_id))


def _project_id_of(body: Any) -> Optional[UUID]:
    raw = getattr(body, "project_id", None)
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail="project_id must be a UUID") from None


async def _open_session(
    engine: EngineClient, db: AsyncSession, user_id: Any, tenant_id: Any, body: Any, query: str,
    phase_config,
) -> dws.RunSnapshot:
    """The run for this request: an existing one (``session_id`` / ``conversation_id``) or a new one."""
    factory = tenant_factory(tenant_id)
    existing = None
    if getattr(body, "session_id", None):
        existing = await owned_run(factory, tenant_id, user_id, body.session_id)
    elif getattr(body, "conversation_id", None):
        try:
            conv = UUID(str(body.conversation_id))
        except ValueError:
            conv = None
        found = await dws.load_run(factory, tenant_id, conv) if conv else None
        if found is not None:
            if str(found.user_id) != str(user_id):
                raise HTTPException(status_code=403, detail="Access denied")
            existing = found
    if existing is not None:
        if existing.status != "running":
            raise HTTPException(status_code=409, detail="Session already ended")
        return existing

    project_id = _project_id_of(body)
    if project_id is None:
        raise HTTPException(status_code=422, detail="project_id is required to start a Deep Work session")
    if await ProjectService(db).get_user_role(project_id, _uuid(user_id), _uuid(tenant_id)) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    agent_config = await _agent_config_for(db, tenant_id)
    models = await edw.resolve_deep_work_models(db, _uuid(tenant_id), None, agent_config)
    conversation_id = uuid.uuid4()
    engine_session = engine.directory.default_session_id
    try:
        await engine.create_conversation(engine_session, tenant_id, project_id, conversation_id, phase_config(models))
    except EngineError as e:
        raise _engine_http_error(e) from None
    async with factory() as fdb:
        # Archived: a Deep Work conversation is not a chat; it must not show in the chat sidebar.
        fdb.add(EngineConversation(
            conversation_id=conversation_id, tenant_id=_uuid(tenant_id), project_id=project_id,
            user_id=_uuid(user_id), session_id=engine_session, title=query[:80],
            selections={"deep_work": True, "knowledge_base_ids": [], "connector_ids": []}, archived_at=_now()))
        await fdb.flush()
    return await dws.create_run(
        factory, tenant_id=tenant_id, project_id=project_id, user_id=user_id,
        conversation_id=conversation_id, query=query)


# --- clarify / plan --------------------------------------------------------------------------------


def intake_message(query: str, kbs: List[str], connectors: List[str]) -> str:
    parts = [f"User Query: {query}"]
    if kbs:
        parts.append(f"Available Knowledge Bases: {', '.join(kbs)}")
    if connectors:
        parts.append(f"Available Connectors: {', '.join(connectors)}")
    return "\n\n".join(parts)


async def clarify(body, db: AsyncSession, user_id, tenant_id) -> dict:
    """Intake: first submission of the session conversation (tools off). Legacy ``IntakeResult`` + ids."""
    engine = _engine()
    factory = tenant_factory(tenant_id)
    run = await _open_session(engine, db, user_id, tenant_id, body, body.query, edw.intake_agent_config)
    engine_session = await _session_id_of(factory, run)
    text = intake_message(body.query, list(body.available_kbs or []), list(body.available_connectors or []))
    try:
        answer = await _ask(engine, _ids(engine_session, run), text, f"dw:{run.id}:clarify:{_sha(text)}")
        result = parse_intake(answer, body.query)
    except HTTPException as e:
        if e.status_code not in (502, 504):
            raise
        logger.warning("Intake failed, auto-proceeding", exc_info=True)  # legacy parity: never block on intake
        result = IntakeResult(action="proceed", enriched_query=body.query)
    if result.action == "proceed" and result.enriched_query:
        await dws.update_run(factory, tenant_id, run.conversation_id, enriched_query=result.enriched_query)
    return {**result.model_dump(), "session_id": str(run.id), "conversation_id": str(run.conversation_id)}


def parse_intake(text: str, query: str) -> IntakeResult:
    """Parse the intake answer; anything unparseable auto-proceeds (as legacy did)."""
    candidates = [text.strip(), *re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            return IntakeResult.model_validate(json.loads(c))
        except (ValueError, ValidationError):
            continue
    return IntakeResult(action="proceed", enriched_query=query)


async def plan(body, db: AsyncSession, user_id, tenant_id) -> dict:
    """Plan: second submission, tools off; the answer is validated into ``DeepWorkPlan`` (one repair, else 422)."""
    engine = _engine()
    factory = tenant_factory(tenant_id)
    text = body.enriched_query or body.query
    run = await _open_session(engine, db, user_id, tenant_id, body, body.query, edw.plan_agent_config)
    engine_session = await _session_id_of(factory, run)
    ids = _ids(engine_session, run)
    agent_config = await _agent_config_for(db, tenant_id)
    models = await edw.resolve_deep_work_models(db, _uuid(tenant_id), None, agent_config)
    try:
        await engine.update_agent(*ids, edw.plan_agent_config(models))
    except EngineError as e:
        raise _engine_http_error(e) from None
    key = f"dw:{run.id}:plan:{_sha(text)}"
    first = await _ask(engine, ids, text, key)

    async def repair(message: str) -> str:
        return await _ask(engine, ids, message, f"{key}:repair:{_sha(message)}")

    try:
        result = await edw.plan_with_repair(first, repair)
    except edw.PlanInvalid as e:
        raise HTTPException(status_code=422, detail={"message": "The plan could not be parsed", "text": e.text, "error": e.error}) from None
    raw = result.model_dump()
    await dws.update_run(factory, tenant_id, run.conversation_id, plan=raw, phase="plan",
                         enriched_query=body.enriched_query)
    return {
        "title": result.title or body.query[:80],
        "steps": [t.sub_query for t in result.sub_queries],
        "raw_plan": raw,
        "session_id": str(run.id),
        "conversation_id": str(run.conversation_id),
    }


# --- start ------------------------------------------------------------------------------------------------


async def _has_search(db: AsyncSession, tenant_id: Any) -> bool:
    from app.models.api_key import APIKey

    try:
        rows = (await db.execute(select(APIKey.provider).where(
            APIKey.tenant_id == _uuid(tenant_id), APIKey.is_active == True,  # noqa: E712
            APIKey.provider.in_(SEARCH_PROVIDERS)))).all()
        if rows:
            return True
    except Exception:
        logger.warning("Search key lookup failed", exc_info=True)
    return any(os.environ.get(v) for v in SEARCH_ENV.values())


async def start(body, db: AsyncSession, user_id, tenant_id) -> dict:
    """Execute the approved plan: idempotent on ``session_id``; 409 while a run is active."""
    engine = _engine()
    factory = tenant_factory(tenant_id)
    if not body.session_id:
        raise HTTPException(status_code=422, detail="session_id is required (run clarify and plan first)")
    run = await owned_run(factory, tenant_id, user_id, body.session_id)
    engine_session = await _session_id_of(factory, run)
    ids = _ids(engine_session, run)

    if run.job_id is not None:  # already started
        try:
            state = ev.engine_run_state(await ev.read_snapshot(engine, *ids))
        except EngineError as e:
            raise _engine_http_error(e) from None
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Engine unavailable") from None
        if state.active and run.status == "running":
            raise HTTPException(status_code=409, detail="Session already active")
        run = await refresh_run(engine, factory, run)
        return {"session_id": str(run.id), "conversation_id": str(run.conversation_id), "status": run.status}
    if run.status != "running":
        raise HTTPException(status_code=409, detail="Session already ended")

    raw_plan = body.plan if body.plan else run.plan
    if not raw_plan:
        raise HTTPException(status_code=422, detail="A plan is required (call plan first)")
    try:
        approved = DeepWorkPlan.model_validate(raw_plan)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid plan: {e.errors()[:3]}") from None

    settings = get_settings()
    selections = Selections(
        knowledge_base_ids=[str(k) for k in (getattr(body, "knowledge_base_ids", None) or [])],
        connector_ids=[str(c) for c in (getattr(body, "connector_ids", None) or [])])
    chat = await load_chat_inputs(
        db, tenant_id=_uuid(tenant_id), user_id=_uuid(user_id), is_admin=False,
        project_id=run.project_id, selections=selections)
    wanted_kbs = list(dict.fromkeys(
        ([chat.project_knowledge_base_id] if chat.project_knowledge_base_id else []) + selections.knowledge_base_ids))
    kb_ids = [k for k in wanted_kbs if k in chat.accessible_knowledge_base_ids]
    connector_ids = [c for c in dict.fromkeys(selections.connector_ids) if c in chat.valid_connector_ids]

    agent_config = await _agent_config_for(db, tenant_id)
    models = await edw.resolve_deep_work_models(db, _uuid(tenant_id), approved, agent_config)
    execute = edw.compose_execute_config(edw.ExecuteInputs(
        plan=approved, models=models, has_search=await _has_search(db, tenant_id),
        knowledge_base_ids=kb_ids, connector_ids=connector_ids, sandbox_enabled=sandbox_available(),
        run_id=str(run.id), max_rounds=edw.default_max_rounds(settings.DEEP_WORK_MAX_REFLECTION_ROUNDS),
        deadline_ms=edw.default_deadline_ms(settings.DEEP_WORK_MAX_DURATION_SEC)))

    # The gateway authorizes KBs and connectors against the conversation's recorded selections.
    async with factory() as fdb:
        conv = await fdb.get(EngineConversation, run.conversation_id)
        conv.selections = {"deep_work": True, "knowledge_base_ids": kb_ids, "connector_ids": connector_ids}
        await fdb.flush()
    await dws.update_run(factory, tenant_id, run.conversation_id, plan=approved.model_dump(),
                         enriched_query=body.enriched_query)
    request_id = f"deep_work:{run.id}"
    try:
        await engine.update_agent(*ids, execute)
        await engine.submit(*ids, {"text": edw.execute_content(approved), "requestId": request_id}, request_id)
    except EngineError as e:
        raise _engine_http_error(e) from None

    async with factory() as fdb:
        job = BackgroundJob(
            id=uuid.uuid4(), tenant_id=_uuid(tenant_id), user_id=_uuid(user_id), job_type=JobType.DEEP_WORK.value,
            status=JobStatus.RUNNING.value, trigger=JobTrigger.MANUAL.value, session_id=str(run.id),
            conversation_id=run.conversation_id, started_at=_now(),
            input_data={"query": run.query, "engine": ENGINE_JOB_MARK, "plan": approved.model_dump()})
        fdb.add(job)
        await fdb.flush()
    linked = await dws.attach_job(factory, tenant_id, run.conversation_id, job.id)
    if linked is None or linked.job_id != job.id:  # a concurrent start won; drop the extra job row
        async with factory() as fdb:
            extra = await fdb.get(BackgroundJob, job.id)
            if extra is not None:
                await fdb.delete(extra)
    return {"session_id": str(run.id), "conversation_id": str(run.conversation_id), "status": "started"}


# --- cancel -----------------------------------------------------------------------------------------------


async def _settle_job(factory, run: dws.RunSnapshot, status: str) -> None:
    if run.job_id is None:
        return
    async with factory() as db:
        job = await db.get(BackgroundJob, run.job_id)
        if job is not None and job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            job.status = status
            job.completed_at = _now()


async def cancel(session_id: str, db: AsyncSession, user_id, tenant_id) -> dict:
    """Abort the root run (the engine aborts children bottom-up), tear down sandboxes, mark ``cancelled``.

    Idempotent: a run that already ended is returned as it is, with no second abort or teardown.
    """
    engine = _engine()
    factory = tenant_factory(tenant_id)
    run = await owned_run(factory, tenant_id, user_id, session_id)
    if run.status == "running":
        engine_session = await _session_id_of(factory, run)
        try:
            await engine.abort(*_ids(engine_session, run), "Cancelled by user")
        except EngineError as e:
            raise _engine_http_error(e) from None
        await teardown_all(run.id)
        run = await dws.update_run(factory, tenant_id, run.conversation_id, status="cancelled") or run
        if run.status == "cancelled":
            await _settle_job(factory, run, JobStatus.CANCELLED.value)
    return {"session_id": str(run.id), "status": run.status}


# --- reads --------------------------------------------------------------------------------------------------


def _worker_states(run: dws.RunSnapshot) -> Dict[str, str]:
    done, failed = set(), set()
    for r in run.rounds or []:
        done.update((r.get("workers") or {}).keys())
        failed.update(r.get("failed") or [])
    return {**{w: "failed" for w in failed}, **{w: "completed" for w in done}}


def todos_of(run: dws.RunSnapshot) -> List[dict]:
    states = _worker_states(run)
    queries = (run.plan or {}).get("sub_queries") or []
    return [{"text": q.get("sub_query", ""), "status": states.get(q.get("worker_id"), "pending")} for q in queries]


async def list_workspace(tenant_id: Any, session_id: str) -> List[dict]:
    from app.services.session_storage import get_session_storage

    workspace = get_session_storage()._get_session_path(str(tenant_id), session_id) / "workspace"
    files = []
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file() and not f.is_symlink():
                stat = f.stat()
                files.append({"name": f.name, "path": str(f.relative_to(workspace)), "size": stat.st_size,
                              "type": f.suffix.lstrip(".") or "file", "modified_at": stat.st_mtime})
    return files


async def full_state(db: AsyncSession, run: dws.RunSnapshot, user_id: Any) -> dict:
    """Engine session state without live fields: row, workspace pointers and artifacts (live state is the view)."""
    engine = _engine()
    factory = tenant_factory(run.tenant_id)
    if str(run.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")
    run = await refresh_run(engine, factory, run, reap=True)
    job_error = None
    if run.status == "failed" and run.job_id is not None:
        async with factory() as fdb:
            job = await fdb.get(BackgroundJob, run.job_id)
            job_error = job.error if job is not None else None
    return {
        "is_engine": True,
        "status": run.status,
        "session_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "engine_phase": run.phase,
        "plan": run.plan,
        "todos": todos_of(run),
        "workspace_files": await list_workspace(run.tenant_id, str(run.id)),
        "artifacts": list(run.artifacts or []),
        "error": job_error,
    }


async def session_status(db: AsyncSession, run: dws.RunSnapshot, user_id: Any) -> dict:
    state = await full_state(db, run, user_id)
    created = run.created_at or _now()
    return {
        "session_id": state["session_id"], "status": state["status"],
        "current_step": run.round, "total_steps": len((run.plan or {}).get("sub_queries") or []),
        "elapsed_seconds": int(((run.updated_at or _now()) - created).total_seconds()),
        "original_message": run.query, "plan": run.plan, "todos": state["todos"],
    }


async def check_workspace_access(factory, tenant_id: Any, user_id: Any, session_id: str) -> None:
    """Engine sessions: the workspace belongs to the run's owner (403 otherwise). Other ids are untouched."""
    run = await find_engine_run(factory, tenant_id, session_id)
    if run is not None and str(run.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")


async def list_sessions(user_id: Any, tenant_id: Any) -> List[dict]:
    """The user's engine sessions (reconciled on read), as session list items tagged ``source: engine``."""
    engine = _engine_or_none()
    factory = tenant_factory(tenant_id)
    async with factory() as db:
        rows = (await db.execute(
            select(DeepWorkRun).where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.user_id == _uuid(user_id))
            .order_by(DeepWorkRun.updated_at.desc()).limit(100))).scalars().all()
        runs = [dws.RunSnapshot.of(r) for r in rows]
    if engine is not None:
        runs = list(await asyncio.gather(*[refresh_run(engine, factory, r) for r in runs]))
    items = []
    for r in runs:
        title = ((r.plan or {}).get("title") or r.query or "")[:80]
        stamp = r.updated_at or r.created_at
        items.append({
            "session_id": str(r.id), "conversation_id": str(r.conversation_id), "status": r.status,
            "plan_title": title, "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": stamp.isoformat() if stamp else None, "source": "engine",
        })
    return items


async def has_engine_runs(db: AsyncSession, tenant_id: Any, user_id: Any) -> bool:
    row = (await db.execute(
        select(DeepWorkRun.id).where(DeepWorkRun.tenant_id == _uuid(tenant_id), DeepWorkRun.user_id == _uuid(user_id)).limit(1)
    )).first()
    return row is not None


def _engine_or_none() -> Optional[EngineClient]:
    try:
        return _engine()
    except Exception:
        return None


async def delete_session(session_id: str, db: AsyncSession, user_id, tenant_id) -> dict:
    """Remove an ended engine session (409 while running): workspace files, run row, listing job."""
    factory = tenant_factory(tenant_id)
    run = await owned_run(factory, tenant_id, user_id, session_id)
    run = await refresh_run(_engine(), factory, run, reap=True)
    if run.status == "running" and run.job_id is not None:
        raise HTTPException(status_code=409, detail="Cannot delete a running session. Stop it first.")
    await _settle_job(factory, run, JobStatus.CANCELLED.value)
    await teardown_all(run.id)
    try:
        import shutil

        from app.services.session_storage import get_session_storage

        path = get_session_storage()._get_session_path(str(tenant_id), str(run.id))
        if path.exists():
            shutil.rmtree(path)
    except Exception:
        logger.warning("Failed to delete workspace of session %s", run.id, exc_info=True)
    await dws.delete_run(factory, tenant_id, run.id)
    return {"status": "deleted"}


# --- engine-only routes ---------------------------------------------------------------------------------------

router = APIRouter()


class SteerBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    requestId: Optional[str] = None


@router.post("/deep-work/{session_id}/steer", status_code=202, dependencies=[Depends(require_engine_deep_work)])
async def steer(
    session_id: str,
    body: SteerBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Queue a steer message for the running session (consumed after the current tool round)."""
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    engine = _engine()
    factory = tenant_factory(tenant_id)
    run = await owned_run(factory, tenant_id, user_id, session_id)
    if run.status != "running" or run.job_id is None:
        raise HTTPException(status_code=409, detail="Session is not running")
    engine_session = await _session_id_of(factory, run)
    key = idempotency_key or body.requestId or str(uuid.uuid4())
    try:
        return await engine.submit(
            *_ids(engine_session, run), {"text": body.text, "whenBusy": "steer", "requestId": body.requestId or key}, key)
    except EngineError as e:
        raise _engine_http_error(e) from None


@router.get("/deep-work/{session_id}/workers/{child_conversation_id}/watch")
async def watch_worker(
    session_id: str,
    child_conversation_id: str,
    cursor: Optional[str] = Query(default=None, pattern=r"^(0|[1-9][0-9]*)$"),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    user: AuthUser = Depends(get_current_user_with_query_token),
):
    """Read-only watch of a worker's child conversation, only for ids that appear in the root's ``subagents`` view."""
    engine = _engine()
    tenant_id = user.tenant_id
    factory = tenant_factory(tenant_id)
    run = await owned_run(factory, tenant_id, user.id, session_id)
    engine_session = await _session_id_of(factory, run)
    try:
        root = await ev.read_snapshot(engine, *_ids(engine_session, run))
    except EngineError as e:
        raise _engine_http_error(e) from None
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Engine unavailable") from None
    if child_conversation_id not in ev.subagent_conversation_ids(root):
        raise HTTPException(status_code=404, detail="Worker not found")
    try:
        upstream = await engine.watch_stream(
            engine_session, run.tenant_id, run.project_id, child_conversation_id,
            last_event_id=last_event_id, cursor=cursor)
    except EngineError as e:
        raise _engine_http_error(e) from None

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
