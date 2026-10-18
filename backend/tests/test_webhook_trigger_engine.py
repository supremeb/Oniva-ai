"""Plan 003 U20: webhooks execute through the engine (SQLite tier, fake engine, real routes)."""
import hashlib
import hmac
import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.v1 import webhooks as wh_api
from app.auth.dependencies import get_current_tenant_id, get_current_user
from app.core.database import get_db, get_db_no_tenant
from app.models.background_job import BackgroundJob
from app.models.custom_agent import CustomAgent
from app.models.tenant import Tenant
from app.models.user import User
from app.models.webhook_trigger import WebhookDelivery, WebhookTrigger
from app.models.workflow import Workflow, WorkflowExecution
from app.services import webhook_secrets
from app.services import webhook_trigger_service as wts
from app.services import workflow_engine_runs as wer
from app.services.engine_client import EngineError
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)

SPEC = {
    "nodes": [
        {"id": "s", "type": "start", "data": {}, "position": {"x": 0.0, "y": 0.0}},
        {"id": "o", "type": "output", "data": {}, "position": {"x": 1.0, "y": 0.0}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "o"}],
}
SECRET = "whsec_test_secret"
BUCKET = 86400
T0 = datetime.fromtimestamp(BUCKET * 20000 + 3600, tz=timezone.utc)  # an hour into a bucket


class FakeEngine:
    def __init__(self):
        self.directory = SimpleNamespace(default_session_id="s1")
        self.starts = []
        self.runs = {}
        self.down = False

    async def start_run(self, session, tenant, project, body, key):
        self.starts.append({"body": body, "key": key})
        if self.down:
            raise EngineError(502, "engine_unreachable")
        return dict(self.runs.setdefault(body["id"], {"conversationId": body["id"], "taskId": f"task-{len(self.runs) + 1}"}))


class World:
    clock = T0

    def sign(self, raw: bytes, secret: str = SECRET, prefix: bool = True) -> str:
        digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return f"sha256={digest}" if prefix else digest

    async def add_trigger(self, **kw):
        values = dict(tenant_id=self.tenant, workflow_id=self.wf, auth_method="hmac", payload_mapping={"who": "name"},
                      auth_config={"secret_enc": webhook_secrets.encrypt_secret(SECRET)}, created_by=self.user,
                      rate_limit_per_hour=100)
        values.update(kw)
        async with self.maker() as db:
            trigger = WebhookTrigger(**values)
            db.add(trigger)
            await db.commit()
            return trigger.id, trigger.url_token

    async def post(self, token, body, headers=None, sign=True, raw=None):
        raw = raw if raw is not None else json.dumps(body).encode()
        hdrs = {"content-type": "application/json", **(headers or {})}
        if sign and "x-signature" not in hdrs:
            hdrs["x-signature"] = self.sign(raw)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t") as c:
            return await c.post(f"/api/v1/webhooks/{token}", content=raw, headers=hdrs)

    async def executions(self):
        async with self.maker() as db:
            return (await db.execute(select(WorkflowExecution))).scalars().all()


@pytest.fixture
async def w(maker, factory, monkeypatch):  # noqa: F811
    world = World()
    world.maker = maker
    # Another test module pops fleet_engine from sys.modules; resolve the live module, not a stale import.
    fe = world.fe = importlib.import_module("app.services.fleet_engine")
    world.engine = FakeEngine()
    monkeypatch.setattr(wer, "get_engine_client", lambda: world.engine)
    monkeypatch.setattr(wer, "tenant_factory", lambda tenant_id: factory)
    monkeypatch.setattr(wer, "engine_workflows_enabled",
                        lambda tenant, settings=None: bool(((tenant.settings or {}).get("features") or {}).get("ENABLE_ENGINE_WORKFLOWS")))
    monkeypatch.setattr(fe, "engine_fleet_enabled",
                        lambda tenant, settings=None: bool(((tenant.settings or {}).get("features") or {}).get("ENABLE_ENGINE_FLEET")))
    monkeypatch.setattr(wts, "_utcnow", lambda: world.clock)

    class _Clock(datetime):  # row timestamps follow the test clock, so the 24 h window is testable
        @classmethod
        def now(cls, tz=None):
            return world.clock

    monkeypatch.setattr(wer, "datetime", _Clock)
    world.fleet_starts = []

    async def start_after_commit(tenant_id, job_id, **kw):
        world.fleet_starts.append(job_id)

    monkeypatch.setattr(fe, "start_after_commit", start_after_commit)
    world.tenant, world.user, world.wf, world.agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        db.add(Tenant(id=world.tenant, name="t", slug=f"t-{world.tenant.hex[:6]}", is_active=True,
                      settings={"features": {"ENABLE_ENGINE_WORKFLOWS": True, "ENABLE_ENGINE_FLEET": True}}))
        await db.commit()
        db.add(User(id=world.user, tenant_id=world.tenant, email="u@x.io", name="u"))
        await db.commit()
        db.add(Workflow(id=world.wf, user_id=world.user, tenant_id=world.tenant, name="W", spec=SPEC))
        await db.execute(CustomAgent.__table__.insert().values(
            id=world.agent, user_id=world.user, tenant_id=world.tenant, name="a", system_prompt="x",
            skills=None, connector_ids=None, knowledge_base_ids=None, workflow_ids=None, compliance_tags=None,
            resource_constraints={"max_concurrent": 1}))
        await db.commit()

    app = FastAPI()
    app.include_router(wh_api.router, prefix="/api/v1")

    async def db_dep():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db_no_tenant] = db_dep
    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=str(world.user), role="member", email="u@x.io")
    app.dependency_overrides[get_current_tenant_id] = lambda: world.tenant
    world.app = app
    return world


# ---- raw-body HMAC --------------------------------------------------------------------------------


async def test_signature_over_raw_bytes_survives_whitespace_and_key_order(w):
    _, token = await w.add_trigger()
    raw = b'{ "b": 2,\n  "name": "Ada",   "a": 1 }'
    r = await w.post(token, None, raw=raw)
    assert r.status_code == 202, r.text
    [row] = await w.executions()
    assert row.input_data["who"] == "Ada"


async def test_signature_over_reserialised_json_is_rejected(w):
    _, token = await w.add_trigger()
    raw = b'{ "name": "Ada",   "a": 1 }'
    compact = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    r = await w.post(token, None, raw=raw, headers={"x-signature": w.sign(compact)})
    assert r.status_code == 401
    assert await w.executions() == []


async def test_one_byte_tamper_and_missing_signature_are_401(w):
    _, token = await w.add_trigger()
    raw = b'{"name":"Ada"}'
    sig = w.sign(raw)
    assert (await w.post(token, None, raw=b'{"name":"Adb"}', headers={"x-signature": sig})).status_code == 401
    assert (await w.post(token, None, raw=raw, sign=False)).status_code == 401
    assert (await w.post(token, None, raw=raw, headers={"x-signature": w.sign(raw, prefix=False)})).status_code == 202


async def test_timestamp_tolerance_is_enforced_over_ts_dot_raw(w):
    _, token = await w.add_trigger(auth_config={"secret_enc": webhook_secrets.encrypt_secret(SECRET),
                                                "timestamp_header": "X-Ts"})
    raw = b'{"name":"Ada"}'
    ts = str(int(w.clock.timestamp()))
    good = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    assert (await w.post(token, None, raw=raw, headers={"x-ts": ts, "x-signature": good})).status_code == 202
    stale = str(int(w.clock.timestamp()) - 301)
    sig = hmac.new(SECRET.encode(), f"{stale}.".encode() + raw, hashlib.sha256).hexdigest()
    assert (await w.post(token, None, raw=raw, headers={"x-ts": stale, "x-signature": sig, "idempotency-key": "k2"})).status_code == 401
    assert (await w.post(token, None, raw=raw, headers={"x-signature": good, "idempotency-key": "k3"})).status_code == 401


async def test_invalid_json_is_400(w):
    _, token = await w.add_trigger(auth_method="none", auth_config={})
    assert (await w.post(token, None, raw=b"{nope", sign=False)).status_code == 400


async def test_unknown_token_is_404(w):
    assert (await w.post("wh_nope", {"a": 1})).status_code == 404


# ---- encrypted secrets ----------------------------------------------------------------------------


async def test_create_returns_secret_once_and_stores_only_ciphertext(w):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/workflows/{w.wf}/webhooks", json={"workflow_id": str(w.wf), "payload_mapping": {"who": "name"}})
        assert r.status_code == 200, r.text
        created = r.json()
        secret = created["secret"]
        assert secret
        listed = await c.get(f"/api/v1/workflows/{w.wf}/webhooks")
        got = await c.get(f"/api/v1/webhooks/{created['id']}")
    assert "secret" not in json.dumps(listed.json()) and "secret" not in json.dumps(got.json())
    async with w.maker() as db:
        trigger = await db.get(WebhookTrigger, uuid.UUID(created["id"]))
        assert trigger.created_by == w.user
    config = trigger.auth_config
    assert "secret" not in config and secret not in json.dumps(config)
    assert webhook_secrets.decrypt_secret(config["secret_enc"]) == secret
    raw = b'{"name":"Ada"}'
    r = await w.post(trigger.url_token, None, raw=raw, headers={"x-signature": w.sign(raw, secret)})
    assert r.status_code == 202


async def test_supplied_secret_is_sealed_and_api_key_is_encrypted(w):
    async with w.maker() as db:
        svc = wts.WebhookTriggerService(db)
        t1, shown = await svc.create_trigger(tenant_id=w.tenant, created_by=w.user, workflow_id=w.wf,
                                             auth_config={"secret": "mine"})
        t2, _ = await svc.create_trigger(tenant_id=w.tenant, created_by=w.user, workflow_id=w.wf,
                                         auth_method="api_key", auth_config={"key": "k-123", "header": "X-K"})
        await db.commit()
    assert shown == "mine" and "secret" not in t1.auth_config and "key" not in t2.auth_config
    assert webhook_secrets.decrypt_secret(t2.auth_config["key_enc"]) == "k-123"
    ok = await w.post(t2.url_token, {"name": "x"}, headers={"x-k": "k-123"}, sign=False)
    bad = await w.post(t2.url_token, {"name": "x"}, headers={"x-k": "nope", "idempotency-key": "z"}, sign=False)
    assert ok.status_code == 202 and bad.status_code == 401


def test_mask_and_legacy_plaintext_resolution():
    assert webhook_secrets.mask("abcdefgh") == "****efgh" and webhook_secrets.mask(None) == ""
    assert webhook_secrets.resolve_secret({"secret": "old"}, "secret", "secret_enc") == "old"
    assert webhook_secrets.resolve_secret({}, "secret", "secret_enc") is None


async def test_ip_whitelist_path_is_kept(w):
    async with w.maker() as db:
        svc = wts.WebhookTriggerService(db)
        trig = SimpleNamespace(auth_method="ip_whitelist", auth_config={}, allowed_ip_addresses=["10.0.0.1"])
        assert await svc._verify_auth(trig, b"", {}, "10.0.0.1") is True
        assert await svc._verify_auth(trig, b"", {}, "10.0.0.2") is False
        assert await svc._verify_auth(SimpleNamespace(auth_method="ip_whitelist", auth_config={}, allowed_ip_addresses=None),
                                      b"", {}, "10.0.0.1") is False


async def test_rate_limit_counts_in_sql_and_returns_429(w):
    _, token = await w.add_trigger(rate_limit_per_hour=1)
    assert (await w.post(token, {"name": "a"})).status_code == 202
    assert (await w.post(token, {"name": "b"})).status_code == 429
    async with w.maker() as db:
        assert (await db.execute(select(func.count()).select_from(WebhookDelivery))).scalar() >= 2


# ---- idempotency ----------------------------------------------------------------------------------


async def test_same_delivery_twice_is_one_run_and_flags_deduplicated(w):
    tid, token = await w.add_trigger()
    first = await w.post(token, {"name": "Ada"})
    second = await w.post(token, {"name": "Ada"})
    assert first.status_code == second.status_code == 202
    assert first.json()["deduplicated"] is False and second.json()["deduplicated"] is True
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert len(await w.executions()) == 1 and len(w.engine.starts) == 1
    [row] = await w.executions()
    assert row.status == "running" and row.trigger == "webhook" and row.user_id == w.user
    key = hashlib.sha256(json.dumps({"name": "Ada"}).encode()).hexdigest()
    bucket = int(T0.timestamp() // BUCKET)
    assert row.id == uuid.uuid5(tid, f"{key}:{bucket}")


TRUSTED = {"idempotency_header": "Idempotency-Key", "trust_idempotency_header": True}


def trusted_config(**extra):
    return {"secret_enc": webhook_secrets.encrypt_secret(SECRET), **TRUSTED, **extra}


async def test_unsigned_header_cannot_turn_a_replayed_body_into_a_new_run(w):
    _, token = await w.add_trigger()
    first = await w.post(token, {"name": "Ada"})
    replay = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "attacker-1"})
    again = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "attacker-2"})
    assert replay.json()["deduplicated"] is True and again.json()["deduplicated"] is True
    assert replay.json()["execution_id"] == again.json()["execution_id"] == first.json()["execution_id"]
    assert len(await w.executions()) == 1 and len(w.engine.starts) == 1
    # Naming the header alone is not enough either: the trigger must vouch for it.
    _, named = await w.add_trigger(auth_config={"secret_enc": webhook_secrets.encrypt_secret(SECRET),
                                                "idempotency_header": "Idempotency-Key"})
    a = await w.post(named, {"name": "Bo"})
    b = await w.post(named, {"name": "Bo"}, headers={"Idempotency-Key": "other"})
    assert b.json()["deduplicated"] is True and a.json()["execution_id"] == b.json()["execution_id"]


async def test_trusted_header_key_is_permanent_and_is_the_run_id(w):
    tid, token = await w.add_trigger(auth_config=trusted_config())
    first = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "evt-1"})
    assert uuid.UUID(first.json()["execution_id"]) == uuid.uuid5(tid, "evt-1")
    assert w.engine.starts[0]["key"] == f"workflow:{uuid.uuid5(tid, 'evt-1')}"
    w.clock = datetime.fromtimestamp(T0.timestamp() + 3 * BUCKET, tz=timezone.utc)
    later = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "evt-1"})
    assert later.json()["execution_id"] == first.json()["execution_id"] and later.json()["deduplicated"] is True
    assert len(await w.executions()) == 1


async def test_jmespath_key_is_permanent(w):
    tid, token = await w.add_trigger(auth_config={"secret_enc": webhook_secrets.encrypt_secret(SECRET),
                                                  "idempotency_key_path": "event.id"})
    body = {"name": "Ada", "event": {"id": "ev_9"}}
    first = await w.post(token, body)
    w.clock = datetime.fromtimestamp(T0.timestamp() + 3 * BUCKET, tz=timezone.utc)
    again = await w.post(token, {**body, "extra": 1})
    assert uuid.UUID(first.json()["execution_id"]) == uuid.uuid5(tid, "ev_9")
    assert again.json()["execution_id"] == first.json()["execution_id"] and again.json()["deduplicated"] is True


async def test_configured_header_name_is_used(w):
    tid, token = await w.add_trigger(auth_config=trusted_config(idempotency_header="X-GitHub-Delivery"))
    r = await w.post(token, {"name": "Ada"}, headers={"X-GitHub-Delivery": "gh-1"})
    assert uuid.UUID(r.json()["execution_id"]) == uuid.uuid5(tid, "gh-1")


async def test_body_hash_next_bucket_starts_a_new_run_same_bucket_dedupes(w):
    _, token = await w.add_trigger()
    first = await w.post(token, {"name": "Ada"})
    w.clock = datetime.fromtimestamp(T0.timestamp() + 600, tz=timezone.utc)
    same = await w.post(token, {"name": "Ada"})
    assert same.json()["execution_id"] == first.json()["execution_id"] and same.json()["deduplicated"] is True
    w.clock = datetime.fromtimestamp(T0.timestamp() + 25 * 3600, tz=timezone.utc)
    nxt = await w.post(token, {"name": "Ada"})
    assert nxt.json()["execution_id"] != first.json()["execution_id"] and nxt.json()["deduplicated"] is False
    assert len(await w.executions()) == 2


async def test_delivery_straddling_the_bucket_boundary_still_dedupes_within_24h(w):
    _, token = await w.add_trigger()
    boundary = (int(T0.timestamp() // BUCKET) + 1) * BUCKET
    w.clock = datetime.fromtimestamp(boundary - 30, tz=timezone.utc)
    first = await w.post(token, {"name": "Ada"})
    w.clock = datetime.fromtimestamp(boundary + 30, tz=timezone.utc)
    second = await w.post(token, {"name": "Ada"})
    assert second.json()["execution_id"] == first.json()["execution_id"]
    assert second.json()["deduplicated"] is True and len(await w.executions()) == 1
    w.clock = datetime.fromtimestamp(boundary - 30 + BUCKET + 60, tz=timezone.utc)  # 24 h + 1 min after the first
    third = await w.post(token, {"name": "Ada"})
    assert third.json()["deduplicated"] is False and len(await w.executions()) == 2


async def test_trusted_headers_with_different_keys_and_identical_body_are_two_runs(w):
    _, token = await w.add_trigger(auth_config=trusted_config())
    a = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "k-1"})
    b = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "k-2"})
    assert a.json()["execution_id"] != b.json()["execution_id"] and len(await w.executions()) == 2


async def test_concurrent_identical_deliveries_are_one_run_not_a_500(w):
    import asyncio

    _, token = await w.add_trigger()
    results = await asyncio.gather(*[w.post(token, {"name": "Ada"}) for _ in range(4)])
    assert [r.status_code for r in results] == [202] * 4, [r.text for r in results]
    assert len({r.json()["execution_id"] for r in results}) == 1 and len(await w.executions()) == 1


async def test_filter_mismatch_is_200_and_starts_nothing(w):
    _, token = await w.add_trigger(filter_expression="payload['kind'] == 'paid'")
    r = await w.post(token, {"kind": "open", "name": "Ada"})
    assert r.status_code == 200 and await w.executions() == [] and w.engine.starts == []
    assert (await w.post(token, {"kind": "paid", "name": "Ada"})).status_code == 202


async def test_engine_outage_is_accepted_and_the_queued_row_is_started_by_the_reconciler(w):
    _, token = await w.add_trigger()
    w.engine.down = True
    r = await w.post(token, {"name": "Ada"})
    assert r.status_code == 202 and r.json()["start_pending"] is True
    [row] = await w.executions()
    assert row.status == "queued" and str(row.id) == r.json()["execution_id"]
    # The sender's retry is a duplicate of that row, not a dropped delivery: the reconciler starts it.
    retry = await w.post(token, {"name": "Ada"})
    assert retry.status_code == 202 and retry.json()["deduplicated"] is True
    w.engine.down = False
    async with w.maker() as db:
        stuck = await wer.find_stuck(db, timedelta(seconds=-1))
    assert [ref.ref_id for ref in stuck] == [row.id]
    started = await wer.start_missing(stuck[0])
    assert started.status == "started"
    [row] = await w.executions()
    assert row.status == "running"


async def test_flag_off_keeps_the_pending_row_with_the_deterministic_id(w):
    async with w.maker() as db:
        tenant = await db.get(Tenant, w.tenant)
        tenant.settings = {"features": {}}
        await db.commit()
    tid, token = await w.add_trigger(auth_config=trusted_config())
    r = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "e1"})
    again = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "e1"})
    [row] = await w.executions()
    assert row.status == "pending" and row.id == uuid.uuid5(tid, "e1") and row.user_id == w.user
    assert again.json()["deduplicated"] is True and w.engine.starts == []


# ---- ownership ------------------------------------------------------------------------------------


async def test_create_without_created_by_is_refused(w):
    async with w.maker() as db:
        with pytest.raises(wts.TriggerUnowned):
            await wts.WebhookTriggerService(db).create_trigger(tenant_id=w.tenant, created_by=None, workflow_id=w.wf)


async def test_unowned_trigger_is_422_and_logged(w):
    _, token = await w.add_trigger(created_by=None)
    r = await w.post(token, {"name": "Ada"})
    assert r.status_code == 422 and r.json()["error"] == "trigger_unowned"
    assert await w.executions() == []
    async with w.maker() as db:
        rows = (await db.execute(select(WebhookDelivery))).scalars().all()
    assert [(d.response_status, d.error_message) for d in rows] == [(422, "trigger_unowned")]
    # An unauthenticated caller learns nothing about ownership.
    assert (await w.post(token, {"name": "Ada"}, headers={"x-signature": "sha256=bad"})).status_code == 401


# ---- agent triggers -------------------------------------------------------------------------------


async def test_agent_trigger_starts_a_fleet_job_and_dedupes(w):
    tid, token = await w.add_trigger(workflow_id=None, agent_id=w.agent, auth_config=trusted_config())
    r = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "a-1"})
    assert r.status_code == 202 and r.json()["queued"] is True and r.json()["deduplicated"] is False
    job_id = uuid.UUID(r.json()["job_id"])
    assert job_id == uuid.uuid5(tid, "a-1") and w.fleet_starts == [job_id]
    async with w.maker() as db:
        job = await db.get(BackgroundJob, job_id)
    assert job.trigger == "webhook" and job.user_id == w.user and job.agent_id == w.agent
    assert w.fe.is_engine_job(job) and job.input_data["who"] == "Ada"
    again = await w.post(token, {"name": "Ada"}, headers={"Idempotency-Key": "a-1"})
    assert again.json()["deduplicated"] is True and again.json()["job_id"] == r.json()["job_id"]
    assert w.fleet_starts == [job_id]


async def test_agent_trigger_at_the_concurrency_limit_is_202_not_queued(w):
    _, token = await w.add_trigger(workflow_id=None, agent_id=w.agent)
    assert (await w.post(token, {"name": "a"}, headers={"Idempotency-Key": "a-1"})).json()["queued"] is True
    r = await w.post(token, {"name": "b"}, headers={"Idempotency-Key": "a-2"})
    assert r.status_code == 202 and r.json()["queued"] is False and "job_id" not in r.json()
    async with w.maker() as db:
        assert (await db.execute(select(func.count()).select_from(BackgroundJob))).scalar() == 1
