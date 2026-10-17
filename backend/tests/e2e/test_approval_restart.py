"""Approvals and policy, end to end (plan 2026-10-17-002, U13 / AE1, AE2, AE4, AE6, AE9, Rule of Two).

Same stack as the chat recovery test (harness.py): Postgres for the engine, the real backend routers on
SQLite, a scripted model, a real worker. The backend knows five more identities (``X-E2E-User``: admin,
viewer, outsider = a member of another project, stranger = a user of another tenant) and a fake E2B
sandbox that records every run in ``exec.log``.

What is real: the engine's approval item and its durability, the /internal/tools/authorize policy, the
approval ledger, the decision route and its RBAC, the execution guard in the gateway, the inbox, and the
signed audit chain (``AuditChain`` with the local-dev KMS, verified through the test-only
``/e2e/audit`` route in the backend process). What is not: the audit table is SQLite here, so its
database-level immutability trigger (an ``UPDATE`` by the app role fails) and the concurrent-append lock
are proven by ``tests/postgres/test_audit_immutability.py`` and ``tests/test_audit_chain.py``, not here.
Skipped unless ONIVA_E2E=1.
"""
import json
import signal
import time

import httpx
import pytest

from tests.e2e import approval_script as scr
from tests.e2e.harness import (AUTH, GATEWAY_TOKEN, SESSION, WRITE_ANSWER, api, as_user, mark_unattended, new_conversation,
                               restart_backend, sql, sql_write, wait_for)
from tests.e2e.test_chat_engine_recovery import snapshot_of

pytestmark = pytest.mark.e2e

PAUSE_MS = 6000  # an approved execute_python invoke is held this long on the wire (ENGINE_TEST_INVOKE_PAUSE)


@pytest.fixture(scope="module")
def approvals_world(world):
    """The module's stack with the worker's execute_python invoke delayed, so a test can slip a call in."""
    world.worker_env["ENGINE_TEST_INVOKE_PAUSE"] = json.dumps({"execute_python": PAUSE_MS})
    world.gate.write_text("open")  # e2e_write is a gated write: never block it here
    world.worker = world.start_worker(1)
    return world


# --- helpers ------------------------------------------------------------------------------------


def restart_worker(w):
    w.worker.kill(signal.SIGKILL)
    w.worker = w.start_worker(w.worker_n + 1)


def ensure_worker(w):
    if w.worker.p.poll() is not None:
        restart_worker(w)


def submit(w, conv: str, text: str, who: str = "member", request_id: str | None = None):
    r = api(w, "POST", f"/{conv}/submissions", headers=as_user(who),
            json={"text": text, "requestId": request_id or f"req-{abs(hash((conv, text)))}"})
    assert r.status_code == 202, r.text
    return r


def engine_approvals(w, conv: str) -> list[dict]:
    return snapshot_of(w, conv).get("approvals", {}).get("items", [])


def pending_item(w, conv: str, tool: str | None = None) -> dict:
    def find():
        items = [i for i in engine_approvals(w, conv)
                 if i["status"] == "pending" and (tool is None or i["toolName"] == tool)]
        return items[0] if items else None
    return wait_for(find, f"a pending engine approval for {tool or 'any tool'}", 60)


def ledger(w, tool_task_id: str) -> dict | None:
    rows = sql(w, "SELECT status, decision, tool_name, rule, approver_role, reason, run_id, tool_call_id, "
                  "arguments_hash, consumed_at, taint_sources, kind FROM tool_approvals WHERE tool_task_id = ?",
               tool_task_id)
    if not rows:
        return None
    keys = ("status", "decision", "tool_name", "rule", "approver_role", "reason", "run_id", "tool_call_id",
            "arguments_hash", "consumed_at", "taint_sources", "kind")
    return dict(zip(keys, rows[0]))


def decide(w, who: str, conv: str, task: str, decision: str = "approve") -> httpx.Response:
    return api(w, "POST", f"/{conv}/approvals/{task}/decision", headers=as_user(who), json={"decision": decision})


def inbox(w, who: str, path: str = "") -> httpx.Response:
    return httpx.get(f"{w.backend_url}/api/v1/inbox{path}", headers={**AUTH, **as_user(who)}, timeout=30)


def inbox_tasks(w, who: str) -> list[str]:
    r = inbox(w, who)
    assert r.status_code == 200, r.text
    return [i["actions"][0]["endpoint"].rsplit("/", 2)[-2] for i in r.json()["items"] if i["kind"] == "approval"
            and i["actions"]]


def inbox_ids(w, who: str) -> list[str]:
    r = inbox(w, who)
    assert r.status_code == 200, r.text
    return [i["source"]["id"] for i in r.json()["items"] if i["kind"] == "approval"]


def audit(w) -> dict:
    r = httpx.get(f"{w.backend_url}/e2e/audit", timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def decided_entries(w) -> list[dict]:
    return [e for e in audit(w)["entries"] if e["action"] == "approval_decided"]


def exec_log(w) -> list[str]:
    return w.dw.lines(w.dw_dir / "exec.log")


def final_text(w, conv: str) -> str:
    view = snapshot_of(w, conv)
    return json.dumps([e["payload"] for e in view["entries"] if e["kind"] == "assistant"])


def wait_answer(w, conv: str, needle: str, timeout: float = 90):
    try:
        wait_for(lambda: needle in final_text(w, conv), f"the assistant to say {needle!r}", timeout)
    except AssertionError as e:
        raise AssertionError(f"{e}\ntranscript: {final_text(w, conv)}\nexec.log: {exec_log(w)}\n"
                             f"backend:\n{w.backend.tail(30)}\nworker:\n{w.worker.tail(30)}") from None


def invoke(w, conv: str, row: dict, args: dict, key_suffix: str = "") -> httpx.Response:
    """The gateway as the engine calls it, for an existing approval's run and tool call."""
    run_id, call_id = row["run_id"], row["tool_call_id"]
    return httpx.post(
        f"{w.backend_url}/internal/tools/execute_python/invoke", timeout=30,
        json={"args": args, "runId": run_id, "toolCallId": call_id, "tenantId": str(w.tenant),
              "projectId": str(w.project), "conversationId": conv},
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}", "Idempotency-Key": f"{run_id}:{call_id}",
                 "X-Engine-Session": SESSION})


# --- AE1, AE2, AE6, AE9: wait through a restart of both processes, only an admin decides ---------


def test_approval_waits_across_restarts_and_only_an_admin_decides(approvals_world):
    w = approvals_world
    conv = new_conversation(w)
    submit(w, conv, "RUN_PYTHON please")
    item = pending_item(w, conv, "execute_python")
    task = item["toolTaskId"]
    row = wait_for(lambda: ledger(w, task), "the ledger row", 30)
    assert row["status"] == "pending" and row["kind"] == "tool_call"
    assert row["approver_role"] == "admin" and row["rule"] == "requires_approval"
    assert row["arguments_hash"]
    assert exec_log(w) == []  # nothing has run

    # The requester sees it in their own inbox; an admin sees it with a decide action.
    assert inbox_tasks(w, "admin") == [task]
    assert inbox(w, "member", "/count").json()["total"] == 1
    assert inbox(w, "admin", "/count").json()["total"] == 1

    # ---- the whole control plane goes down and comes back --------------------------------------
    w.worker.kill(signal.SIGKILL)
    restart_backend(w)
    restart_worker(w)

    assert [i for i in engine_approvals(w, conv) if i["toolTaskId"] == task][0]["status"] == "pending"
    assert ledger(w, task)["status"] == "pending"
    assert inbox_tasks(w, "admin") == [task]
    assert inbox(w, "member", "/count").json()["total"] == 1
    assert exec_log(w) == []

    # ---- AE2: nobody who may not decide can ----------------------------------------------------
    assert decide(w, "member", conv, task).status_code == 403  # the requester: admin-level tool
    assert decide(w, "viewer", conv, task).status_code == 403  # same tenant, project viewer
    assert decide(w, "outsider", conv, task).status_code == 403  # member of another project
    assert decide(w, "stranger", conv, task).status_code == 404  # another tenant
    assert [i for i in engine_approvals(w, conv) if i["toolTaskId"] == task][0]["status"] == "pending"
    assert ledger(w, task)["status"] == "pending"
    assert decided_entries(w) == []
    assert exec_log(w) == []

    # ---- AE9 (arguments binding), then AE1: the admin approves ----------------------------------
    # Straight at the gateway with no approval row: refused, the sandbox sees nothing.
    ghost = httpx.post(
        f"{w.backend_url}/internal/tools/execute_python/invoke", timeout=30,
        json={"args": {"code": "print('no approval')"}, "runId": "ghost-run", "toolCallId": "ghost-call",
              "tenantId": str(w.tenant), "projectId": str(w.project), "conversationId": conv},
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}", "Idempotency-Key": "ghost-run:ghost-call",
                 "X-Engine-Session": SESSION})
    assert ghost.status_code == 403 and ghost.json()["error"] == "approval_required", ghost.text
    assert exec_log(w) == []

    ok = decide(w, "admin", conv, task)
    assert ok.status_code == 200, f"{ok.text}\n{w.backend.tail(60)}"
    assert ledger(w, task)["status"] == "approved"
    assert inbox_tasks(w, "admin") == []  # decided: gone from the inbox

    # The approved call is on the wire (held by the test harness). A modified call for the same tool
    # call is refused before it reaches the sandbox.
    wait_for(lambda: '"invoke_pending"' in (w.tmp / f"worker{w.worker_n}.log").read_text()
             and "execute_python" in (w.tmp / f"worker{w.worker_n}.log").read_text(),
             "the approved invoke to be on the wire", 30)
    tampered = invoke(w, conv, ledger(w, task), {"code": "print('tampered')"})
    assert tampered.status_code == 403 and tampered.json()["error"] == "approval_arguments_mismatch", tampered.text
    assert exec_log(w) == []

    # The approved invoke arrives, runs once, and the model receives the output.
    wait_answer(w, conv, scr.PY_ANSWER, 90)
    assert len(exec_log(w)) == 1 and scr.PY_STDOUT in exec_log(w)[0], exec_log(w)
    assert ledger(w, task)["consumed_at"] is not None

    # The idempotency record is gone (say, pruned) but the approval is spent: a replay is refused.
    final_row = ledger(w, task)
    sql_write(w, "DELETE FROM tool_invocations WHERE tool = 'execute_python'")
    replay = invoke(w, conv, final_row, {"code": scr.PY_CODE})
    assert replay.status_code == 403 and replay.json()["error"] == "approval_consumed", replay.text
    assert len(exec_log(w)) == 1

    # ---- AE6: the chain across both restarts is intact ------------------------------------------
    chain = audit(w)
    assert chain["verify"]["ok"] is True and chain["verify"]["first_break"] is None, chain["verify"]
    seqs = [e["seq"] for e in chain["entries"]]
    assert seqs == list(range(1, len(seqs) + 1)), seqs
    actions = [e["action"] for e in chain["entries"]]
    assert actions.count("approval_requested") == 1 and actions.count("approval_decided") == 1, actions
    decided = [e for e in chain["entries"] if e["action"] == "approval_decided"][0]
    assert decided["actor_role"] == "admin" and decided["decision"] == "approve"


# --- AE4: expiry and abort -----------------------------------------------------------------------


def test_expired_and_aborted_approvals_close_their_ledger_rows(approvals_world):
    w = approvals_world
    ensure_worker(w)
    ran = len(exec_log(w))  # the sandbox runs of earlier tests
    # A one-hour approval TTL for the tenant, then the engine clock moves two hours on.
    sql_write(w, "UPDATE tenants SET settings = ?",
              json.dumps({"features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True},
                          "approvals": {"ttl_hours": 1}}))
    conv = new_conversation(w)
    submit(w, conv, "RUN_PYTHON expire")
    item = pending_item(w, conv, "execute_python")
    task = item["toolTaskId"]
    assert wait_for(lambda: ledger(w, task), "the ledger row", 30)["status"] == "pending"
    assert 3_000_000 < item["expiresAt"] - item["requestedAt"] <= 3_600_000

    clock = httpx.post(f"{w.engine_url}/v1/test/clock", json={"advanceMs": 2 * 3_600_000}, timeout=10,
                       headers={"Authorization": f"Bearer {w.worker_env['ENGINE_SERVICE_TOKEN']}"})
    assert clock.status_code == 200, clock.text
    wait_answer(w, conv, scr.PY_EXPIRED_ANSWER, 60)  # the call ended approval_expired
    assert len(exec_log(w)) == ran

    # The inbox read reconciles the ledger with the engine: the item disappears and the row is expired.
    def reconciled():
        return task not in inbox_tasks(w, "admin") and ledger(w, task)["status"] == "expired"
    wait_for(reconciled, "the ledger row to close as expired", 90, every=2)

    # An abort while an approval is pending closes the row cancelled and removes the inbox item.
    sql_write(w, "UPDATE tenants SET settings = ?",
              json.dumps({"features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True}}))
    conv2 = new_conversation(w)
    submit(w, conv2, "RUN_PYTHON abort")
    task2 = pending_item(w, conv2, "execute_python")["toolTaskId"]
    wait_for(lambda: ledger(w, task2), "the second ledger row", 30)
    assert task2 in inbox_tasks(w, "admin")
    assert api(w, "POST", f"/{conv2}/abort", json={"reason": "e2e"}).status_code == 200
    assert ledger(w, task2)["status"] == "cancelled"
    assert task2 not in inbox_tasks(w, "admin")
    assert decide(w, "admin", conv2, task2).status_code in (404, 409, 410)
    assert len(exec_log(w)) == ran

    chain = audit(w)
    assert chain["verify"]["ok"] is True, chain["verify"]
    actions = [e["action"] for e in chain["entries"]]
    assert "approval_expired" in actions and "approval_cancelled" in actions, actions


# --- Rule of Two ----------------------------------------------------------------------------------


def test_rule_of_two_attended_unattended_and_subagent(approvals_world):
    w = approvals_world
    ensure_worker(w)
    w.effects.unlink(missing_ok=True)

    # Attended: browse_url, then an external write parks with the untrusted reason; the user decides.
    conv = new_conversation(w)
    submit(w, conv, "RUN_TAINT please")
    item = pending_item(w, conv, "e2e_write")
    row = wait_for(lambda: ledger(w, item["toolTaskId"]), "the rule-of-two ledger row", 30)
    assert row["rule"] == "rule_of_two" and "untrusted content" in row["reason"], row
    assert row["approver_role"] == "member" and row["taint_sources"], row
    assert not w.effects.exists()
    assert decide(w, "viewer", conv, item["toolTaskId"]).status_code == 403
    assert decide(w, "member", conv, item["toolTaskId"]).status_code == 200  # the chat's own user may decide
    wait_answer(w, conv, WRITE_ANSWER, 90)
    assert w.effects.read_text().splitlines() == ["effect tainted"]

    # Unattended: the same sequence has nobody to ask, so the write is blocked and the run completes.
    w.effects.unlink(missing_ok=True)
    conv2 = new_conversation(w)
    mark_unattended(w, conv2)  # no human owns this run
    submit(w, conv2, "RUN_TAINT unattended")
    wait_answer(w, conv2, scr.TAINT_BLOCKED_ANSWER, 90)
    assert not w.effects.exists()
    assert not [i for i in engine_approvals(w, conv2)]
    assert any(e["action"] == "tool_policy_block" for e in audit(w)["entries"])

    # A subagent doing the same parks its approval for the chat's user, and that user can decide it.
    w.effects.unlink(missing_ok=True)
    conv3 = new_conversation(w)
    submit(w, conv3, "RUN_SUBAGENT_TAINT please")
    parked = wait_for(
        lambda: sql(w, "SELECT tool_task_id, requested_by, rule, engine_conversation_id FROM tool_approvals "
                       "WHERE tool_name = 'e2e_write' AND status = 'pending'"),
        "the subagent's rule-of-two approval", 90)
    task3, requester, rule, child = parked[0]
    assert rule == "rule_of_two"
    assert child and child != conv3.replace("-", "") and child != conv3, (child, conv3)  # raised by the child
    owner = sql(w, "SELECT user_id FROM engine_conversations WHERE user_id IS NOT NULL")
    assert requester in {r[0] for r in owner}, (requester, owner)  # the chat's user, not nobody
    assert decide(w, "member", conv3, task3).status_code == 200
    wait_answer(w, conv3, scr.SUBAGENT_ANSWER, 90)
    assert w.effects.read_text().splitlines() == ["effect tainted"]

    chain = audit(w)
    assert chain["verify"]["ok"] is True, chain["verify"]
    assert [e["seq"] for e in chain["entries"]] == list(range(1, len(chain["entries"]) + 1))
