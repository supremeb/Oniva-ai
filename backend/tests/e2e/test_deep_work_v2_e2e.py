"""U22 / R16-R20: Deep Work with the subagent contract v2 flag on completes end to end (fake mode).

Same stack as the recovery e2e, plus ``ENABLE_ENGINE_SUBAGENT_V2=true`` on the backend (switches
``typed_results,verifier,steer_worker``; ``fork`` stays off because the scripted model keys on the last message,
and a forked reflector would see the plan title) and ``ENGINE_COMPLETE_TASK=1`` / ``ENGINE_STEER_WORKER=1`` on the
engine worker. The scripted model ends the workers and the reflector with ``complete_task`` and runs the
``verifier`` once before ``dw_finalize``.

Skipped unless ONIVA_E2E=1.
"""
import json

import pytest

from tests.e2e import dw_script as script
from tests.e2e.harness import stack
from tests.e2e.test_chat_engine_recovery import snapshot_of
from tests.e2e.test_deep_work_engine_recovery import dw, ensure_worker, report_of, run_completes

pytestmark = pytest.mark.e2e

PREFIX = "v2"
NAME = "V2"
QUERY = f"DWQ_{NAME} compare the two vendors"
TITLE = f"DWPLAN_{NAME}"
IDS = [f"{PREFIX}-w1", f"{PREFIX}-w2"]
QUERIES = [f"q-{PREFIX}-w1", f"q-{PREFIX}-w2"]
EMPTY_VERDICT = {"unsupported_claims": [], "missing_citations": [], "broken_citations": []}


def _rule(when, steps):
    return script._rule(when, steps)


def v2_rules() -> list:
    """The plain research scenario with typed endings, labels and a verifier step."""
    call, text = script._call, script._text
    plan = {"title": TITLE, "deliverable": None, "sub_queries": [
        {"worker_id": w, "sub_query": q, "tools": ["web_search"]} for w, q in zip(IDS, QUERIES)]}
    rules = [
        _rule({"role": "user", "includes": f"User Query: {QUERY}"},
              [text(json.dumps({"action": "proceed", "enriched_query": QUERY}))]),
        _rule({"role": "user", "includes": QUERY}, [text(json.dumps(plan))]),
    ]
    for r in script.scenario_rules(TITLE, IDS, QUERIES, PREFIX):
        when, steps = r["when"], r["respond"]["steps"]
        if when.get("tool") == "dw_start_round":  # every worker call carries its label
            steps = [call("research_worker", {"prompt": f"WORKER {w}: {q}", "label": w}) for w, q in zip(IDS, QUERIES)]
        elif when.get("tool") in ("dw_save_learnings", "dw_record_reflection") and steps[0]["type"] == "text":
            steps = [call("complete_task", {"result": json.loads(steps[0]["text"])})]  # typed ending
        rules.append({"when": when, "respond": {**r["respond"], "steps": steps}})
    rules += [
        # verifier child: read the report, report no problems
        _rule({"role": "user", "includes": f"VERIFY {PREFIX}"}, [call("dw_get_report", {})]),
        _rule({"role": "tool_result", "tool": "dw_get_report"}, [call("complete_task", {"result": EMPTY_VERDICT})]),
        # orchestrator: after the last section, verify once, then finalize
        _rule({"role": "tool_result", "tool": "dw_write_section", "includes": "Method"},
              [call("verifier", {"prompt": f"VERIFY {PREFIX} the draft"})]),
        _rule({"role": "tool_result", "tool": "verifier"}, [call("dw_finalize", {})]),
        *script.finish_rules(),
    ]
    return rules


@pytest.fixture(scope="module")
def world_v2():
    extra = {"ENABLE_ENGINE_SUBAGENT_V2": "true", "ENGINE_SUBAGENT_V2_SWITCHES": "typed_results,verifier,steer_worker"}
    with stack(extra_env=extra, worker_env={"ENGINE_COMPLETE_TASK": "1", "ENGINE_STEER_WORKER": "1"}) as w:
        w.script.write_text(json.dumps({"rules": v2_rules()}))  # the worker has not started yet
        yield w


def start(w) -> str:
    body = {"query": QUERY, "project_id": str(w.project)}
    clarified = dw(w, "POST", "/engine/clarify", json=body)
    assert clarified.status_code == 200, f"{clarified.text}\nbackend:\n{w.backend.tail()}"
    sid = clarified.json()["session_id"]
    planned = dw(w, "POST", "/engine/plan", json={**body, "session_id": sid, "enriched_query": QUERY})
    assert planned.status_code == 200, planned.text
    started = dw(w, "POST", "/engine", json={**body, "session_id": sid, "plan": planned.json()["raw_plan"],
                                              "enriched_query": QUERY})
    assert started.status_code == 200, f"{started.text}\nbackend:\n{w.backend.tail()}"
    return sid, started.json()["conversation_id"]


def test_flag_on_run_completes_with_typed_workers_labels_and_a_verifier(world_v2):
    w = world_v2
    ensure_worker(w, 1)
    session_id, conv = start(w)
    run_completes(w, conv)

    view = snapshot_of(w, conv)
    children = view["subagents"]
    assert len(children) == 4, children  # two workers, the reflector and the verifier
    stream = json.dumps(view["entries"])
    for wid in IDS:  # labels reach the subagent calls
        assert f'"label": "{wid}"' in stream.replace('":"', '": "') or f'"label":"{wid}"' in stream, wid

    # the parent got each worker's validated payload: the JSON the Deep Work card has always parsed (R20)
    results = [e["payload"] for e in view["entries"] if e["kind"] == "tool_result"]
    worker_results = [json.loads(r["content"]) for r in results if r.get("toolName") == "research_worker"]
    assert len(worker_results) == 2
    typed = [r for r in results if r.get("toolName") in ("research_worker", "reflector", "verifier")]
    assert len(typed) == 4 and all(isinstance(r.get("details", {}).get("result"), dict) for r in typed), typed
    for r in worker_results:
        assert {"summary", "learning_count", "source_count", "top_findings", "sources"} <= set(r)
        assert r["learning_count"] == 2 and len(r["sources"]) == 2
    verifier = [r for r in results if r.get("toolName") == "verifier"]
    assert len(verifier) == 1 and json.loads(verifier[0]["content"]) == EMPTY_VERDICT

    finalize = [r for r in results if r.get("toolName") == "dw_finalize"]
    assert len(finalize) == 1 and "warnings" not in json.loads(finalize[0]["content"])
    report = report_of(w, session_id)
    assert report.count("## Findings") == 1 and report.count("## Sources") == 1
