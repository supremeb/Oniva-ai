"""Deep Work engine contract (plan 2026-10-15-002, U11): the backend side of the engine's
``deep-work-shape`` fixtures.

The engine's scripted Deep Work scenario (``engine/test/watch/deep-work-fixtures.test.ts``) runs on a fake
gateway and an orchestrator config that must be the ones the backend really produces. This test records
both from the real code into ``engine/test/fixtures/deep-work/`` and fails when they drift:

* ``dw_manifest.json``: the ``dw_*`` entries of ``GET /internal/tools/manifest`` (names, JSON Schemas,
  side effects, idempotency, timeouts). The engine builds its fake gateway from it.
* ``execute_agent.json``: ``compose_execute_config`` for a fixed two-worker plan, with each system prompt
  replaced by a ``<prompt:...>`` placeholder (prompt wording is tested elsewhere; the structure is the contract).

    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/internal/test_engine_deep_work_contract.py   # re-record
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.config import Settings
from app.internal_api.router import mount_internal_router
from app.internal_api.tools import ensure_skills_loaded
from app.services.deep_work.models import DeepWorkPlan, WorkerTask
from app.services.engine_deep_work_agent import (
    DeepWorkModels,
    ExecuteInputs,
    compose_execute_config,
)
from tests.contracts.recorder import check_or_record

ENGINE_FIXTURES = Path(__file__).resolve().parents[3] / "engine/test/fixtures/deep-work"
GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
MODELS = DeepWorkModels(high="m/high", mid="m/mid", low="m/low", worker="m/worker")

pytestmark = pytest.mark.skipif(not ENGINE_FIXTURES.parent.exists(), reason="engine not checked out")


@pytest.fixture(scope="module")
def manifest():
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(ensure_skills_loaded())
    finally:
        loop.close()
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    r = TestClient(app).get("/internal/tools/manifest", headers={"Authorization": f"Bearer {GATEWAY}"})
    assert r.status_code == 200
    return {t["name"]: t for t in r.json()["tools"]}


def test_dw_gateway_tools_match_the_engine_fixture(manifest):
    tools = [manifest[name] for name in sorted(manifest) if name.startswith("dw_") and not name.startswith("dw_browser")]
    names = {t["name"] for t in tools}
    assert names == {
        "dw_start_round", "dw_save_learnings", "dw_record_reflection", "dw_write_section",
        "dw_finalize", "dw_get_learnings", "dw_get_report"}
    for t in tools:
        assert t["parameters"]["type"] == "object"
        assert t["sideEffect"] == ("none" if t["name"] in ("dw_get_learnings", "dw_get_report") else "write")
    check_or_record("dw_manifest", {"tools": tools}, ENGINE_FIXTURES)


def test_execute_agent_config_matches_the_engine_fixture(manifest):
    plan = DeepWorkPlan(
        title="Contract run",
        sub_queries=[WorkerTask(worker_id=f"w{i}", sub_query=f"q{i}", tools=["web_search"]) for i in (1, 2)])
    cfg = compose_execute_config(
        ExecuteInputs(plan=plan, models=MODELS, has_search=True, run_id="run-1", max_rounds=40,
                      deadline_ms=1_000_600_000), NOW)
    cfg["systemPrompt"] = "<prompt:orchestrator>"
    for name, sub in cfg["metadata"]["subagents"].items():
        sub["systemPrompt"] = f"<prompt:{name}>"
    # Every non-subagent tool the configs name is served by the gateway manifest (the web tools are fakes in the engine).
    gateway_tools = {t for t in cfg["tools"] if t.startswith("dw_")}
    for sub in cfg["metadata"]["subagents"].values():
        gateway_tools |= {t for t in sub["tools"] if t.startswith("dw_") and not t.startswith("dw_browser")}
    assert gateway_tools <= set(manifest)
    check_or_record("execute_agent", cfg, ENGINE_FIXTURES)
