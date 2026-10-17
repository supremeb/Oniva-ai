"""U20 - migration 121: webhook secrets encrypted, created_by backfilled, unresolvable triggers reported.

Seeds the database at revision 120, runs the backfill (report inspected), then the real Alembic upgrade
(idempotent re-run). Needs pgvector (the Alembic baseline); CI runs it. Uses its own database name so a
developer's ``oniva`` database is never touched.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.webhook_trigger import WebhookTrigger
from app.services import webhook_secrets
from app.services.webhook_trigger_service import WebhookTriggerService
from tests.postgres.conftest import run_alembic

DB = "oniva_p6u20_webhook_mig"
T = str(uuid.uuid4())
U_WF, U_AGENT = str(uuid.uuid4()), str(uuid.uuid4())
WF, AGENT = str(uuid.uuid4()), str(uuid.uuid4())
TRIG_WF, TRIG_AGENT, TRIG_ORPHAN, TRIG_OWNED, TRIG_KEY, TRIG_CLEAN = (str(uuid.uuid4()) for _ in range(6))


def _load_migration():
    from tests.postgres.conftest import BACKEND_DIR

    spec = importlib.util.spec_from_file_location(
        "mig121", BACKEND_DIR / "migrations" / "versions" / "121_encrypt_webhook_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeded(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db(DB)
    owner = pg_server.url(DB)
    res = run_alembic(owner, "upgrade", "120_engine_runs")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text("SET session_replication_role = replica"))  # seed without the FK closure
        c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:t, 'm', 'm')"), {"t": T})
        for u in (U_WF, U_AGENT):
            c.execute(text("INSERT INTO users (id, tenant_id, email, name) VALUES (:u, :t, :e, 'u')"),
                      {"u": u, "t": T, "e": f"{u[:6]}@x.io"})
        c.execute(text("INSERT INTO workflows (id, user_id, tenant_id, name, spec) VALUES (:w, :u, :t, 'W', '{}')"),
                  {"w": WF, "u": U_WF, "t": T})
        c.execute(text("INSERT INTO custom_agents (id, user_id, tenant_id, name, system_prompt, visibility) "
                       "VALUES (:a, :u, :t, 'A', 'x', 'private')"), {"a": AGENT, "u": U_AGENT, "t": T})

        def trig(i, token, wf, agent, config, owner_id=None):
            c.execute(text(
                "INSERT INTO webhook_triggers (id, tenant_id, workflow_id, agent_id, url_token, auth_method, auth_config, "
                "payload_mapping, rate_limit_per_hour, is_enabled, created_by) VALUES "
                "(:i, :t, :w, :a, :tok, :m, CAST(:c AS jsonb), '{}'::jsonb, 100, true, :o)"),
                {"i": i, "t": T, "w": wf, "a": agent, "tok": token, "m": "api_key" if "key" in config else "hmac",
                 "c": json.dumps(config), "o": owner_id})

        trig(TRIG_WF, "wh_wf", WF, None, {"secret": "plain-wf", "signature_header": "X-Sig"})
        trig(TRIG_AGENT, "wh_agent", None, AGENT, {"secret": "plain-agent"})
        trig(TRIG_ORPHAN, "wh_orphan", None, None, {"secret": "plain-orphan"})  # target deleted
        trig(TRIG_OWNED, "wh_owned", WF, None, {"secret": "plain-owned"}, owner_id=U_AGENT)
        trig(TRIG_KEY, "wh_key", WF, None, {"key": "plain-key", "header": "X-K"}, owner_id=U_WF)
        trig(TRIG_CLEAN, "wh_clean", WF, None, {"secret_enc": webhook_secrets.encrypt_secret("already")}, owner_id=U_WF)
    yield eng, owner
    eng.dispose()


def _config(eng, trigger_id):
    with eng.connect() as c:
        return c.execute(text("SELECT auth_config FROM webhook_triggers WHERE id = :i"), {"i": trigger_id}).scalar()


def _owner(eng, trigger_id):
    with eng.connect() as c:
        return str(c.execute(text("SELECT created_by FROM webhook_triggers WHERE id = :i"), {"i": trigger_id}).scalar())


def test_backfill_encrypts_backfills_and_reports_exactly_the_unresolvable(seeded):
    eng, _ = seeded
    module = _load_migration()
    with eng.begin() as conn:
        report = module.backfill(conn)

    assert report.encrypted == 5  # five rows held a plaintext secret or key
    assert report.backfilled == 2
    assert [(r.trigger_id, r.tenant_id, r.target_kind) for r in report.unresolvable] == [(TRIG_ORPHAN, T, "none")]

    for trig, plain in ((TRIG_WF, "plain-wf"), (TRIG_AGENT, "plain-agent"), (TRIG_ORPHAN, "plain-orphan"),
                        (TRIG_OWNED, "plain-owned")):
        config = _config(eng, trig)
        assert "secret" not in config and plain not in json.dumps(config)
        assert webhook_secrets.decrypt_secret(config["secret_enc"]) == plain
    key_config = _config(eng, TRIG_KEY)
    assert "key" not in key_config and webhook_secrets.decrypt_secret(key_config["key_enc"]) == "plain-key"
    assert webhook_secrets.decrypt_secret(_config(eng, TRIG_CLEAN)["secret_enc"]) == "already"
    assert _config(eng, TRIG_WF)["signature_header"] == "X-Sig"

    assert _owner(eng, TRIG_WF) == U_WF and _owner(eng, TRIG_AGENT) == U_AGENT
    assert _owner(eng, TRIG_OWNED) == U_AGENT  # an existing owner is never overwritten
    assert _owner(eng, TRIG_ORPHAN) == "None"


def test_rerun_changes_nothing_and_alembic_chains_to_head(seeded):
    eng, owner = seeded
    before = {t: (_config(eng, t), _owner(eng, t)) for t in (TRIG_WF, TRIG_AGENT, TRIG_ORPHAN, TRIG_OWNED, TRIG_KEY, TRIG_CLEAN)}
    module = _load_migration()
    with eng.begin() as conn:
        report = module.backfill(conn)
    assert (report.encrypted, report.backfilled) == (0, 0)
    assert [r.trigger_id for r in report.unresolvable] == [TRIG_ORPHAN]

    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "121_encrypt_webhook_secrets"
    assert before == {t: (_config(eng, t), _owner(eng, t)) for t in before}

    down = run_alembic(owner, "downgrade", "120_engine_runs")
    assert down.returncode != 0 and "cannot be downgraded" in down.stderr


async def test_runtime_refuses_the_unresolvable_trigger_with_422(seeded):
    _, owner = seeded
    engine = create_async_engine(owner.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            svc = WebhookTriggerService(db)
            raw = b'{"a":1}'
            sig = hmac.new(b"plain-orphan", raw, hashlib.sha256).hexdigest()
            out = await svc.process_webhook("wh_orphan", raw, {"a": 1}, {"x-signature": sig}, "1.2.3.4")
            assert out["status"] == 422 and out["error"] == "trigger_unowned"
            # a migrated trigger verifies against the encrypted secret
            trigger = await db.get(WebhookTrigger, uuid.UUID(TRIG_WF))
            good = hmac.new(b"plain-wf", raw, hashlib.sha256).hexdigest()
            assert await svc._verify_auth(trigger, raw, {"x-sig": good}, "1.2.3.4") is True
            assert await svc._verify_auth(trigger, raw, {"x-sig": "0" * 64}, "1.2.3.4") is False
    finally:
        await engine.dispose()
