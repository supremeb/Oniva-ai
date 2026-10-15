"""U11 / R18 / AE10: a real engine worker, a scripted model and the real backend routers, end to end.

Processes: a throwaway Postgres 14+ cluster (engine storage), the backend (``backend_app.py``: the
real /internal router + browser routes on file SQLite, uvicorn), and ``npm run worker:test`` in
``engine/`` (scripted model, backend policy and gateway). The test drives only the backend's
browser routes. Skipped unless ONIVA_E2E=1.

Scenario 1: create, submit, SSE to completion; the read tool ran once, usage was recorded and priced
from the scripted tokens, the answer is in the stream; replaying the invoke key does not run the tool
again. Scenario 2: the worker is SIGKILLed while a non-idempotent write is held in flight in the
backend; the write completes and its outcome is stored; the restarted worker adopts that outcome, the
run completes, and the effect happened exactly once.
"""
import signal

import pytest

from tests.e2e.harness import (ANSWER, ANSWER_TOKENS, GATEWAY_TOKEN, READ_TOKENS, SESSION, WRITE_ANSWER,
                               api, new_conversation, sql, wait_for, watch_until)
import httpx

pytestmark = pytest.mark.e2e


def test_engine_smoke(world):
    w = world
    worker = w.start_worker(1)

    # ---- scenario 1: tool call, usage, answer -------------------------------------------------
    conv = new_conversation(w)
    r = api(w, "POST", f"/{conv}/submissions", json={"text": "RUN_KYC please", "requestId": "req-kyc"})
    assert r.status_code == 202, r.text
    stream = watch_until(w, conv, ANSWER)
    assert "kyc_verification" in stream

    rows = sql(w, "SELECT idempotency_key, tool, status, outcome FROM tool_invocations WHERE tool = 'kyc_verification'")
    assert len(rows) == 1 and rows[0][2] == "completed", rows
    key = rows[0][0]

    def usage():
        got = sql(w, "SELECT input_tokens, output_tokens, cost_usd, provider_name FROM llm_usage_records "
                     "ORDER BY input_tokens")
        return got if len(got) >= 2 else None
    ledger = wait_for(usage, "the usage ledger rows", 30)
    assert (READ_TOKENS[0], READ_TOKENS[1]) in [(a, b) for a, b, *_ in ledger]
    assert (ANSWER_TOKENS[0], ANSWER_TOKENS[1]) in [(a, b) for a, b, *_ in ledger]
    assert all(float(c) > 0 for _, _, c, _ in ledger), ledger  # priced from the table, not zero

    # Replaying the same invoke key returns the stored outcome without running the tool again.
    run_id, call_id = key.split(":", 1)
    body = {"args": {"full_name": "Ada Lovelace", "dob": "1815-12-10", "country": "UK"},
            "runId": run_id, "toolCallId": call_id, "tenantId": str(w.tenant), "projectId": str(w.project),
            "conversationId": conv}
    again = httpx.post(f"{w.backend_url}/internal/tools/kyc_verification/invoke", json=body, timeout=30,
                       headers={"Authorization": f"Bearer {GATEWAY_TOKEN}", "Idempotency-Key": key,
                                "X-Engine-Session": SESSION})
    assert again.status_code == 200, again.text
    assert len(sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'kyc_verification'")) == 1

    # ---- scenario 2: SIGKILL during a blocked write, restart, no duplicate ---------------------
    conv2 = new_conversation(w)
    assert api(w, "POST", f"/{conv2}/submissions", json={"text": "RUN_WRITE now", "requestId": "req-write"}
               ).status_code == 202
    wait_for(lambda: sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status = 'in_flight'"),
             "the write to be in flight in the backend", 60)
    assert not w.effects.exists()  # blocked on the gate: nothing happened yet

    worker.kill(signal.SIGKILL)
    w.gate.write_text("open")  # the backend's effect, detached from the dead caller, now completes
    wait_for(lambda: w.effects.exists() and sql(
        w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status != 'in_flight'"),
        "the in-flight write to finish and store its outcome", 30)

    w.start_worker(2)
    watch_until(w, conv2, WRITE_ANSWER, 90)

    assert w.effects.read_text().splitlines() == ["effect once"], "the write must happen exactly once"
    inv = sql(w, "SELECT idempotency_key FROM tool_invocations WHERE tool = 'e2e_write'")
    assert len(inv) == 1, inv
    # Conversations stay bound to their user/tenant through the proxy.
    assert sql(w, "SELECT count(*) FROM engine_conversations")[0][0] == 2
