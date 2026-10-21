"""Build the scripted model's rule file for a task's `fake:` section (fake mode).

The worker's `ScriptedModel` answers each request from the LAST message alone, by the first matching rule
(`engine/test/support/scripted-model.ts`). A task provides:

- chat-like tasks (`chat`, `long_session`, `memory`, `repeat`, `redteam`): `fake.rules`, a list of rules in the
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

from typing import Any

from tests.e2e import dw_script

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


def build_script(task: Task) -> dict[str, Any]:
    rules = _rules(task)
    if task.kind == "deep_work":
        plan = deep_work_plan(task)
        ids = [s["worker_id"] for s in plan["sub_queries"]]
        queries = [s["sub_query"] for s in plan["sub_queries"]]
        prefix = ids[0][: -len("-w1")]
        rules += dw_script.scenario_rules(
            plan["title"], ids, queries, prefix, findings_md=task.fake.get("findings_md")
        )
        rules += dw_script.finish_rules()
    elif not rules:
        raise ScriptError(f"task '{task.id}': fake mode needs fake.rules")
    return {"rules": rules, "fallback": task.fake.get("fallback") or DEFAULT_FALLBACK}
