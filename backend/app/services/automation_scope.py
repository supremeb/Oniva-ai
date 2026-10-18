"""Automation scope: the hidden per-tenant project and archived conversation of an engine run.

Fleet jobs and workflow executions run as engine work, and engine conversations need a project
(plan 003, KTD1). Every run gets a deterministic archived ``EngineConversation`` linked back to its
domain row by ``run_kind`` / ``run_ref_id``, so the model policy and usage ledger find it and chat
lists never show it.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.engine import EngineConversation
from app.models.project import Project
from app.services.engine_client import get_engine_client

RUN_NAMESPACE = uuid.UUID("6f0c6d52-3b1e-5a64-9d0e-0a7a1d6c2f40")
AUTOMATION_SETTINGS = {"system": "automations"}
AUTOMATION_PROJECT_NAME = "Automations"
RUN_KINDS = ("fleet", "workflow", "batch")


def run_conversation_id(kind: str, ref_id: Any) -> UUID:
    return uuid.uuid5(RUN_NAMESPACE, f"{kind}:{ref_id}")


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def is_automation_project(settings: Any) -> bool:
    return isinstance(settings, dict) and settings.get("system") == "automations"


async def ensure_automation_project(factory: Callable, tenant_id: Any, owner_id: Any) -> UUID:
    """The tenant's automation project id; created once, race-safe by a per-tenant advisory lock."""
    tenant_id, owner_id = _uuid(tenant_id), _uuid(owner_id)
    async with factory() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"automations:{tenant_id}"},
            )
        found = (await db.execute(
            select(Project.id).where(
                Project.tenant_id == tenant_id,
                Project.settings["system"].as_string() == "automations",
            ).order_by(Project.created_at, Project.id).limit(1)
        )).scalar_one_or_none()
        if found is not None:
            return found
        project = Project(
            tenant_id=tenant_id, owner_id=owner_id, name=AUTOMATION_PROJECT_NAME,
            description="Hidden project holding automation run conversations.",
            settings=dict(AUTOMATION_SETTINGS), is_archived=False,
        )
        db.add(project)
        await db.flush()
        return project.id


async def bind_run_conversation(
    factory: Callable, kind: str, ref_id: Any, tenant_id: Any, user_id: Any,
) -> tuple[UUID, UUID, str]:
    """``(conversation_id, project_id, session_id)`` for a run; the archived row is written once."""
    if kind not in RUN_KINDS:
        raise ValueError(f"unknown run kind {kind!r}")
    tenant_id, user_id, ref_id = _uuid(tenant_id), _uuid(user_id), _uuid(ref_id)
    conversation_id = run_conversation_id(kind, ref_id)
    session_id = get_engine_client().directory.session_for(tenant_id, kind)
    project_id = await ensure_automation_project(factory, tenant_id, user_id)
    async with factory() as db:
        existing = (await db.execute(
            select(EngineConversation.project_id, EngineConversation.session_id)
            .where(EngineConversation.conversation_id == conversation_id)
        )).first()
        if existing is not None:
            return conversation_id, existing.project_id, existing.session_id
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        await db.execute(
            insert(EngineConversation).values(
                conversation_id=conversation_id, tenant_id=tenant_id, project_id=project_id,
                user_id=user_id, session_id=session_id, run_kind=kind, run_ref_id=ref_id,
                archived_at=datetime.now(timezone.utc),
            ).on_conflict_do_nothing(index_elements=["conversation_id"])
        )
    return conversation_id, project_id, session_id
