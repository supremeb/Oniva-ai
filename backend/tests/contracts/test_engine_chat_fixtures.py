"""Engine-path chat fixtures (U12, KTD6) and the guard that every legacy chat fixture has one.

``fixtures/chat_engine_<scenario>.json`` holds the conversation view the real engine produced (scripted
model, same conversation as the legacy fixture ``chat_<scenario>.json``), normalized with the recorder.
The source is ``frontend/lib/engine/__fixtures__/chat_views.json``, which the adapter tests
(``viewToMessages.test.ts``) render and compare to the legacy ``message_parts``; this test keeps the two
copies from drifting and checks each view still carries what the legacy fixture persisted.

    ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts/test_engine_chat_fixtures.py   # re-record
"""
import json
from pathlib import Path

import pytest

from .recorder import check_or_record, normalize

FIXTURES = Path(__file__).parent / "fixtures"
FRONTEND_VIEWS = Path(__file__).resolve().parents[3] / "frontend/lib/engine/__fixtures__/chat_views.json"
SCENARIOS = ["text", "tool_call", "error"]


def legacy_chat_fixtures() -> list[str]:
    return sorted(p.stem.removeprefix("chat_") for p in FIXTURES.glob("chat_*.json")
                  if not p.stem.startswith("chat_engine_"))


def test_every_legacy_chat_fixture_has_an_engine_counterpart():
    missing = [s for s in legacy_chat_fixtures() if not (FIXTURES / f"chat_engine_{s}.json").exists()]
    assert not missing, f"legacy chat fixtures without a chat_engine_* counterpart: {missing}"
    assert set(legacy_chat_fixtures()) <= set(SCENARIOS)  # a new legacy scenario must be driven below too


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_engine_view_fixture_is_current(scenario):
    if not FRONTEND_VIEWS.exists():
        pytest.skip("frontend fixtures not checked out")
    view = json.loads(FRONTEND_VIEWS.read_text())[f"chat_{scenario}"]
    check_or_record(f"chat_engine_{scenario}", {"legacy": f"chat_{scenario}", "view": normalize(view)})


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_engine_view_matches_legacy_conversation(scenario):
    engine = json.loads((FIXTURES / f"chat_engine_{scenario}.json").read_text())
    legacy = json.loads((FIXTURES / f"chat_{scenario}.json").read_text())
    entries = engine["view"]["entries"]
    assert engine["legacy"] == f"chat_{scenario}"
    users = [e for e in entries if e["kind"] == "user"]
    assert len(users) == 1
    assert users[0]["payload"]["content"] == "What is the contract?"

    assistant_text = "".join(
        b["text"] for e in entries if e["kind"] == "assistant"
        for b in e["payload"]["message"]["blocks"] if b["type"] == "text")
    if legacy["persisted_messages"]:
        assert assistant_text == legacy["persisted_messages"][0]["response"]
    else:
        # Deliberate difference (KTD6): legacy persisted nothing on error; the engine keeps the partial
        # text and a run_error entry.
        assert assistant_text == "partial "
        assert [e["kind"] for e in entries if e["kind"] == "run_error"] == ["run_error"]
    if scenario == "tool_call":
        results = [e for e in entries if e["kind"] == "tool_result"]
        names = [r["payload"]["toolName"] for r in results]
        assert names == [m["toolName"] for m in legacy["persisted_messages"][0]["message_parts"]
                         if m["type"] == "tool_call"]  # engine results carry the tool name legacy omitted
