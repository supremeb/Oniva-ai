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
from typing import Any, Dict, List, Optional

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
    return scenario_rules(title(name), worker_ids(name), sub_queries(name), SCENARIOS[name]["prefix"],
                          sandbox=SCENARIOS[name]["sandbox"], hold=SCENARIOS[name]["hold"])


def scenario_rules(plan_title: str, ids: List[str], queries: List[str], prefix: str, *, sandbox: bool = False,
                   hold: Any = None, findings_md: Any = None) -> List[Dict[str, Any]]:
    """The rules of one research scenario, from explicit parts (the eval harness builds its own scenarios).

    ``plan_title`` is the plan's title (it is in the orchestrator's first message), ``ids`` the worker ids (each must
    start with ``prefix``), ``queries`` one search per worker. ``hold`` is ``"search"``/``"write"`` (the recovery
    scenarios) or ``None`` for a plain run. ``findings_md`` replaces the text of the "Findings" section.
    """
    cfg = {"prefix": prefix, "sandbox": sandbox, "hold": hold}
    rules: List[Dict[str, Any]] = [
        # --- orchestrator: start the round with both workers in one message ---
        _rule({"role": "user", "includes": plan_title}, [_call("dw_start_round", {
            "round": 1, "workers": [{"worker_id": w, "sub_query": q} for w, q in zip(ids, queries)]})]),
        _rule({"role": "tool_result", "tool": "dw_start_round", "includes": ids[0]}, [
            _call("research_worker", {"prompt": f"WORKER {w}: {q}"}) for w, q in zip(ids, queries)]),
        # --- orchestrator: after the round (or a steer that ended it) reflect; "restart" holds a write first ---
        _rule({"role": "tool_result", "tool": "research_worker", "includes": f"saved {cfg['prefix']}-"},
              [_call("e2e_hold", {"note": f"{cfg['prefix']}-root"})] if cfg["hold"] == "write" else
              [_call("reflector", {"prompt": f"REFLECT {cfg['prefix']} round 1"})]),
        _rule({"role": "tool_result", "tool": "reflector", "includes": f"reflected {cfg['prefix']}"}, [
            _call("dw_write_section", {"heading": "Findings", "markdown": findings_md
                                       or f"Researched {queries[0]} and {queries[1]}."})]),
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
    rules += finish_rules()
    return {"rules": rules}


def finish_rules() -> List[Dict[str, Any]]:
    """After the Findings section: a Method section, ``dw_finalize`` and the closing text (scenario-independent)."""
    return [
        _rule({"role": "tool_result", "tool": "dw_write_section", "includes": "Method"}, [_call("dw_finalize", {})]),
        _rule({"role": "tool_result", "tool": "dw_write_section"}, [
            _call("dw_write_section", {"heading": "Method", "markdown": "Two parallel workers and one reflection round."})]),
        _rule({"role": "tool_result", "tool": "dw_finalize"}, [_text(FINAL_TEXT)]),
    ]


# --- subagent contract scenarios (U24 eval suite) -------------------------------------------------------------------
#
# ``subagent_rules`` builds one complete Deep Work script for the eval tasks of ``evals/tasks/dw_subagents``. The scripted
# model sees only the last message, so the same scenario is written twice: for the baseline arm (``v2=False``: workers
# end with a JSON text answer, no verifier) and for the v2 arm (``v2=True``: workers end with ``complete_task``, the
# orchestrator labels its calls and runs the verifier once before ``dw_finalize``). A scenario ``spec`` (all optional):
#
#   prose        {worker_id: "prose" | "partial"}   the worker answers in text that is not the summary object
#   nudge        "complete" (default) | "ignore"    what a v2 worker does when the engine asks it for complete_task
#   urls         {worker_id: [url, ...]}            sources the worker saves (default example.com/<id>/1 and /2)
#   urls_v2      {worker_id: [url, ...]}            the same for the v2 arm (workers told to avoid sibling topics)
#   bait         "broken_index" | "uncited"         a draft the baseline finalizes as is; the v2 verifier flags it
#   findings_fixed   text that replaces the bad draft
#   steer        {worker, message, query, keyword}  v2 arm: ``steer_worker`` in the first round's batch
#   gap          {worker_id, sub_query, urls, urls_v2}   a second round with one more worker

NUDGE_MARKER = "Call the complete_task tool now"
STEER_MARKER = "Message from the orchestrator"
SUBAGENT_FINDINGS = "Researched {a} and {b} [1][2]."
UNCITED_BACKGROUND = (
    "Background. The market has several vendors with different tiers, discounts and contract terms, and buyers "
    "usually compare them on price, support and the length of the commitment before they choose, which is why "
    "the findings above matter."
)


def _urls_for(worker: str, spec: Dict[str, Any], v2: bool) -> List[str]:
    gap = spec.get("gap") or {}
    if gap.get("worker_id") == worker:
        src = (gap.get("urls_v2") if v2 else None) or gap.get("urls") or {}
    else:
        src = (spec.get("urls_v2") if v2 else None) or spec.get("urls") or {}
    return list(src.get(worker) or [f"https://example.com/{worker}/{n}" for n in (1, 2)])


def _learning_at(worker: str, url: str, n: int) -> Dict[str, Any]:
    return {"fact": f"Finding {n} for {worker}", "source_url": url, "confidence": 0.8,
            "source_title": f"Source {worker}", "source_type": "web", "quote": f"Quote {n} for {worker}"}


def _summary(worker: str, urls: List[str], text: Optional[str] = None) -> Dict[str, Any]:
    return {"summary": text or f"saved {worker}", "learning_count": len(urls), "source_count": len(urls),
            "top_findings": [f"Finding 1 for {worker}"],
            "sources": [{"title": f"Source {worker}", "url": u, "type": "web"} for u in urls]}


def _end_steps(v2: bool, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_call("complete_task", {"result": payload})] if v2 else [_text(json.dumps(payload))]


def _worker_rules(w: str, q: str, spec: Dict[str, Any], v2: bool) -> List[Dict[str, Any]]:
    urls = _urls_for(w, spec, v2)
    save = _call("dw_save_learnings", {"worker_id": w, "learnings": [_learning_at(w, u, n) for n, u in enumerate(urls, 1)]})
    kind = (spec.get("prose") or {}).get(w)
    if kind == "prose":
        ending = [_text(f"Done. I saved the findings for {w} ({len(urls)} sources). Plain-text recap: prices differ by tier.")]
    elif kind == "partial":
        ending = [_text(json.dumps({"summary": f"saved {w}", "note": "partial answer, counts omitted"}))]
    else:
        ending = _end_steps(v2, _summary(w, urls))
    return [
        _rule({"role": "user", "includes": f"WORKER {w}:"}, [_call("web_search", {"query": q})]),
        _rule({"role": "tool_result", "tool": "web_search", "includes": f'query": "{q}"'}, [save]),
        _rule({"role": "tool_result", "tool": "dw_save_learnings", "includes": f'"worker_id": "{w}"'}, ending),
    ]


def _steer_rules(spec: Dict[str, Any], v2: bool) -> List[Dict[str, Any]]:
    """The steered worker searches the new focus when the orchestrator's message reaches it, then saves it."""
    steer = spec.get("steer") or {}
    if not v2 or not steer:
        return []
    w, q = steer["worker"], steer["query"]
    urls = _urls_for(w, spec, v2)
    focus = _learning_at(w, urls[-1], len(urls) + 1)
    focus["fact"] = f"Focused finding for {w}: {steer.get('keyword', '')}"
    # a later save replaces the worker's earlier one, so it carries the first learnings too
    learnings = [_learning_at(w, url, n) for n, url in enumerate(urls, 1)] + [focus]
    return [
        _rule({"role": "user", "includes": STEER_MARKER}, [_call("web_search", {"query": q})]),
        _rule({"role": "tool_result", "tool": "web_search", "includes": f'query": "{q}"'},
              [_call("dw_save_learnings", {"worker_id": w, "learnings": learnings})]),
    ]


def _reflector_rules(prefix: str, spec: Dict[str, Any], v2: bool) -> List[Dict[str, Any]]:
    gap = spec.get("gap") or {}
    rules: List[Dict[str, Any]] = [
        _rule({"role": "user", "includes": f"REFLECT {prefix} round 2"}, [_call("dw_get_learnings", {})]),
        _rule({"role": "user", "includes": f"REFLECT {prefix} round 1"}, [_call("dw_get_learnings", {})]),
    ]
    if gap:
        rules.append(_rule({"role": "tool_result", "tool": "dw_get_learnings", "includes": gap["worker_id"]}, [
            _call("dw_record_reflection", {"round": 2, "score": 90, "gaps": [], "should_continue": False})]))
    gaps = [gap["sub_query"]] if gap else []
    rules.append(_rule({"role": "tool_result", "tool": "dw_get_learnings"}, [
        _call("dw_record_reflection", {"round": 1, "score": 60 if gap else 90, "gaps": gaps, "should_continue": bool(gap)})]))
    r2 = {"score": 90, "gaps": [], "round": 2, "continuing": False, "note": f"reflected {prefix} r2"}
    r1 = {"score": 60 if gap else 90, "gaps": gaps, "round": 1, "continuing": bool(gap),
          "note": f"reflected {prefix} r1" + (" gap" if gap else "")}
    rules.append(_rule({"role": "tool_result", "tool": "dw_record_reflection", "includes": '"round": 2'}, _end_steps(v2, r2)))
    rules.append(_rule({"role": "tool_result", "tool": "dw_record_reflection", "includes": '"round": 1'}, _end_steps(v2, r1)))
    return rules


def subagent_rules(plan_title: str, ids: List[str], queries: List[str], prefix: str, *, v2: bool,
                   spec: Optional[Dict[str, Any]] = None, findings_md: Optional[str] = None) -> List[Dict[str, Any]]:
    """All rules of one subagent-contract scenario (orchestrator, workers, reflector, verifier) for one arm."""
    spec = spec or {}
    gap, steer, bait = spec.get("gap") or {}, spec.get("steer") or {}, spec.get("bait")
    findings = findings_md or SUBAGENT_FINDINGS.format(a=queries[0], b=queries[-1])
    fixed = spec.get("findings_fixed") or findings
    if bait == "broken_index":
        findings = f"{findings} The vendor also reports a third-party audit [9]."

    def research(workers: List[str], qs: List[str], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [_call("research_worker", {"prompt": f"WORKER {w}: {q}", **({"label": w} if v2 else {})})
                for w, q in zip(workers, qs)] + extra

    steer_call = [_call("steer_worker", {"label": steer["worker"], "message": steer["message"]})] if v2 and steer else []
    reflect_r1 = [_call("reflector", {"prompt": f"REFLECT {prefix} round 1"})]
    rules: List[Dict[str, Any]] = [
        _rule({"role": "user", "includes": plan_title}, [_call("dw_start_round", {
            "round": 1, "workers": [{"worker_id": w, "sub_query": q} for w, q in zip(ids, queries)]})]),
    ]
    if gap:  # round 2: one more worker, then its own reflection
        rules += [
            _rule({"role": "tool_result", "tool": "dw_start_round", "includes": gap["worker_id"]},
                  research([gap["worker_id"]], [gap["sub_query"]], [])),
            _rule({"role": "tool_result", "tool": "research_worker", "includes": f"saved {gap['worker_id']}"},
                  [_call("reflector", {"prompt": f"REFLECT {prefix} round 2"})]),
            _rule({"role": "tool_result", "tool": "reflector", "includes": "r1 gap"}, [_call("dw_start_round", {
                "round": 2, "workers": [{"worker_id": gap["worker_id"], "sub_query": gap["sub_query"]}]})]),
        ]
    rules += [  # the steer_worker result can be the last one of the round: it also ends the round
        _rule({"role": "tool_result", "tool": "dw_start_round", "includes": ids[0]}, research(ids, queries, steer_call)),
        _rule({"role": "tool_result", "tool": "research_worker"}, reflect_r1),
        _rule({"role": "tool_result", "tool": "steer_worker"}, reflect_r1),
    ]
    # --- the report: Findings [, Background], Method, then (v2) the verifier, then finalize ---
    sections = [("Findings", findings)]
    if bait == "uncited":
        sections.append(("Background", UNCITED_BACKGROUND))
    sections.append(("Method", "Parallel workers, one reflection round and a verification step." if v2
                     else "Parallel workers and one reflection round."))
    fix_section, fix_md = ("Background", fixed) if bait == "uncited" else ("Findings", fixed)
    if bait == "broken_index":  # the baseline learns of the bad index from the blocked finalize
        rules.append(_rule({"role": "tool_result", "tool": "dw_finalize", "includes": "Citations out of range"},
                           [_call("dw_write_section", {"heading": fix_section, "markdown": fix_md})]))
    rules.append(_rule({"role": "tool_result", "tool": "reflector"},
                       [_call("dw_write_section", {"heading": sections[0][0], "markdown": sections[0][1]})]))
    rules.append(_rule({"role": "tool_result", "tool": "dw_write_section", "includes": '"replaced": true'},
                       [_call("dw_finalize", {})]))
    for i in range(len(sections) - 1, 0, -1):
        if i == len(sections) - 1:
            nxt = [_call("verifier", {"prompt": f"VERIFY {prefix} the draft report"})] if v2 else [_call("dw_finalize", {})]
        else:
            nxt = [_call("dw_write_section", {"heading": sections[i + 1][0], "markdown": sections[i + 1][1]})]
        rules.append(_rule({"role": "tool_result", "tool": "dw_write_section", "includes": sections[i][0]}, nxt))
    rules.append(_rule({"role": "tool_result", "tool": "dw_write_section"},
                       [_call("dw_write_section", {"heading": sections[1][0], "markdown": sections[1][1]})]))
    rules.append(_rule({"role": "tool_result", "tool": "dw_finalize"}, [_text(FINAL_TEXT)]))
    if v2:
        verdict = {"unsupported_claims": [], "missing_citations": [], "broken_citations": []}
        if bait == "broken_index":
            verdict["broken_citations"] = [{"section": "Findings", "citation": "[9]"}]
        elif bait == "uncited":
            verdict["missing_citations"] = [{"section": "Background", "claim": "Several vendors with different tiers"}]
        rules += [
            _rule({"role": "user", "includes": f"VERIFY {prefix}"}, [_call("dw_get_report", {})]),
            _rule({"role": "tool_result", "tool": "dw_get_report"}, [_call("complete_task", {"result": verdict})]),
            _rule({"role": "tool_result", "tool": "verifier", "includes": '"section"'},
                  [_call("dw_write_section", {"heading": fix_section, "markdown": fix_md})]),
            _rule({"role": "tool_result", "tool": "verifier"}, [_call("dw_finalize", {})]),
        ]
    # --- workers (a steer message first), the engine's complete_task reminder, the reflector ---
    rules = _steer_rules(spec, v2) + rules
    for w, q in zip(ids + ([gap["worker_id"]] if gap else []), queries + ([gap["sub_query"]] if gap else [])):
        rules += _worker_rules(w, q, spec, v2)
    if v2:
        done = ([_call("complete_task", {"result": _summary("nudged", [], "completed after the reminder")})]
                if spec.get("nudge", "complete") == "complete" else [_text("Plain-text recap again: I have nothing more to add.")])
        rules.append(_rule({"role": "user", "includes": NUDGE_MARKER}, done))
    rules += _reflector_rules(prefix, spec, v2)
    return rules
