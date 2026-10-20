"""The scripted model for the Fleet e2e scenarios (test-only).

A Fleet run is one agent loop on the engine. The model answers each request from the LAST message
alone, so every scenario keys on strings only it produces. The task text (``task(name)``) is the user
message; the agent's tools are ``web_search`` (fake, a ``GATED`` query waits for the dw gate) and
``e2e_hold`` (a non-replayable write that appends one line to the effects file once the write gate exists).

  recovery  web_search (held) -> e2e_hold -> final text. Killed while the search is held.
  restart   same script; the BACKEND process is replaced while the search is held. The engine tells the
            model the gateway was unreachable (a call in flight in a dead process is not retried by the
            engine); the model searches again (not held) and the run goes on.
  steer     web_search (held); an ``FLEET_REDIRECT`` message sent meanwhile is the next user message.
  cancel    web_search (held) -> e2e_hold -> final text; cancelled while the search is held, so the
            write must never start.

The browser scenarios (``ui_task``) hold ONE write, ``e2e_fleet_hold`` (the same gate file as ``e2e_hold``, a
separate tool name so its results do not meet the Deep Work rules), because the browser spec's control API opens
and closes that write gate: task -> e2e_fleet_hold -> final text; a ``FLEET_STEER`` message goes straight to its own
final text.

Each model request has its own token counts, so a ledger row can be matched to the request that made it.
"""
from typing import Any, Dict, List

UI_SCENARIOS = ("recovery", "steer", "cancel")  # the browser spec (frontend/e2e/fleet-engine-recovery.spec.ts)
UI_FINAL = "Fleet ui done (e2e-flt-ui)."
UI_STEERED = "Fleet ui steered (e2e-flt-ui-steer)."
SCENARIOS = ("recovery", "restart", "steer", "cancel")
TOKENS = {  # scenario -> tokens of request 1, 2, 3 (input, output)
    "recovery": [(101, 11), (102, 12), (103, 13)],
    "restart": [(111, 21), (112, 22), (113, 23), (114, 24)],  # 2 = after the retried search, 4 = after the outage
    "steer": [(121, 31), (122, 32)],
    "cancel": [(131, 41), (132, 42), (133, 43)],
}
REDIRECT = "FLEET_REDIRECT use the cheaper vendor"


def task(name: str) -> str:
    return f"FLEETQ_{name.upper()} check the vendor list"


def ui_task(name: str) -> str:
    return f"FLEETUI_{name.upper()} check the vendor list"


def search_query(name: str) -> str:
    return f"flt-{name}-GATED"


def note(name: str) -> str:
    return f"flt-{name}-write"


def final_text(name: str) -> str:
    return f"Fleet {name} done (e2e-flt-{name})."


def _call(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_call", "name": tool, "arguments": arguments}


def _rule(when: Dict[str, Any], steps: List[Dict[str, Any]], tokens) -> Dict[str, Any]:
    return {"when": when, "respond": {"steps": steps, "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1]}}}


def build() -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = []
    for name in SCENARIOS:
        t = TOKENS[name]
        text = {"type": "text", "text": final_text(name)}
        rules.append(_rule({"role": "user", "includes": f"FLEETQ_{name.upper()}"},
                           [_call("web_search", {"query": search_query(name)})], t[0]))
        if name == "steer":
            rules.append(_rule({"role": "user", "includes": "FLEET_REDIRECT"}, [text], t[1]))
            rules.append(_rule({"role": "tool_result", "tool": "web_search", "includes": search_query(name)},
                               [{"type": "text", "text": "Fleet steer was not seen (e2e-flt-missed)."}], t[1]))
            continue
        if name == "restart":
            rules.append(_rule({"role": "tool_result", "tool": "web_search", "includes": "could not be reached"},
                               [_call("web_search", {"query": "flt-restart-retry"})], t[3]))
            rules.append(_rule({"role": "tool_result", "tool": "web_search", "includes": "flt-restart-retry"},
                               [_call("e2e_hold", {"note": note(name)})], t[1]))
        rules.append(_rule({"role": "tool_result", "tool": "web_search", "includes": search_query(name)},
                           [_call("e2e_hold", {"note": note(name)})], t[1]))
        rules.append(_rule({"role": "tool_result", "tool": "e2e_hold", "includes": note(name)}, [text], t[2]))
    for name in UI_SCENARIOS:
        rules.append(_rule({"role": "user", "includes": f"FLEETUI_{name.upper()}"},
                           [_call("e2e_fleet_hold", {"note": f"flt-ui-{name}"})], (141, 51)))
    rules.append(_rule({"role": "user", "includes": "FLEET_STEER"}, [{"type": "text", "text": UI_STEERED}], (143, 53)))
    rules.append(_rule({"role": "tool_result", "tool": "e2e_fleet_hold"}, [{"type": "text", "text": UI_FINAL}], (142, 52)))
    return {"rules": rules}
