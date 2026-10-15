"""U12 / AE10, R13, R20: kill the engine worker mid-turn through the chat routes; the run recovers.

Same stack as the engine smoke (harness.py). The test drives only the backend's browser routes:
create a conversation (selections, not an agent), submit a message whose scripted model streams
text and then calls a gated non-idempotent write, and consume the watch stream the way the browser
does (a reconnecting reader sending Last-Event-ID). While the write is blocked the worker is
SIGKILLed; it is restarted, the gate opens, and the reader must receive the run's continuation and
the answer. The final transcript has one user entry, one tool effect, the answer and ledger rows,
and a duplicate submit with the same Idempotency-Key returns the original submission.
Skipped unless ONIVA_E2E=1.
"""
import json
import signal
import threading
import time

import httpx
import pytest

from tests.e2e.harness import (AUTH, PRE_TEXT, WRITE_ANSWER, api, new_conversation, sql, wait_for)

pytestmark = pytest.mark.e2e


def parse_sse(buf: str):
    """Yield (id, event, data) for each complete frame in `buf`; return (frames, rest)."""
    frames, rest = [], buf
    while "\n\n" in rest:
        block, rest = rest.split("\n\n", 1)
        fid = event = None
        data = []
        for line in block.splitlines():
            if line.startswith("id:"):
                fid = line[3:].strip()
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data or event:
            frames.append((fid, event, "\n".join(data)))
    return frames, rest


class Watcher(threading.Thread):
    """The browser's stream reader: reconnects forever, resuming from the last event id it saw."""

    def __init__(self, w, conversation_id: str):
        super().__init__(daemon=True)
        self.w, self.conv = w, conversation_id
        self.frames: list[tuple] = []  # (id, event, data) in arrival order
        self.text = ""
        self.last_id: str | None = None
        self.connects: list[str | None] = []  # the Last-Event-ID sent on each connect
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def run(self):
        url = f"{self.w.backend_url}/api/v1/engine/conversations/{self.conv}/watch"
        while not self.stop.is_set():
            headers = dict(AUTH)
            if self.last_id is not None:
                headers["Last-Event-ID"] = self.last_id
            self.connects.append(self.last_id)
            buf = ""
            try:
                with httpx.stream("GET", url, headers=headers, timeout=httpx.Timeout(10, read=5)) as r:
                    if r.status_code != 200:
                        time.sleep(0.3)
                        continue
                    for chunk in r.iter_text():
                        buf += chunk.replace("\r\n", "\n")
                        frames, buf = parse_sse(buf)
                        with self.lock:
                            for f in frames:
                                self.frames.append(f)
                                self.text += f[2]
                                if f[0] is not None:
                                    self.last_id = f[0]
                        if self.stop.is_set():
                            return
            except (httpx.HTTPError, OSError):
                time.sleep(0.3)

    def seen(self, needle: str) -> bool:
        with self.lock:
            return needle in self.text


def snapshot_of(w, conversation_id: str) -> dict:
    """A fresh load: the first snapshot frame of a new watch (no cursor)."""
    buf = ""
    with httpx.stream("GET", f"{w.backend_url}/api/v1/engine/conversations/{conversation_id}/watch",
                      headers=AUTH, timeout=httpx.Timeout(30, read=30)) as r:
        assert r.status_code == 200, r.read()
        for chunk in r.iter_text():
            buf += chunk.replace("\r\n", "\n")
            frames, buf = parse_sse(buf)
            for _id, event, data in frames:
                if event == "snapshot":
                    body = json.loads(data)
                    return body.get("snapshot", body)
    raise AssertionError("no snapshot frame")


def test_chat_engine_recovery(world):
    w = world
    worker = w.start_worker(1)
    conv = new_conversation(w)

    watcher = Watcher(w, conv)
    watcher.start()
    try:
        wait_for(lambda: watcher.frames, "the first snapshot", 30)

        key = "idem-chat-recovery-1"
        first = api(w, "POST", f"/{conv}/submissions", headers={"Idempotency-Key": key},
                    json={"text": "RUN_CHAT_WRITE please", "requestId": "req-chat-1"})
        assert first.status_code == 202, first.text

        wait_for(lambda: sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status = 'in_flight'"),
                 "the write to be in flight", 60)
        wait_for(lambda: watcher.seen(PRE_TEXT), "the streamed text before the tool call", 30)
        assert not w.effects.exists()
        before_kill = len(watcher.connects)

        worker.kill(signal.SIGKILL)
        # While the worker is down the browser route cannot reach the engine: a retry is refused, not lost.
        down = api(w, "POST", f"/{conv}/submissions", headers={"Idempotency-Key": key},
                   json={"text": "RUN_CHAT_WRITE please", "requestId": "req-chat-1"})
        assert down.status_code >= 500, down.text
        w.gate.write_text("open")  # the backend-side effect completes without the dead caller
        wait_for(lambda: w.effects.exists() and sql(
            w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write' AND status != 'in_flight'"),
            "the write to complete and store its outcome", 30)

        w.start_worker(2)
        wait_for(lambda: watcher.seen(WRITE_ANSWER), "the continuation to reach the reconnected reader", 90)

        # The reader reconnected with a cursor, and what it received continues rather than restarts.
        assert len(watcher.connects) > before_kill
        assert any(c is not None for c in watcher.connects[before_kill:]), watcher.connects
        assert watcher.text.count(PRE_TEXT) >= 1

        # A duplicate submit with the same key across the restart returns the same submission.
        again = api(w, "POST", f"/{conv}/submissions", headers={"Idempotency-Key": key},
                    json={"text": "RUN_CHAT_WRITE please", "requestId": "req-chat-1"})
        assert again.status_code == 202, again.text
        # Same submission (its status has moved on from active to done; identity must not change).
        strip = lambda d: {k: v for k, v in d.items() if k != "status"}  # noqa: E731
        assert strip(again.json()) == strip(first.json()), (first.json(), again.json())
        assert first.json()["status"] == "active" and again.json()["status"] == "done"
    finally:
        watcher.stop.set()

    # ---- the final transcript (a fresh load) ----------------------------------------------------
    view = snapshot_of(w, conv)
    entries = view["entries"]
    kinds = [e["kind"] for e in entries]
    users = [e for e in entries if e["kind"] == "user"]
    assert len(users) == 1 and users[0]["payload"].get("requestId") == "req-chat-1", entries
    results = [e for e in entries if e["kind"] == "tool_result" and e["payload"].get("toolName") == "e2e_write"]
    assert len(results) == 1, kinds
    assistant_text = json.dumps([e["payload"] for e in entries if e["kind"] == "assistant"])
    assert PRE_TEXT in assistant_text and WRITE_ANSWER in assistant_text
    assert w.effects.read_text().splitlines() == ["effect chat"], "the write must happen exactly once"
    assert len(sql(w, "SELECT 1 FROM tool_invocations WHERE tool = 'e2e_write'")) == 1
    assert view["live"]["message"] is None  # the run is over

    ledger = wait_for(lambda: (lambda rows: rows if len(rows) >= 2 else None)(sql(
        w, "SELECT input_tokens, output_tokens, cost_usd FROM llm_usage_records ORDER BY input_tokens")),
        "ledger rows", 30)
    assert {(21, 9), (13, 7)} <= {(a, b) for a, b, _ in ledger}, ledger
    assert all(float(c) > 0 for _, _, c in ledger)

    cost = api(w, "GET", f"/{conv}/cost")
    assert cost.status_code == 200, cost.text
    body = cost.json()
    assert body["inputTokens"] == sum(a for a, _, _ in ledger)
    assert body["outputTokens"] == sum(b for _, b, _ in ledger)
    assert body["totalUsd"] == pytest.approx(sum(float(c) for *_, c in ledger))
