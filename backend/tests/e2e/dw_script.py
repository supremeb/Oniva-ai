"""The scripted model for the Deep Work e2e scenarios (test-only).

The engine's scripted model answers each request from the LAST message alone, so every scenario's
rules key on strings only that scenario produces. Three scenarios, each a plan of two workers:

  recovery  rec-w1 (search finishes) and rec-w2 (its web_search is held in flight on the gate), then a
            reflection that stops, two sections and ``dw_finalize``. A killed engine replays the
            held search (a read), so the run resumes without a marker.
  restart   res-w1 and res-w2 finish; then the ORCHESTRATOR calls ``e2e_hold`` (a non-replayable write)
            held on the write gate. A kill there leaves an ``interrupted`` result in the root
            conversation: the card's "resumed after restart" marker. (A kill inside a worker's
            replay-safe tool leaves no marker in the root view; that is the ``recovery`` run.)
  steer     st-w1 and st-w2 (search held); a ``DW_REDIRECT`` message ends the research round early.
  cancel    can-w1 and can-w2 each start a sandbox (``shell_exec``) and then block in a held search.

Use ``query(name)`` as the user's question; the intake and plan answers are scripted as well.
"""
import json
from typing import Any, Dict, List

SCENARIOS = {
    "recovery": {"prefix": "rec", "sandbox": False, "hold": "search"},
    "restart": {"prefix": "res", "sandbox": False, "hold": "write"},
    "steer": {"prefix": "st", "sandbox": False, "hold": "search"},
    "cancel": {"prefix": "can", "sandbox": True, "hold": "search"},
}
FINAL_TEXT = "Deep Work complete (e2e-dw-done)."
GATED = "GATED"


def query(name: str) -> str:
    return f"DWQ_{name.upper()} compare the two vendors"


def title(name: str) -> str:
    return f"DWPLAN_{name.upper()}"


def worker_ids(name: str) -> List[str]:
    p = SCENARIOS[name]["prefix"]
    return [f"{p}-w1", f"{p}-w2"]


def sub_queries(name: str) -> List[str]:
    """The first worker's search completes at once; the second one blocks on the gate."""
    p = SCENARIOS[name]["prefix"]
    if name == "cancel":
        return [f"q-{p}-w1-{GATED}", f"q-{p}-w2-{GATED}"]
    if SCENARIOS[name]["hold"] == "write":
        return [f"q-{p}-w1", f"q-{p}-w2"]  # nothing held in a worker
    return [f"q-{p}-w1", f"q-{p}-w2-{GATED}"]


def plan(name: str) -> Dict[str, Any]:
    return {
        "title": title(name),
        "sub_queries": [
            {"worker_id": w, "sub_query": q, "tools": ["web_search"]}
            for w, q in zip(worker_ids(name), sub_queries(name))
        ],
        "deliverable": None,
    }


def _call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_call", "name": name, "arguments": arguments}


def _text(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def _rule(when: Dict[str, Any], steps: List[Dict[str, Any]], tokens=(30, 10)) -> Dict[str, Any]:
    return {"when": when, "respond": {"steps": steps, "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1]}}}


def _learning(worker: str, n: int) -> Dict[str, Any]:
    return {
        "fact": f"Finding {n} for {worker}", "source_url": f"https://example.com/{worker}/{n}",
        "confidence": 0.8, "source_title": f"Source {worker}", "source_type": "web", "quote": f"Quote {n} for {worker}",
    }


def _scenario_rules(name: str) -> List[Dict[str, Any]]:
    cfg = SCENARIOS[name]
    ids, queries = worker_ids(name), sub_queries(name)
    rules: List[Dict[str, Any]] = [
        # --- orchestrator: start the round with both workers in one message ---
        _rule({"role": "user", "includes": title(name)}, [_call("dw_start_round", {
            "round": 1, "workers": [{"worker_id": w, "sub_query": q} for w, q in zip(ids, queries)]})]),
        _rule({"role": "tool_result", "tool": "dw_start_round", "includes": ids[0]}, [
            _call("research_worker", {"prompt": f"WORKER {w}: {q}"}) for w, q in zip(ids, queries)]),
        # --- orchestrator: after the round (or a steer that ended it) reflect; "restart" holds a write first ---
        _rule({"role": "tool_result", "tool": "research_worker", "includes": f"saved {cfg['prefix']}-"},
              [_call("e2e_hold", {"note": f"{cfg['prefix']}-root"})] if cfg["hold"] == "write" else
              [_call("reflector", {"prompt": f"REFLECT {cfg['prefix']} round 1"})]),
        _rule({"role": "tool_result", "tool": "reflector", "includes": f"reflected {cfg['prefix']}"}, [
            _call("dw_write_section", {"heading": "Findings", "markdown": f"Researched {queries[0]} and {queries[1]}."})]),
        # --- reflector ---
        _rule({"role": "user", "includes": f"REFLECT {cfg['prefix']}"}, [_call("dw_get_learnings", {})]),
        _rule({"role": "tool_result", "tool": "dw_get_learnings", "includes": f"{cfg['prefix']}-w1"}, [
            _call("dw_record_reflection", {"round": 1, "score": 90, "gaps": [], "should_continue": False})]),
        _rule({"role": "tool_result", "tool": "dw_record_reflection", "includes": '"round": 1'},
              [_text(json.dumps({"score": 90, "gaps": [], "round": 1, "continuing": False,
                                 "note": f"reflected {cfg['prefix']}"}))]),
    ]
    if cfg["hold"] == "write":  # any outcome of the held write, "interrupted by a restart" included
        rules.append(_rule({"role": "tool_result", "tool": "e2e_hold"},
                           [_call("reflector", {"prompt": f"REFLECT {cfg['prefix']} round 1"})]))
    for w, q in zip(ids, queries):
        steps = [_call("shell_exec", {"command": f"echo {w}", "worker_id": w})] if cfg["sandbox"] else [
            _call("web_search", {"query": q})]
        rules.append(_rule({"role": "user", "includes": f"WORKER {w}:"}, steps))
        if cfg["sandbox"]:
            rules.append(_rule({"role": "tool_result", "tool": "shell_exec", "includes": f"echo {w}"},
                               [_call("web_search", {"query": q})]))
        save = _call("dw_save_learnings", {"worker_id": w, "learnings": [_learning(w, 1), _learning(w, 2)]})
        rules.append(_rule({"role": "tool_result", "tool": "web_search", "includes": f"query\": \"{q}\""}, [save]))
        rules.append(_rule({"role": "tool_result", "tool": "dw_save_learnings", "includes": f'"worker_id": "{w}"'}, [
            _text(json.dumps({"summary": f"saved {w}", "learning_count": 2, "source_count": 2,
                              "top_findings": [f"Finding 1 for {w}"],
                              "sources": [{"title": f"Source {w}", "url": f"https://example.com/{w}/{n}", "type": "web"}
                                          for n in (1, 2)]}))]))
    return rules


def build() -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = [
        # --- intake and plan (the user's question picks the scenario) ---
        *[_rule({"role": "user", "includes": f"User Query: {query(n)}"},
                [_text(json.dumps({"action": "proceed", "enriched_query": query(n)}))]) for n in SCENARIOS],
        *[_rule({"role": "user", "includes": query(n)}, [_text(json.dumps(plan(n)))]) for n in SCENARIOS],
        # --- a steer ends the round: the orchestrator moves on to reflection ---
        _rule({"role": "user", "includes": "DW_REDIRECT"}, [_call("reflector", {"prompt": "REFLECT st round 1"})]),
    ]
    for n in SCENARIOS:
        rules += _scenario_rules(n)
    rules += [
        _rule({"role": "tool_result", "tool": "dw_write_section", "includes": "Method"}, [_call("dw_finalize", {})]),
        _rule({"role": "tool_result", "tool": "dw_write_section"}, [
            _call("dw_write_section", {"heading": "Method", "markdown": "Two parallel workers and one reflection round."})]),
        _rule({"role": "tool_result", "tool": "dw_finalize"}, [_text(FINAL_TEXT)]),
    ]
    return {"rules": rules}
