"""U34: the pure helpers of the memory driver (offline): what counts as model-visible memory, writes and taint."""

import pytest

from evals.drivers import memory as drv
from evals.fake_scripts import ScriptError, build_script, curator_rules, is_deep_work
from evals.loader import load_tasks
from evals.schema import Task

PROMPT = """You are a helpful assistant.

## Tools
Use them well.

## Project memory

This is the project's memory.

<memory_core path="core.md">
# Project
- Reports use APA citations.
</memory_core>

## Something else
Not memory.

## Project playbook

- [b1] Cite every number.
"""


def test_memory_sections_keep_the_memory_and_playbook_blocks_only():
    text = drv.memory_sections(PROMPT)
    assert "Reports use APA citations." in text and "[b1] Cite every number." in text
    assert "Not memory." not in text and "Use them well." not in text
    assert text.count("## Project") == 2
    assert drv.memory_sections("## Tools\nnothing") == "" and drv.memory_sections("") == ""


def entry(kind, **payload):
    return {"kind": kind, "payload": payload}


RAW = {
    "taint": {"tainted": True},
    "entries": [
        entry("user", content="hi"),
        entry("assistant", message={"blocks": [{"type": "text", "text": "Looking."}, {"type": "tool_call", "name": "memory_create"}]}),
        entry("tool_result", toolName="memory_create", isError=True, status="blocked", content="Blocked by policy"),
        entry("tool_result", toolName="web_search", isError=False, content="results"),
        entry("tool_result", toolName="memory_view", isError=False, content="core.md: ..."),
        entry("assistant", message={"blocks": [{"type": "text", "text": "Done."}]}),
    ],
}


def test_visible_texts_are_answers_and_memory_tool_results_only():
    seen = drv.visible_texts(RAW, "attack")
    assert [(t["label"], t["text"]) for t in seen] == [
        ("attack answer", "Looking."), ("attack memory_create result", "Blocked by policy"),
        ("attack memory_view result", "core.md: ..."), ("attack answer", "Done.")]
    assert drv.final_text(RAW) == "Done." and drv.final_text({}) == ""


def test_memory_writes_record_whether_each_was_refused_and_taint_is_read_from_the_trace():
    assert drv.memory_writes(RAW) == [{"tool": "memory_create", "refused": True, "status": "blocked"}]
    assert drv.tainted(RAW) is True and drv.tainted({"taint": {"tainted": False}}) is False and drv.tainted(None) is False


def test_ids_are_normalised_between_sqlite_hex_and_the_api():
    assert drv._uuid("0123456789abcdef0123456789abcdef") == "01234567-89ab-cdef-0123-456789abcdef"
    assert drv._uuid(None) is None and drv._uuid("") is None
    assert drv._hex("01234567-89ab-cdef-0123-456789abcdef") == "0123456789abcdef0123456789abcdef"


def task(kind="repeat", **kw):
    return Task(id="t", kind=kind, **kw)


def test_the_curator_rule_and_the_deep_work_kinds():
    assert is_deep_work(task("repeat")) and is_deep_work(task("deep_work"))
    assert is_deep_work(task("redteam", input={"path": "dw"})) and not is_deep_work(task("redteam", input={"path": "chat"}))
    assert not is_deep_work(task("memory"))
    assert curator_rules(task()) == []
    (rule,) = curator_rules(task(fake={"curator": {"deltas": [{"op": "retire", "id": "x"}]}}))
    assert rule["when"]["includes"] == "## Current playbook bullets" and '"retire"' in rule["respond"]["steps"][0]["text"]
    (raw,) = curator_rules(task(fake={"curator": {"raw": "Sure, I will remember that."}}))
    assert raw["respond"]["steps"][0]["text"] == "Sure, I will remember that."
    for bad in ({"curator": {"x": 1}}, {"curator": "text"}):
        with pytest.raises(ScriptError):
            curator_rules(task(fake=bad))


def test_a_repeat_task_scripts_as_deep_work_with_the_curator_ahead_of_the_plan():
    from pathlib import Path

    evals = Path(__file__).resolve().parents[2] / "evals"
    repeat = next(t for t in load_tasks(evals / "tasks") if t.kind == "repeat")
    rules = build_script(repeat)["rules"]
    plan_rule = next(i for i, r in enumerate(rules) if r["when"].get("includes") == repeat.input["plan"]["title"])
    curator = next(i for i, r in enumerate(rules) if r["when"].get("includes") == "## Current playbook bullets")
    assert curator < plan_rule  # the curator prompt quotes the run; the plan-title rule must not answer it
