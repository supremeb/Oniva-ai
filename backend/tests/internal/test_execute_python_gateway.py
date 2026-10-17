"""U6 / KTD7, AE9: execute_python through the gateway runs only after an approved, matching, unused approval."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.skills.registry import SkillRegistry
from app.internal_api import execution
from app.internal_api.execution import ToolApprovalRefused, ToolRateLimiter, execute_tool
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.services.approval_service import ApprovalService
from app.services.tool_policy import canonical_args_hash
from app.skills.execute_python import tool as ep

TENANT = uuid.uuid4()
RUN, CALL = "run_1", "call_1"
CODE = {"code": "print(6 * 7)"}


class FakeHandle:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    async def upload_files(self, files):
        self.sandbox.uploads.append(files)

    async def execute_code(self, code, timeout=60):
        self.sandbox.runs.append((code, timeout))
        await asyncio.sleep(0.01)
        return SimpleNamespace(success=True, stdout="42", stderr="", error=None,
                               output_files=None, duration_seconds=0.1)


class FakeSandbox:
    def __init__(self):
        self.runs, self.uploads, self.torn_down = [], [], 0

    async def get_or_create(self, session_id, worker_id=None, **kw):
        return FakeHandle(self)

    async def teardown(self, session_id, worker_id=None):
        self.torn_down += 1


@pytest.fixture
async def world(test_db, monkeypatch):
    maker = async_sessionmaker(test_db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(execution, "_session_factory", lambda: maker)

    async def no_tenant_context(session, tenant_id):  # set_config is Postgres-only
        return None

    monkeypatch.setattr("app.core.database.set_tenant_context", no_tenant_context)
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    await test_db.commit()

    sandbox = FakeSandbox()
    monkeypatch.setattr(ep, "_executor", lambda: sandbox)
    settings = SimpleNamespace(ENABLE_SANDBOX=True, E2B_API_KEY="k")
    monkeypatch.setattr(ep, "get_settings", lambda: settings)

    class W:
        pass

    w = W()
    w.db, w.sandbox, w.settings, w.maker = test_db, sandbox, settings, maker

    async def approval(status="approved", decision="approve", args=CODE, run=RUN, call=CALL, consumed=None):
        row = await ApprovalService(test_db).record_pending(
            tenant_id=TENANT, conversation_id=None, tool_task_id=None, run_id=run, tool_call_id=call,
            tool_name="execute_python", arguments=args, arguments_hash=canonical_args_hash(args),
            approver_role="admin")
        row.status, row.decision, row.consumed_at = status, decision if status != "pending" else None, consumed
        await test_db.commit()
        return row

    async def run(args=CODE, call=CALL, run_id=RUN, **kw):
        return await execute_tool(
            "execute_python", args, tenant_id=str(TENANT), project_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()), conversation_id=str(uuid.uuid4()), run_id=run_id,
            tool_call_id=call, limiter=ToolRateLimiter(None), **kw)

    async def reload(row):
        async with maker() as s:
            return (await s.execute(select(ToolApproval).where(ToolApproval.id == row.id))).scalar_one()

    w.approval, w.run, w.reload = approval, run, reload
    return w


@pytest.fixture(autouse=True)
def _real_skill(monkeypatch):
    """Route SkillRegistry.invoke_skill for execute_python straight to the real handler."""
    real = SkillRegistry.invoke_skill

    async def invoke(ctx, name, **kw):
        if name == "execute_python":
            return await ep.execute_python(ctx, **kw)
        return await real(ctx, name, **kw)

    monkeypatch.setattr(SkillRegistry, "invoke_skill", staticmethod(invoke))


async def test_approved_row_runs_code_once_and_is_consumed(world):
    row = await world.approval()
    out = await world.run()
    assert not out.is_error
    assert '"stdout": "42"' in out.content
    assert world.sandbox.runs == [("print(6 * 7)", 60)]
    assert world.sandbox.torn_down == 1
    assert (await world.reload(row)).consumed_at is not None


@pytest.mark.parametrize("status,decision", [
    ("pending", None), ("deciding", "approve"), ("denied", "deny"), ("expired", None),
])
async def test_not_approved_never_reaches_sandbox(world, status, decision):
    await world.approval(status=status, decision=decision)
    with pytest.raises(ToolApprovalRefused) as e:
        await world.run()
    assert e.value.status == 403 and e.value.body == {"error": "approval_required"}
    assert world.sandbox.runs == []


async def test_no_ledger_row_is_refused(world):
    with pytest.raises(ToolApprovalRefused) as e:
        await world.run()
    assert e.value.code == "approval_required"
    assert world.sandbox.runs == []


async def test_row_for_another_tool_call_is_not_reused(world):
    await world.approval(call="call_other")
    with pytest.raises(ToolApprovalRefused) as e:
        await world.run(call="call_mine")
    assert e.value.code == "approval_required"
    assert world.sandbox.runs == []


async def test_changed_arguments_are_refused_and_not_consumed(world):
    row = await world.approval()
    with pytest.raises(ToolApprovalRefused) as e:
        await world.run(args={"code": "import os; os.system('rm -rf /')"})
    assert e.value.code == "approval_arguments_mismatch"
    assert world.sandbox.runs == []
    assert (await world.reload(row)).consumed_at is None
    assert not (await world.run()).is_error  # the approved call still runs


async def test_second_invoke_after_success_is_consumed(world):
    await world.approval()
    await world.run()
    with pytest.raises(ToolApprovalRefused) as e:
        await world.run()
    assert e.value.code == "approval_consumed"
    assert len(world.sandbox.runs) == 1


async def test_concurrent_invokes_run_exactly_once(world):
    await world.approval()
    results = await asyncio.gather(world.run(), world.run(), return_exceptions=True)
    assert sum(isinstance(r, ToolApprovalRefused) for r in results) == 1
    assert len(world.sandbox.runs) == 1


async def test_sandbox_disabled_is_error_and_does_not_consume(world):
    world.settings.ENABLE_SANDBOX = False
    row = await world.approval()
    out = await world.run()
    assert out.is_error and "sandbox" in out.content.lower()
    assert world.sandbox.runs == []
    assert (await world.reload(row)).consumed_at is None
    world.settings.ENABLE_SANDBOX = True  # sandbox available again: the approved call retries once
    assert not (await world.run()).is_error
    assert (await world.reload(row)).consumed_at is not None


async def test_sandbox_start_failure_is_error_and_does_not_consume(world, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("SANDBOX_PROXY_URL is not set")

    monkeypatch.setattr(world.sandbox, "get_or_create", boom)
    row = await world.approval()
    out = await world.run()
    assert out.is_error and "SANDBOX_PROXY_URL" in out.content
    assert world.sandbox.runs == []
    assert (await world.reload(row)).consumed_at is None


async def test_executed_with_nonzero_exit_is_consumed(world, monkeypatch):
    async def failing(self, code, timeout=60):
        self.sandbox.runs.append((code, timeout))
        return SimpleNamespace(success=False, stdout="", stderr="boom", error="exit 1",
                               output_files=None, duration_seconds=0.1)

    monkeypatch.setattr(FakeHandle, "execute_code", failing)
    row = await world.approval()
    out = await world.run()
    assert not out.is_error and '"success": false' in out.content
    assert len(world.sandbox.runs) == 1
    assert (await world.reload(row)).consumed_at is not None


async def test_outside_gateway_refuses_and_creates_nothing(world):
    from app.agents.skills.context import SkillContext

    ctx = SkillContext(user_id="u", tenant_id=str(TENANT), session_id="s", metadata={}, db=None)
    out = await ep.execute_python(ctx, "print(1)")
    assert out == {"success": False, "error": "Code execution needs an approved request. Use engine chat."}
    assert world.sandbox.runs == []
    rows = (await world.db.execute(select(ToolApproval))).scalars().all()
    assert rows == []


async def test_gateway_timeout_after_code_started_consumes_the_approval(world, monkeypatch):
    async def hang(self, code, timeout=60):
        self.sandbox.runs.append((code, timeout))
        await asyncio.sleep(30)

    monkeypatch.setattr(FakeHandle, "execute_code", hang)
    row = await world.approval()
    out = await world.run(timeout_ms=50)
    assert out.is_error and out.details["error"] == "timeout" and out.executed
    assert len(world.sandbox.runs) == 1
    assert (await world.reload(row)).consumed_at is not None
    with pytest.raises(ToolApprovalRefused) as e:  # the same approval cannot run the code again
        await world.run()
    assert e.value.code == "approval_consumed"


async def test_gateway_timeout_before_code_started_leaves_the_approval_unconsumed(world, monkeypatch):
    async def slow_start(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(world.sandbox, "get_or_create", slow_start)
    row = await world.approval()
    out = await world.run(timeout_ms=50)
    assert out.is_error and out.details["error"] == "timeout" and not out.executed
    assert world.sandbox.runs == []
    assert (await world.reload(row)).consumed_at is None
