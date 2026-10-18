"""U22 / AE4, R10: webhook deliveries start workflow runs on the engine exactly once.

Same stack as test_workflow_engine_recovery.py. A trigger is created through the real route (the HMAC secret is
shown once), deliveries are signed over the raw bytes, and the workflow's HTTP node posts to the counting target:

  * a delivery whose bytes a re-serialization would change is accepted; one changed byte is rejected 401;
  * the same signed body delivered again (whatever unsigned ``Idempotency-Key`` header it carries), while the run is held and again after a worker kill, starts no
    second run (``deduplicated: true``, the same execution id) and the node's POST happens once;
  * a different body starts a second run;
  * the database holds no plaintext secret.

Skipped unless ONIVA_E2E=1.
"""
import hashlib
import hmac
import json
import signal
import time

import httpx
import pytest

from tests.e2e.harness import sql
from tests.e2e.workflow_helpers import (
    api, close_gates, dones, edge, ensure_worker, exec_row, http_node, in_flight, node, open_gate, save_workflow,
    starts, wait_for, wait_status)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_gates(world):
    close_gates(world)
    yield


def create_trigger(w, workflow_id: str) -> tuple[str, str]:
    """``(url_token, secret)`` of a new HMAC trigger on the workflow."""
    r = api(w, "POST", f"/workflows/{workflow_id}/webhooks", json={"workflow_id": workflow_id, "auth_method": "hmac"})
    assert r.status_code == 200, f"{r.text}\nbackend:\n{w.backend.tail()}"
    body = r.json()
    return body["url"].rsplit("/", 1)[-1], body["secret"]


def deliver(w, token: str, secret: str, raw: bytes, key: str | None = None, signed: bytes | None = None) -> httpx.Response:
    sig = hmac.new(secret.encode(), signed if signed is not None else raw, hashlib.sha256).hexdigest()
    headers = {"X-Signature": f"sha256={sig}", "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    return httpx.post(f"{w.backend_url}/api/v1/webhooks/{token}", content=raw, headers=headers, timeout=30)


def runs_of(w, workflow_id: str) -> list[str]:
    return [r[0] for r in sql(w, "SELECT id FROM workflow_executions WHERE workflow_id = ? AND trigger = 'webhook'",
                              workflow_id.replace("-", ""))]


def test_webhook_delivery_is_signed_over_raw_bytes_and_starts_one_run(world):
    w = world
    worker = ensure_worker(w, 1)
    wf = save_workflow(w, [node("s", "start"), http_node(w, "h", "hold", "wh-1"), node("o", "output")],
                       [edge("s", "h"), edge("h", "o")])
    token, secret = create_trigger(w, wf)
    stored = json.dumps(sql(w, "SELECT auth_config FROM webhook_triggers WHERE url_token = ?", token))
    assert secret not in stored and "secret_enc" in stored, "the database must hold no plaintext secret"

    raw = b'{"z": 1,\n  "a":   [1,2 ],"b":"x"}'  # whitespace and key order a re-serialization would change
    assert json.dumps(json.loads(raw)).encode() != raw
    flipped = raw.replace(b'"x"', b'"y"')
    assert len(flipped) == len(raw)
    rejected = deliver(w, token, secret, flipped, key="d-1", signed=raw)
    assert rejected.status_code == 401, rejected.text
    assert runs_of(w, wf) == [], "a rejected delivery started a run"

    first = deliver(w, token, secret, raw, key="d-1")
    assert first.status_code == 202 and first.json()["deduplicated"] is False, first.text
    execution = first.json()["execution_id"]
    wait_for(lambda: in_flight(w, "wh-1"), "the run's POST in flight", 90)

    again = deliver(w, token, secret, raw, key="d-1")
    assert again.status_code == 202 and again.json() == {**again.json(), "deduplicated": True,
                                                         "execution_id": execution}, again.text

    worker.kill(signal.SIGKILL)
    during_outage = deliver(w, token, secret, raw, key="d-1")
    assert during_outage.json()["deduplicated"] is True and during_outage.json()["execution_id"] == execution
    w.start_worker(2)
    open_gate(w)

    row = wait_status(w, execution)
    time.sleep(3)
    late = deliver(w, token, secret, raw, key="d-replayed-with-another-header")
    assert late.json()["deduplicated"] is True and late.json()["execution_id"] == execution, late.text
    assert runs_of(w, wf) == [execution.replace("-", "")], "the same delivery key started more than one run"
    assert starts(w, "wh-1") == 1 and dones(w, "wh-1") == 1, "the node's POST did not happen exactly once"
    assert row["trigger"] == "webhook" and [t["status"] for t in row["trace"]] == ["success"] * 3, row["trace"]

    other = deliver(w, token, secret, b'{"z": 2}')
    assert other.status_code == 202 and other.json()["deduplicated"] is False
    assert other.json()["execution_id"] != execution
    wait_status(w, other.json()["execution_id"])
    assert starts(w, "wh-1") == 2 and len(runs_of(w, wf)) == 2
    assert exec_row(w, execution)["completed_at"] == row["completed_at"], "a duplicate delivery re-settled the run"
