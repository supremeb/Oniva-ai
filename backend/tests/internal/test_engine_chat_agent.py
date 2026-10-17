"""U3 / R4-R6: engine chat agent config from selections (no database, mocked engine)."""
import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1 import engine_conversations as ec
from app.auth.dependencies import get_current_user, get_current_user_with_query_token
from app.core.database import get_db
from app.models.engine import EngineConversation
from app.services import engine_chat_agent as eca
from app.services.engine_chat_agent import (
    AgentUnavailable,
    ChatInputs,
    Selections,
    build_engine_chat_agent,
    compose_agent_config,
)
from app.services.engine_client import EngineClient, get_engine_client
from tests.internal.test_engine_conversations import (
    PROJECT,
    TENANT_A,
    FakeDB,
    settings,
    user,
)

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
MANIFEST = {
    "web_search", "browse_url", "read_file", "write_file", "execute_python", "search_knowledge",
    "list_available_connectors", "get_connector_schema", "invoke_connector_tool", "summarize_document",
}
KB1, KB2 = str(uuid.uuid4()), str(uuid.uuid4())
CONN = str(uuid.uuid4())


def inputs(**kw) -> ChatInputs:
    base = dict(base_prompt="Custom prompt.", agent_skills=["read_file", "summarize_document"],
                accessible_knowledge_base_ids={KB1, KB2}, valid_connector_ids={CONN})
    base.update(kw)
    return ChatInputs(**base)


def compose(sel: Selections, **kw):
    return compose_agent_config(inputs(**kw), sel, MANIFEST, NOW)


def test_custom_prompt_and_exactly_the_agents_skills():
    built = compose(Selections())
    assert built.agent["systemPrompt"].startswith("Custom prompt.")
    assert built.agent["tools"] == ["read_file", "summarize_document"]
    assert "model" not in built.agent  # the policy endpoint decides per request


def test_selected_connectors_add_the_connector_tools():
    built = compose(Selections(connector_ids=[CONN]))
    assert built.agent["tools"] == [
        "get_connector_schema", "invoke_connector_tool", "list_available_connectors",
        "read_file", "summarize_document"]
    assert built.agent["metadata"]["allowedConnectorIds"] == [CONN]


def test_web_search_off_removes_web_tools_even_if_enabled_skills():
    built = compose(Selections(), agent_skills=["web_search", "browse_url", "read_file"],)
    assert built.agent["tools"] == ["read_file"]
    on = compose(Selections(web_search=True), agent_skills=["read_file"])
    assert on.agent["tools"] == ["browse_url", "read_file", "web_search"]
    assert "Web search is enabled" in on.agent["systemPrompt"]
    assert "Web search is enabled" not in built.agent["systemPrompt"]


def test_kb_selected_adds_search_knowledge_and_instruction():
    built = compose(Selections(knowledge_base_ids=[KB1]))
    assert "search_knowledge" in built.agent["tools"]
    assert built.selections["knowledge_base_ids"] == [KB1]
    assert built.agent["metadata"]["knowledgeBaseIds"] == [KB1]
    assert "Call search_knowledge first" in built.agent["systemPrompt"]
    none = compose(Selections(), agent_skills=["search_knowledge"])
    assert "search_knowledge" not in none.agent["tools"]


def test_inaccessible_kb_is_dropped_and_reported():
    built = compose(Selections(knowledge_base_ids=[KB1, KB2]),
                    accessible_knowledge_base_ids={KB1})
    assert built.selections["knowledge_base_ids"] == [KB1]
    assert built.dropped["knowledge_base_ids"] == [KB2]
    only_bad = compose(Selections(knowledge_base_ids=[KB2]), accessible_knowledge_base_ids=set())
    assert "search_knowledge" not in only_bad.agent["tools"]


def test_agent_and_project_knowledge_bases_are_included():
    built = compose(Selections(), agent_knowledge_base_ids=[KB1], project_knowledge_base_id=KB2)
    assert built.selections["knowledge_base_ids"] == [KB1, KB2]


def test_interactive_only_and_unknown_skills_are_not_served():
    built = compose(Selections(skill_ids=["ask_user", "write_file"]))
    assert "ask_user" not in built.agent["tools"] and "write_file" in built.agent["tools"]
    assert built.dropped["skill_ids"] == ["ask_user"]


def test_skill_uuids_map_to_names():
    ref = str(uuid.uuid5(eca._SKILL_NS, "write_file"))
    assert "write_file" in compose(Selections(skill_ids=[ref])).agent["tools"]


def test_unknown_connector_is_dropped():
    other = str(uuid.uuid4())
    built = compose(Selections(connector_ids=[CONN, other]))
    assert built.selections["connector_ids"] == [CONN] and built.dropped["connector_ids"] == [other]


def test_project_block_and_date_in_prompt():
    built = compose(Selections(), project_name="Atlas", project_instructions="Be terse.",
                    project_files=["a.pdf"])
    prompt = built.agent["systemPrompt"]
    assert 'You are chatting within the project "Atlas".' in prompt
    assert "Be terse." in prompt and "a.pdf" in prompt
    assert "Today's date is October 15, 2026." in prompt


def test_camel_case_selections_are_accepted():
    sel = Selections.model_validate({"knowledgeBaseIds": [KB1], "webSearch": True, "skillIds": ["read_file"]})
    assert sel.knowledge_base_ids == [KB1] and sel.web_search and sel.skill_ids == ["read_file"]


@pytest.mark.asyncio
async def test_build_uses_loader_and_manifest():
    seen = {}

    async def loader(db, **kw):
        seen.update(kw)
        return inputs()

    built = await build_engine_chat_agent(
        None, tenant_id=TENANT_A, user=user(role="admin"), project_id=PROJECT,
        selections=Selections(), loader=loader, manifest=MANIFEST, now=NOW)
    assert seen["is_admin"] is True and str(seen["project_id"]) == PROJECT
    assert built.agent["tools"] == ["read_file", "summarize_document"]


def test_project_context_matches_legacy_text():
    from app.services.agent_resolver import build_project_context, build_web_search_instruction

    assert build_project_context("P", "Do X", ["f.txt"]) == (
        '<project-context>\nYou are chatting within the project "P".\n'
        "All your responses should be relevant to this project.\n"
        "\nProject Instructions:\nDo X\n"
        "\nProject Files (1):\n  - f.txt\n"
        "When the user asks about files, reference these by name. "
        "Relevant content from these files will be provided below when available.\n</project-context>")
    assert build_project_context(None, "  ") is None
    assert build_project_context(None, "Hi") == "<project-instructions>\nHi\n</project-instructions>"
    text = build_web_search_instruction(NOW, ["a.com", "b.com"])
    assert text.startswith("Today's date is October 15, 2026. Web search is enabled.")
    assert "include the current year (2026)" in text and text.endswith("domains: a.com, b.com.")


# --- capability-honest prompt (U4 / R4, KTD4) --------------------------------------


def test_capability_block_omits_web_search_when_off_and_states_the_rule():
    prompt = compose(Selections()).agent["systemPrompt"]
    assert eca.CAPABILITY_HEADING in prompt
    assert "Web search is enabled" not in prompt
    assert "web search" not in prompt.lower()
    assert eca.CAPABILITY_FOOTER in prompt


def test_capability_block_lists_web_search_and_the_instruction_when_on():
    prompt = compose(Selections(web_search=True)).agent["systemPrompt"]
    assert "Web search is enabled" in prompt
    assert "web search" in prompt.lower()
    assert eca.CAPABILITY_FOOTER in prompt


def test_capability_block_lists_knowledge_search_when_kbs_selected():
    prompt = compose(Selections(knowledge_base_ids=[KB1])).agent["systemPrompt"]
    block = prompt.split(eca.CAPABILITY_HEADING, 1)[1]
    assert "search_knowledge" in block


def test_capability_block_lists_connectors_files_and_code():
    built = compose(Selections(connector_ids=[CONN]),
                    agent_skills=["read_file", "execute_python"])
    block = built.agent["systemPrompt"].split(eca.CAPABILITY_HEADING, 1)[1]
    assert "connectors" in block and "read_file" in block and "execute_python" in block


def test_capability_block_lists_tools_outside_the_families_by_name():
    block = eca.build_capabilities_block(["read_file", "generate_chart", "analyze_data"])
    assert "- other tools: analyze_data, generate_chart" in block
    assert "file operations: read_file" in block
    assert block.endswith(eca.CAPABILITY_FOOTER)


def test_capability_block_with_no_tools_is_unchanged():
    block = eca.build_capabilities_block([])
    assert "You have no tools beyond your own knowledge." in block
    assert "other tools" not in block


def test_prompt_never_contains_a_key_or_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-sentinel-secret-123")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-sentinel-456")
    built = compose(Selections(web_search=True, knowledge_base_ids=[KB1], connector_ids=[CONN]),
                    agent_skills=["read_file", "execute_python"])
    prompt = built.agent["systemPrompt"]
    assert "sk-sentinel-secret-123" not in prompt and "tvly-sentinel-456" not in prompt
    assert "api_key" not in prompt.lower() and "apikey" not in prompt.lower()
    assert "model" not in built.agent  # keys and model ids are resolved per request


# --- routes ----------------------------------------------------------------------


class RouteEnv:
    def __init__(self, monkeypatch):
        self.calls = []
        self.db = FakeDB()
        self.current = user()
        cfg = settings()
        self.client = EngineClient(cfg, transport=httpx.MockTransport(self._handle))
        self.app = FastAPI()
        ec.mount_engine_conversations(self.app, cfg)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_engine_client] = lambda: self.client
        self.app.dependency_overrides[get_current_user] = lambda: self.current
        self.app.dependency_overrides[get_current_user_with_query_token] = lambda: self.current
        self.http = TestClient(self.app, raise_server_exceptions=False)
        self.inputs = inputs()
        self.fail_engine = False

        async def role(self_, project_id, user_id, tenant_id):
            return "owner"

        monkeypatch.setattr(ec.ProjectService, "__init__", lambda self_, db: None)
        monkeypatch.setattr(ec.ProjectService, "get_user_role", role)

        async def build(db, **kw):
            async def loader(_db, **k):
                if kw["selections"].agent_id == "gone":
                    raise AgentUnavailable("agent_not_found")
                return self.inputs
            return await build_engine_chat_agent(db, loader=loader, manifest=MANIFEST, now=NOW, **kw)

        monkeypatch.setattr(ec, "build_engine_chat_agent", build)

    def _handle(self, req):
        self.calls.append(req)
        if self.fail_engine:
            return httpx.Response(400, json={"error": "invalid_agent"})
        if req.method == "PUT":
            return httpx.Response(200, json={"conversationId": "x", "agent": json.loads(req.content)})
        return httpx.Response(201, json={"conversationId": "x"})


@pytest.fixture
def renv(monkeypatch):
    return RouteEnv(monkeypatch)


def test_create_ignores_client_prompt_and_tools(renv):
    r = renv.http.post("/api/v1/engine/conversations", json={
        "project_id": PROJECT, "systemPrompt": "evil", "tools": ["execute_python"],
        "agent": {"systemPrompt": "evil", "tools": ["execute_python"]},
        "selections": {"knowledge_base_ids": [KB1], "web_search": False}})
    assert r.status_code == 201
    sent = json.loads(renv.calls[0].content)["agent"]
    assert sent["systemPrompt"].startswith("Custom prompt.") and "evil" not in sent["systemPrompt"]
    assert "execute_python" not in sent["tools"] and "search_knowledge" in sent["tools"]
    row = renv.db.rows[uuid.UUID(r.json()["conversationId"])]
    assert row.selections["knowledge_base_ids"] == [KB1]


def test_create_reports_dropped_kbs(renv):
    renv.inputs = inputs(accessible_knowledge_base_ids={KB1})
    r = renv.http.post("/api/v1/engine/conversations", json={
        "project_id": PROJECT, "selections": {"knowledge_base_ids": [KB1, KB2]}})
    assert r.json()["dropped"]["knowledge_base_ids"] == [KB2]


def test_create_unknown_agent_404_no_engine_call(renv):
    r = renv.http.post("/api/v1/engine/conversations", json={
        "project_id": PROJECT, "selections": {"agent_id": "gone"}})
    assert r.status_code == 404 and not renv.calls


def seed(renv, selections=None, owner=None) -> str:
    cid = uuid.uuid4()
    renv.db.rows[cid] = EngineConversation(
        conversation_id=cid, tenant_id=uuid.UUID(TENANT_A), project_id=uuid.UUID(PROJECT),
        user_id=uuid.UUID(owner or renv.current.id), session_id="sess-1", selections=selections)
    return str(cid)


def test_patch_selections_updates_agent_and_record(renv):
    cid = seed(renv, {"knowledge_base_ids": [KB1], "web_search": False})
    r = renv.http.patch(f"/api/v1/engine/conversations/{cid}/selections", json={"web_search": True})
    assert r.status_code == 200
    req = renv.calls[0]
    assert req.method == "PUT" and req.url.path == f"/v1/conversations/{cid}/agent"
    assert req.headers["x-oniva-tenant"] == TENANT_A and req.headers["x-oniva-project"] == PROJECT
    sent = json.loads(req.content)
    assert "web_search" in sent["tools"] and "search_knowledge" in sent["tools"]  # KB kept (omitted field)
    stored = renv.db.rows[uuid.UUID(cid)].selections
    assert stored["web_search"] is True and stored["knowledge_base_ids"] == [KB1]


def test_patch_failure_leaves_record_unchanged(renv):
    cid = seed(renv, {"web_search": False})
    renv.fail_engine = True
    r = renv.http.patch(f"/api/v1/engine/conversations/{cid}/selections", json={"web_search": True})
    assert r.status_code == 400
    assert renv.db.rows[uuid.UUID(cid)].selections == {"web_search": False}


def test_patch_binding_enforced(renv):
    other = seed(renv, {}, owner=str(uuid.uuid4()))
    assert renv.http.patch(f"/api/v1/engine/conversations/{other}/selections", json={}).status_code == 403
    assert not renv.calls


@pytest.mark.asyncio
async def test_update_agent_client_call():
    seen = {}

    def handler(req):
        seen["req"] = req
        return httpx.Response(200, json={"conversationId": "c", "agent": {"tools": ["a"]}})

    client = EngineClient(settings(), transport=httpx.MockTransport(handler))
    out = await client.update_agent("sess-1", TENANT_A, PROJECT, "c", {"tools": ["a"]})
    assert out["agent"] == {"tools": ["a"]}
    req = seen["req"]
    assert req.method == "PUT" and req.url.path == "/v1/conversations/c/agent"
    assert json.loads(req.content) == {"tools": ["a"]}


# --- search_knowledge binding (KTD3) ----------------------------------------------


@pytest.fixture
async def kb_factory(tmp_path):
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kb.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as session:
            yield session
            await session.commit()

    yield factory
    await engine.dispose()


async def test_gateway_reads_bound_kbs_from_the_conversation_record(kb_factory):
    from app.internal_api.invocations import conversation_knowledge_bases

    conv_with, conv_empty, conv_old = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenant, project, owner = uuid.UUID(TENANT_A), uuid.UUID(PROJECT), uuid.uuid4()
    async with kb_factory() as db:
        for cid, sel in ((conv_with, {"knowledge_base_ids": [KB1]}), (conv_empty, {}), (conv_old, None)):
            db.add(EngineConversation(conversation_id=cid, tenant_id=tenant, project_id=project,
                                      user_id=owner, session_id="s", selections=sel))
    get = lambda c, t=TENANT_A: conversation_knowledge_bases(kb_factory, t, str(c))  # noqa: E731
    assert await get(conv_with) == [KB1]
    assert await get(conv_empty) == []  # selections recorded, no KB: search is refused
    assert await get(conv_old) is None  # predates selections: unbound
    assert await get(conv_with, str(uuid.uuid4())) is None  # wrong tenant sees no record
    assert await conversation_knowledge_bases(kb_factory, TENANT_A, None) is None


async def test_execute_tool_passes_bindings_the_model_cannot_override():
    from app.agents.skills.registry import Skill, _registry
    from app.internal_api.execution import ToolRateLimiter, execute_tool
    from app.internal_api.tool_specs import ToolSpec

    seen = {}

    async def handler(ctx, q: str = "x"):
        seen.update(ctx.metadata)
        return {"ok": True}

    _registry["spy_kb"] = Skill(name="spy_kb", description="d", category="test", handler=handler)
    try:
        out = await execute_tool(
            "spy_kb", {"q": "y"}, tenant_id=TENANT_A, project_id=PROJECT, user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(), spec=ToolSpec(side_effect="none", idempotent=True),
            bindings={"knowledge_base_ids": [KB1]}, limiter=ToolRateLimiter())
    finally:
        _registry.pop("spy_kb", None)
    assert not out.is_error and seen["knowledge_base_ids"] == [KB1]


def test_model_supplied_kb_ids_are_rejected_by_the_args_schema():
    from pydantic import ValidationError

    from app.internal_api.schemas import build_args_model
    from app.skills.search_knowledge.tool import search_knowledge

    model = build_args_model(search_knowledge, "search_knowledge")
    model.model_validate({"query": "q"})
    with pytest.raises(ValidationError):
        model.model_validate({"query": "q", "knowledge_base_ids": [KB2]})


async def test_search_knowledge_scopes_to_bound_kbs(monkeypatch):
    from app.agents.skills.context import SkillContext
    from app.services import rag_service
    from app.skills.search_knowledge.tool import search_knowledge

    captured = {}

    class FakeRAG:
        def __init__(self, db):
            pass

        async def hybrid_search_v2(self, **kw):
            captured.update(kw)
            return []

    monkeypatch.setattr(rag_service, "RAGService", FakeRAG)
    ctx = SkillContext.for_gateway(tenant_id=TENANT_A, user_id=uuid.uuid4(), db=object())
    ctx.metadata["knowledge_base_ids"] = [KB1]
    assert (await search_knowledge(ctx, query="q"))["success"] is True
    assert captured["knowledge_base_ids"] == [uuid.UUID(KB1)]

    ctx.metadata["knowledge_base_ids"] = []
    refused = await search_knowledge(ctx, query="q")
    assert refused["success"] is False and refused["results"] == []

    unbound = SkillContext.for_gateway(tenant_id=TENANT_A, user_id=uuid.uuid4(), db=object())
    captured.clear()
    await search_knowledge(unbound, query="q")
    assert captured["knowledge_base_ids"] is None  # legacy behavior unchanged


# --- legacy continuation summary reaches the model (system prompt) -----------------


def test_continuation_summary_is_a_delimited_prompt_section():
    built = compose_agent_config(inputs(), Selections(), MANIFEST, NOW, continuation_summary="  User wants Q3 churn.  ")
    prompt = built.agent["systemPrompt"]
    assert "## Earlier conversation summary" in prompt
    assert "<earlier_conversation_summary>\nUser wants Q3 churn.\n</earlier_conversation_summary>" in prompt
    assert "Earlier conversation summary" not in compose(Selections()).agent["systemPrompt"]
    blank = compose_agent_config(inputs(), Selections(), MANIFEST, NOW, continuation_summary="  ")
    assert "Earlier conversation summary" not in blank.agent["systemPrompt"]


def test_patch_selections_keeps_the_continuation_summary(renv):
    cid = seed(renv, {"web_search": False})
    renv.db.rows[uuid.UUID(cid)].continuation_summary = "SUMMARY-XYZ"
    r = renv.http.patch(f"/api/v1/engine/conversations/{cid}/selections", json={"web_search": True})
    assert r.status_code == 200
    sent = json.loads(renv.calls[0].content)
    assert "SUMMARY-XYZ" in sent["systemPrompt"] and "web_search" in sent["tools"]


def test_agent_config_carries_compaction_settings():
    cfg = compose(Selections()).agent["compaction"]
    window = eca.DEFAULT_CONTEXT_WINDOW
    assert set(cfg) == {"enabled", "contextWindow", "reserveTokens", "backgroundMargin", "keepRecentTokens"}
    assert cfg["enabled"] is True and cfg["contextWindow"] == window
    # Engine thresholds: background at window - reserve - margin (70 percent), blocking at window - reserve.
    assert window - cfg["reserveTokens"] - cfg["backgroundMargin"] == int(window * 0.70)
    assert cfg["reserveTokens"] > 0 and cfg["keepRecentTokens"] > 0


def test_compaction_settings_scale_with_window_and_fraction():
    cfg = eca.compaction_settings(100_000, 0.5)
    assert cfg["contextWindow"] == 100_000
    assert 100_000 - cfg["reserveTokens"] - cfg["backgroundMargin"] == 50_000
