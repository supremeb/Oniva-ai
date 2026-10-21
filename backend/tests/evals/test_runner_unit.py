"""U5 pieces that need no stack: the budget meter, script building, ledger cost, the corpus, the live-mode guards."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from evals import runner
from evals.budget import BudgetConfigError, BudgetMeter
from evals.fake_scripts import ScriptError, build_script
from evals.fixture_trim import trim_trace
from evals.ledger import settle_cost, with_ledger_cost
from evals.loader import load_task, load_tasks
from evals.replay import fixture_urls, read_fixture, request_key
from evals.schema import GraderSpec, Span, Trace, Trial, TrialResult, Usage
from evals.stack import KEY_VAR, LiveModeError, eval_stack, require_live_key
from evals.trace import flatten

ROOT = Path(__file__).resolve().parents[2] / "evals"
CORPUS = ROOT / "tasks"
FIX = Path(__file__).parent / "fixtures"


# --- budget ------------------------------------------------------------------


def test_meter_stops_short_of_the_cap():
    m = BudgetMeter(1.0)
    assert m.can_start()
    m.record(0.4)
    assert m.can_start()  # 0.4 spent, the next is expected to cost 0.4
    m.record(0.45)
    assert not m.can_start()  # 0.85 + 0.45 would cross 1.0
    assert m.remaining == pytest.approx(0.15)


def test_meter_uses_the_task_budget_as_the_first_estimate():
    assert BudgetMeter(1.0).can_start(task_max_usd=0.5)
    assert not BudgetMeter(0.3).can_start(task_max_usd=0.5)
    assert BudgetMeter(None).can_start(task_max_usd=99)


def test_meter_from_env_and_the_live_requirement():
    assert BudgetMeter.from_env({}).max_usd is None
    assert BudgetMeter.from_env({"EVAL_MAX_USD": "2.5"}).max_usd == 2.5
    for bad in ({}, {"EVAL_MAX_USD": ""}, {"EVAL_MAX_USD": "0"}, {"EVAL_MAX_USD": "lots"}):
        with pytest.raises(BudgetConfigError):
            BudgetMeter.from_env(bad, required=True)


# --- fake scripts --------------------------------------------------------------


def test_chat_script_comes_from_the_tasks_rules_with_times_expanded():
    task = load_task(CORPUS.parent.parent / "tests" / "evals" / "tasks" / "fail-limit-exceeded.yaml")
    script = build_script(task)
    flood = script["rules"][0]["respond"]["steps"]
    assert len(flood) == 33 and {s["name"] for s in flood} == {"browse_url"} and "times" not in flood[0]
    assert script["fallback"]["steps"][0]["text"] == "ok"


def test_deep_work_script_follows_the_plan():
    script = build_script(load_task(CORPUS / "dw-fake-smoke.yaml"))
    text = json.dumps(script)
    for needle in ("DWPLAN_EVAL_SMOKE", "es-w1", "es-w2", "dw_finalize", "[1][2]", "eval smoke es-w1 vendor pricing"):
        assert needle in text
    assert script["rules"][0]["when"]["includes"] == "DWPLAN_EVAL_SMOKE"


def test_bad_fake_sections_are_reported_by_task():
    chat = load_task(CORPUS / "chat-fake-browse.yaml")
    with pytest.raises(ScriptError, match="needs fake.rules"):
        build_script(replace(chat, fake={}))
    with pytest.raises(ScriptError, match="respond.steps"):
        build_script(replace(chat, fake={"rules": [{"when": {}}]}))
    dw = load_task(CORPUS / "dw-fake-smoke.yaml")
    bad_plan = {"title": "T", "sub_queries": [{"worker_id": "a", "sub_query": "q"}, {"worker_id": "b", "sub_query": "q"}]}
    with pytest.raises(ScriptError, match="worker ids"):
        build_script(replace(dw, input={**dw.input, "plan": bad_plan}))
    with pytest.raises(ScriptError, match="input.plan"):
        build_script(replace(dw, input={"query": "something to research"}))


# --- corpus and fixtures ----------------------------------------------------------


def test_corpus_loads_and_every_fake_web_call_has_a_fixture():
    tasks = {t.id: t for t in load_tasks(CORPUS)}
    assert {"chat-fake-browse", "dw-fake-smoke"} <= set(tasks)
    web = ROOT / "fixtures" / "web"
    assert read_fixture(web, request_key("browse_url", {"url": "https://example.com/pricing"})) is not None
    for q in ("eval smoke es-w1 vendor pricing", "eval smoke es-w2 vendor pricing"):
        result = read_fixture(web, request_key("web_search", {"query": q}))
        assert result is not None and result["query"] == q
    urls = set(fixture_urls(web))
    # the URLs the scripted workers cite as sources are the ones the fixtures list (url_resolves)
    assert {f"https://example.com/es-w{w}/{n}" for w in (1, 2) for n in (1, 2)} <= urls


# --- ledger cost -----------------------------------------------------------------


def _trace() -> Trace:
    return Trace(
        root=Span(kind="subagent", name="root"),
        usage_by_role={
            "root": Usage(input_tokens=100, output_tokens=10),
            "research_worker": Usage(input_tokens=50, output_tokens=5),
        },
    )


def test_ledger_cost_lands_on_the_root_once():
    priced = with_ledger_cost(_trace(), 0.25)
    assert sum(u.cost_usd for u in priced.usage_by_role.values()) == pytest.approx(0.25)
    assert priced.usage_by_role["research_worker"].input_tokens == 50
    result = TrialResult(trial=Trial("t", 0), trace=priced)
    assert result.cost_usd == pytest.approx(0.25)


async def test_settle_cost_waits_until_the_ledger_matches_the_usage_documents():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        late = len(calls) < 3
        return httpx.Response(200, json={"totalUsd": 0.1, "inputTokens": 120 if late else 150, "outputTokens": 15})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await settle_cost(http, "http://b", {}, "conv", _trace(), wait_s=5)
    assert got["complete"] is True and got["totalUsd"] == 0.1 and len(calls) == 3
    assert calls[0] == "/api/v1/engine/conversations/conv/cost"


async def test_settle_cost_gives_up_with_the_latest_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalUsd": 0.05, "inputTokens": 1, "outputTokens": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await settle_cost(http, "http://b", {}, "conv", _trace(), wait_s=0.5)
    assert got["complete"] is False and got["totalUsd"] == 0.05


# --- runner pieces -----------------------------------------------------------------


def test_a_broken_grader_fails_its_trial_not_the_suite():
    task = replace(
        load_task(CORPUS / "chat-fake-browse.yaml"),
        graders=(GraderSpec(id="bad", type="budget", params={}),),  # budget needs a limit
    )
    raw = json.loads((FIX / "trace_fake_chat.json").read_text())
    from evals.graders import GradeContext

    out = runner.grade_trace(task, GradeContext(trace=flatten(raw), raw=raw))
    assert len(out) == 1 and not out[0].passed and out[0].detail.startswith("grader error")


def test_judges_are_skipped_in_fake_mode():
    task = replace(load_task(CORPUS / "chat-fake-browse.yaml"), graders=(GraderSpec(id="j", type="judge"),))
    from evals.graders import GradeContext

    raw = json.loads((FIX / "trace_fake_chat.json").read_text())
    assert runner.grade_trace(task, GradeContext(trace=flatten(raw), raw=raw)) == ()


def test_run_error_kind_is_the_roots_last_run_error():
    raw = json.loads((FIX / "trace_fake_run_error.json").read_text())
    assert runner.run_error_kind(raw) == "invalid_request"
    assert runner.run_error_kind(json.loads((FIX / "trace_fake_chat.json").read_text())) is None
    assert runner.run_error_kind(None) is None


def test_skipped_trials_never_pass():
    res = runner.skipped(load_task(CORPUS / "chat-fake-browse.yaml"), 2, "fake")
    assert res.status == "skipped_budget" and not res.passed and res.cost_usd == 0.0 and res.trial.index == 2


def test_trace_trimming_keeps_structure():
    raw = json.loads((FIX / "trace_fake_chat.json").read_text())
    trimmed = trim_trace({"entries": [{"payload": {"text": "x" * 2000}}], "n": 3}, max_chars=100)
    assert trimmed["n"] == 3 and trimmed["entries"][0]["payload"]["text"].endswith("[trimmed 1900 chars]")
    assert len(json.dumps(raw)) < 20_000


# --- live mode never starts without a key and a cap ----------------------------------


@pytest.fixture
def no_stack(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a stack was booted")

    monkeypatch.setattr(runner, "eval_stack", boom)


async def test_live_mode_without_a_key_fails_fast_and_spends_nothing(monkeypatch, no_stack):
    monkeypatch.delenv(KEY_VAR, raising=False)
    with pytest.raises(LiveModeError, match=KEY_VAR):
        await runner.run_suite([load_task(CORPUS / "chat-fake-browse.yaml")], mode="live", meter=BudgetMeter(5))
    with pytest.raises(LiveModeError):
        require_live_key({})
    with pytest.raises(LiveModeError):
        with eval_stack("live"):
            pass


async def test_live_mode_without_a_cap_fails_fast(monkeypatch, no_stack):
    monkeypatch.setenv(KEY_VAR, "present-but-never-used")
    for meter in (None, BudgetMeter(None), BudgetMeter(0)):
        with pytest.raises(BudgetConfigError, match="EVAL_MAX_USD"):
            await runner.run_suite([load_task(CORPUS / "chat-fake-browse.yaml")], mode="live", meter=meter)


async def test_the_cli_needs_an_explicit_live_flag_and_a_cap(monkeypatch, capsys):
    # main() runs its own event loop, so it goes on a thread of its own
    main = lambda *argv: asyncio.to_thread(runner.main, list(argv))  # noqa: E731
    monkeypatch.delenv(KEY_VAR, raising=False)
    monkeypatch.delenv("EVAL_MAX_USD", raising=False)
    assert await main("--live", "--task", "chat-fake-browse") == 2
    assert "EVAL_MAX_USD" in capsys.readouterr().out
    monkeypatch.setenv("EVAL_MAX_USD", "1")
    assert await main("--live", "--task", "chat-fake-browse") == 2
    assert KEY_VAR in capsys.readouterr().out
    assert await main("--task", "no-such-task") == 2


def test_paged_traces_keep_the_submissions():
    from evals.trace import _merge_page

    merged: dict = {"conversationId": "c"}
    subs = [{"id": "s", "requestId": "r", "status": "done", "unanswered": None}]
    _merge_page(merged, {"entries": [1], "submissions": subs, "truncated": True, "nextCursor": "x"})
    _merge_page(merged, {"entries": [2], "submissions": subs, "truncated": False, "nextCursor": None})
    assert merged["submissions"] == subs and merged["entries"] == [1, 2]
