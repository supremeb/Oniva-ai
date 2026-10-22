"""Build the scripted model's rule file for a task's `fake:` section (fake mode).

The worker's `ScriptedModel` answers each request from the LAST message alone, by the first matching rule
(`engine/test/support/scripted-model.ts`). A task provides:

- `long_session`: the rules are generated from `input` (`evals.long_session.fake_rules`); `fake.rules` come first;
- `repeat` tasks and `redteam` tasks with `input.path: dw` are Deep Work tasks (below) and may carry `fake.curator`
  (see `curator_rules`): the scripted memory curator's reply to the curator prompt;
- chat-like tasks (`chat`, `memory`, `redteam` with `input.path: chat`): `fake.rules`, a list of rules in the
  scripted model's own format (`when: {role, includes, tool}`, `respond: {steps, usage}`). A `tool_call` step with
  `times: N` expands to N identical calls; a `text` step with `repeat: N` repeats its text (the model's own feature);
- `deep_work`: the plan in `input.plan` (worker ids `<prefix>-w1`, `<prefix>-w2`, ...). The orchestrator, worker and
  reflector rules are generated with the e2e Deep Work script builders (`tests.e2e.dw_script`), so the flow is the one
  the recovery e2e tests already exercise: start round, workers search and save learnings, reflection, two sections,
  finalize. `fake.findings_md` sets the "Findings" section's text (put `[1]` style citations here).
  `fake.rules` are tried first, e.g. to force an error.

Both accept `fake.fallback` (a response for requests no rule matches; default: the text "ok", which the intake step
reads as "proceed"). Nothing here can reach a provider.
"""

from __future__ import annotations

import json
from typing import Any

from tests.e2e import dw_script

from . import long_session
from .schema import Task

DEFAULT_FALLBACK: dict[str, Any] = {"steps": [{"type": "text", "text": "ok"}]}


class ScriptError(ValueError):
    """The task's `fake:` section (or plan) cannot be turned into a script."""


def _expand(step: Any) -> list[Any]:
    """`{"type": "tool_call", ..., "times": 3}` stands for three identical calls (ids are assigned per request)."""
    if isinstance(step, dict) and step.get("type") == "tool_call" and "times" in step:
        copies = {k: v for k, v in step.items() if k != "times"}
        return [dict(copies) for _ in range(int(step["times"]))]
    return [step]


def _rules(task: Task) -> list[dict[str, Any]]:
    raw = task.fake.get("rules") or []
    if not isinstance(raw, list):
        raise ScriptError(f"task '{task.id}': fake.rules must be a list")
    rules = []
    for i, rule in enumerate(raw):
        steps = (rule.get("respond") or {}).get("steps") if isinstance(rule, dict) else None
        if not isinstance(steps, list):
            raise ScriptError(f"task '{task.id}': fake.rules[{i}] needs respond.steps")
        expanded = [e for step in steps for e in _expand(step)]
        rules.append({**rule, "respond": {**rule["respond"], "steps": expanded}})
    return rules


def deep_work_plan(task: Task) -> dict[str, Any]:
    """The task's plan, validated to the shape the scenario builder needs."""
    plan = task.input.get("plan")
    if not isinstance(plan, dict) or not plan.get("title") or not plan.get("sub_queries"):
        raise ScriptError(f"task '{task.id}': deep_work needs input.plan with a title and sub_queries")
    subs = plan["sub_queries"]
    ids = [str(s.get("worker_id", "")) for s in subs]
    if len(ids) < 2 or not ids[0].endswith("-w1"):
        raise ScriptError(f"task '{task.id}': plan worker ids must be <prefix>-w1, <prefix>-w2, ... (at least two)")
    prefix = ids[0][: -len("-w1")]
    if any(i != f"{prefix}-w{n}" for n, i in enumerate(ids, 1)):
        raise ScriptError(f"task '{task.id}': plan worker ids must be {prefix}-w1, {prefix}-w2, ... in order")
    return plan


CURATOR_MARK = "## Current playbook bullets"  # a heading only the memory curator's prompt carries


def curator_rules(task: Task) -> list[dict[str, Any]]:
    """The scripted memory curator (U34): `fake.curator` is `{deltas: [...]}` (a delta list) or `{raw: "text"}` (a reply
    that is not JSON). Its rule answers the curator's prompt, which quotes the source conversation, so it must come
    before every rule that keys on the user's query or the plan title."""
    spec = task.fake.get("curator")
    if not spec:
        return []
    if not isinstance(spec, dict) or not ("deltas" in spec or "raw" in spec):
        raise ScriptError(f"task '{task.id}': fake.curator needs 'deltas' (a list) or 'raw' (text)")
    reply = str(spec["raw"]) if "raw" in spec else json.dumps({"deltas": spec["deltas"]})
    return [{
        "when": {"role": "user", "includes": CURATOR_MARK},
        "respond": {"steps": [{"type": "text", "text": reply}], "usage": {"inputTokens": 400, "outputTokens": 60}},
    }]


def is_deep_work(task: Task) -> bool:
    """Deep Work tasks and the memory tasks that run Deep Work (`repeat`, and `redteam` with `input.path: dw`)."""
    return task.kind in ("deep_work", "repeat") or (task.kind == "redteam" and task.input.get("path") == "dw")


def build_script(task: Task, arm: str = "baseline") -> dict[str, Any]:
    """The scripted model's rules for `task`. `arm` (`baseline` or `v2`) matters only to a Deep Work task with a
    `fake.scenario` (the subagent-contract suite, U24): that scenario is scripted once per arm, see
    `tests.e2e.dw_script.subagent_rules`."""
    rules = _rules(task)
    if is_deep_work(task):
        rules += curator_rules(task)
        plan = deep_work_plan(task)
        ids = [s["worker_id"] for s in plan["sub_queries"]]
        queries = [s["sub_query"] for s in plan["sub_queries"]]
        prefix = ids[0][: -len("-w1")]
        if isinstance(task.fake.get("scenario"), dict):
            rules += dw_script.subagent_rules(
                plan["title"], ids, queries, prefix, v2=arm == "v2", spec=task.fake["scenario"],
                findings_md=task.fake.get("findings_md"),
            )
        else:
            rules += dw_script.scenario_rules(
                plan["title"], ids, queries, prefix, findings_md=task.fake.get("findings_md")
            )
            rules += dw_script.finish_rules()
    elif task.kind == "long_session":
        try:
            rules += long_session.fake_rules(task)  # explicit fake.rules (e.g. a forced error) are tried first
        except long_session.SpecError as e:
            raise ScriptError(str(e)) from None
    elif not rules:
        raise ScriptError(f"task '{task.id}': fake mode needs fake.rules")
    return {"rules": rules, "fallback": task.fake.get("fallback") or DEFAULT_FALLBACK}
