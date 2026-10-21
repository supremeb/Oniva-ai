"""Judges and calibration, with a fake transport only: no model is ever called."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from evals import calibrate as cal
from evals.budget import BudgetMeter
from evals.graders import GradeContext
from evals.graders import judge as j
from evals.schema import GraderSpec, Span, Trace

REAL_PROMPTS = j.PROMPTS_DIR
MODEL = "judge/pinned-1"


def run(coro):
    return asyncio.run(coro)


class Fake:
    """Scripted transport: replies in order (the last repeats); a callable reply sees the call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[j.JudgeCall] = []

    async def __call__(self, call: j.JudgeCall) -> j.JudgeReply:
        self.calls.append(call)
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(call)
        if isinstance(r, str):
            r = j.JudgeReply(r, 10, 5, cost_usd=0.001)
        return r


def reply(verdict="yes", reason="ok"):
    return json.dumps({"verdict": verdict, "reason": reason})


ITEM = {"claim": "Team costs 29 EUR.", "evidence": "Team: 29 EUR per seat (source: https://x.test/p)"}


@pytest.fixture
def dirs(tmp_path):
    prompts = tmp_path / "judges"
    shutil.copytree(REAL_PROMPTS, prompts)
    return prompts, tmp_path / "calibration"


def runner(transport, dirs, *, meter=None, retries=1):
    prompts, calib = dirs
    return j.JudgeRunner(
        transport, MODEL, cache=j.JudgeCache(None), meter=meter, prompts_dir=prompts, calibration_dir=calib, retries=retries
    )


# --- prompts and shape ----------------------------------------------------------------------------------------------


def test_every_judge_has_a_prompt_and_an_extractor():
    assert set(j.JUDGE_FIELDS) == set(j.EXTRACTORS)
    for name in j.JUDGE_FIELDS:
        text = j.prompt_text(name)
        assert '"verdict"' in text and "yes" in text
    assert {p.stem for p in REAL_PROMPTS.glob("*.md")} == set(j.JUDGE_FIELDS)


def test_judge_type_is_still_not_a_code_grader():
    from evals.graders import grade

    with pytest.raises(ValueError):
        grade(GraderSpec(id="x", type="judge"), GradeContext(trace=Trace(root=Span("model", "root"))))


def test_unknown_judge_and_missing_field(dirs):
    with pytest.raises(j.JudgeConfigError):
        j.prompt_text("nope")
    with pytest.raises(ValueError, match="evidence"):
        run(runner(Fake(reply()), dirs).judge("claim_supported", {"claim": "x"}))


def test_no_pinned_model_is_refused(dirs, monkeypatch):
    monkeypatch.delenv(j.MODEL_VAR, raising=False)
    with pytest.raises(j.JudgeConfigError):
        j.JudgeRunner.from_env(Fake())
    with pytest.raises(j.JudgeConfigError):
        j.JudgeRunner(Fake(), "")


def test_call_is_pinned_and_labelled(dirs):
    t = Fake(reply())
    run(runner(t, dirs).judge("claim_supported", ITEM))
    (call,) = t.calls
    assert call.model == MODEL
    assert "<CLAIM>\nTeam costs 29 EUR.\n</CLAIM>" in call.user and "<EVIDENCE>" in call.user


# --- strict parsing -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [reply("yes", "r"), reply("no", ""), "```json\n" + reply() + "\n```", "  " + reply() + "\n"])
def test_parse_accepts_one_object(text):
    verdict, _ = j.parse_reply(text)
    assert verdict in ("yes", "no")


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Sure! " + reply(),
        reply("maybe"),
        reply("Yes"),
        '{"verdict": "yes"}',
        '{"verdict": "yes", "reason": 3}',
        '{"verdict": "yes", "reason": "a", "extra": 1}',
        '["yes"]',
        "",
    ],
)
def test_parse_rejects_everything_else(text):
    with pytest.raises(j.ParseError):
        j.parse_reply(text)


def test_malformed_then_valid_retries_once(dirs):
    t = Fake("garbage", reply("no", "unsupported"))
    v = run(runner(t, dirs).judge("claim_supported", ITEM))
    assert (v.verdict, v.reason, v.calls) == ("no", "unsupported", 2)
    assert v.cost_usd == pytest.approx(0.002)


def test_malformed_twice_is_error_and_never_passes(dirs):
    t = Fake("garbage", "still garbage", reply())  # a third reply must never be asked for
    v = run(runner(t, dirs).judge("claim_supported", ITEM))
    assert v.verdict == "error" and not v.ok and len(t.calls) == 2


def test_transport_failure_is_retried_then_error(dirs):
    t = Fake(RuntimeError("503"), RuntimeError("503"))
    v = run(runner(t, dirs).judge("claim_supported", ITEM))
    assert v.verdict == "error" and "503" in v.reason and v.cost_usd == 0
    t2 = Fake(RuntimeError("blip"), reply())
    assert run(runner(t2, dirs).judge("claim_supported", ITEM)).verdict == "yes"


# --- cache ----------------------------------------------------------------------------------------------------------


def test_cache_hit_makes_zero_model_calls(dirs):
    t = Fake(reply())
    r = runner(t, dirs)
    first = run(r.judge("claim_supported", ITEM))
    second = run(r.judge("claim_supported", dict(ITEM)))
    assert len(t.calls) == 1 and second.cached and second.cost_usd == 0 and second.verdict == first.verdict


def test_cache_persists_on_disk_and_ignores_errors(dirs, tmp_path):
    cache_dir = tmp_path / "cache"
    prompts, calib = dirs

    def make(t):
        return j.JudgeRunner(t, MODEL, cache=j.JudgeCache(cache_dir), prompts_dir=prompts, calibration_dir=calib)

    t1 = Fake("bad", "bad")
    assert run(make(t1).judge("claim_supported", ITEM)).verdict == "error"
    t2 = Fake(reply("no"))
    run(make(t2).judge("claim_supported", ITEM))  # the error was not cached: this calls the model
    t3 = Fake(reply("yes"))
    v = run(make(t3).judge("claim_supported", ITEM))
    assert v.cached and v.verdict == "no" and not t3.calls


def test_changed_prompt_model_or_input_misses_the_cache(dirs):
    prompts, _ = dirs
    t = Fake(reply())
    cache = j.JudgeCache(None)
    r = runner(t, dirs)
    r.cache = cache
    run(r.judge("claim_supported", ITEM))
    (prompts / "claim_supported.md").write_text((prompts / "claim_supported.md").read_text() + "\nBe terse.\n")
    run(r.judge("claim_supported", ITEM))
    assert len(t.calls) == 2
    r.model = "judge/other"
    run(r.judge("claim_supported", ITEM))
    run(r.judge("claim_supported", {**ITEM, "claim": "Team costs 30 EUR."}))
    assert len(t.calls) == 4


# --- budget ---------------------------------------------------------------------------------------------------------


def test_meter_charge_and_can_spend():
    m = BudgetMeter(0.01)
    m.charge(0.004)
    assert m.spent == pytest.approx(0.004) and m.trials == 0 and m.largest_trial == 0
    assert m.can_spend() and not m.can_spend(0.01)
    m.charge(0.006)
    assert not m.can_spend()
    assert BudgetMeter(None).can_spend(99)


def test_judge_spend_goes_through_the_meter_and_stops_at_the_cap(dirs):
    meter = BudgetMeter(0.0025)
    t = Fake(reply())  # each call costs 0.001
    r = runner(t, dirs, meter=meter)
    verdicts = [run(r.judge("claim_supported", {**ITEM, "claim": f"claim {i}"})) for i in range(5)]
    assert meter.spent == pytest.approx(0.003)
    assert [v.verdict for v in verdicts] == ["yes"] * 3 + ["error"] * 2
    assert "budget" in verdicts[-1].reason and len(t.calls) == 3


def test_priced_from_the_model_table_when_the_reply_has_no_cost(dirs):
    t = Fake(lambda c: j.JudgeReply(reply(), 1_000_000, 0))
    meter = BudgetMeter(None)
    run(j.JudgeRunner(t, "gpt-4o", cache=j.JudgeCache(None), meter=meter, prompts_dir=dirs[0]).judge("claim_supported", ITEM))
    assert meter.spent > 0


# --- gating and calibration records ---------------------------------------------------------------------------------


def write_labels(calib: Path, name: str, rows):
    calib.mkdir(parents=True, exist_ok=True)
    (calib / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def labelled(n_yes, n_no):
    rows = []
    for i in range(n_yes):
        rows.append({"id": f"y{i}", "label": "yes", "input": {**ITEM, "claim": f"supported {i}"}})
    for i in range(n_no):
        rows.append({"id": f"n{i}", "label": "no", "input": {**ITEM, "claim": f"unsupported {i}"}})
    return rows


def by_claim(wrong=()):
    """A judge that answers yes for 'supported *', no for 'unsupported *', flipping the ids in `wrong`."""

    def answer(call: j.JudgeCall):
        claim = call.user.split("<CLAIM>\n")[1].split("\n</CLAIM>")[0]
        verdict = "no" if claim.startswith("unsupported") else "yes"
        if claim.split()[-1] in wrong or claim in wrong:
            verdict = "yes" if verdict == "no" else "no"
        return reply(verdict, claim)

    return answer


def test_calibration_math(dirs):
    _, calib = dirs
    write_labels(calib, "claim_supported", labelled(20, 20))
    exs = cal.load_examples("claim_supported", calib)
    # 2 yes missed (TPR 18/20 = .9), 4 no missed (TNR 16/20 = .8)
    wrong = {"supported 0", "supported 1"} | {f"unsupported {i}" for i in range(4)}
    out = run(cal.calibrate(runner(Fake(by_claim(wrong)), dirs), "claim_supported", exs))
    assert (out.positives, out.negatives) == (20, 20)
    assert out.tpr == pytest.approx(0.9) and out.tnr == pytest.approx(0.8)
    assert not out.passed and len(out.disagreements) == 6
    assert {d.id for d in out.disagreements} >= {"y0", "n3"}
    assert "NOT CALIBRATED" in out.summary()


def test_perfect_judge_passes_and_errors_count_as_wrong(dirs):
    _, calib = dirs
    write_labels(calib, "claim_supported", labelled(20, 20))
    exs = cal.load_examples("claim_supported", calib)
    assert run(cal.calibrate(runner(Fake(by_claim()), dirs), "claim_supported", exs)).passed
    bad = Fake(lambda c: "nonsense" if "supported 0" in c.user else by_claim()(c))
    out = run(cal.calibrate(runner(bad, dirs), "claim_supported", exs))
    assert out.tpr == pytest.approx(19 / 20) and out.tnr == pytest.approx(19 / 20)  # 'supported 0' is in both classes' ids


def test_too_few_labels_cannot_pass(dirs):
    _, calib = dirs
    write_labels(calib, "claim_supported", labelled(5, 5))
    out = run(cal.calibrate(runner(Fake(by_claim()), dirs), "claim_supported", cal.load_examples("claim_supported", calib)))
    assert out.tpr == out.tnr == 1.0 and not out.passed and "at least 20" in out.problems[0]
    assert run(
        cal.calibrate(runner(Fake(by_claim()), dirs), "claim_supported", cal.load_examples("claim_supported", calib), min_per_class=5)
    ).passed


def test_threshold_cannot_be_lowered(dirs):
    with pytest.raises(ValueError):
        run(cal.calibrate(runner(Fake(by_claim()), dirs), "claim_supported", [], threshold=0.5))


def test_load_examples_validates(dirs):
    _, calib = dirs
    with pytest.raises(cal.LabelError, match="missing"):
        cal.load_examples("claim_supported", calib)
    write_labels(calib, "claim_supported", [{"id": "a", "label": "maybe", "input": ITEM}])
    with pytest.raises(cal.LabelError, match="line 1"):
        cal.load_examples("claim_supported", calib)
    write_labels(calib, "claim_supported", [{"id": "a", "label": "yes", "input": {"claim": "x"}}])
    with pytest.raises(cal.LabelError, match="evidence"):
        cal.load_examples("claim_supported", calib)
    write_labels(calib, "claim_supported", [{"id": "a", "label": "yes", "input": ITEM}] * 2)
    with pytest.raises(cal.LabelError, match="duplicate"):
        cal.load_examples("claim_supported", calib)


def test_main_exit_codes_and_record(dirs, tmp_path, capsys):
    prompts, calib = dirs
    write_labels(calib, "claim_supported", labelled(20, 20))
    good = Fake(by_claim())
    meter = BudgetMeter(5.0)
    code = cal.main(["claim_supported"], transport=good, calibration_dir=calib, meter=meter, model=MODEL, cache=j.JudgeCache(None))
    assert code == 0 and meter.spent == pytest.approx(0.04)
    rec = json.loads(j.record_path("claim_supported", calib).read_text())
    assert rec["passed"] and rec["model"] == MODEL and rec["prompt_sha256"] == j.prompt_hash("claim_supported")
    assert j.gating_status("claim_supported", MODEL, calibration_dir=calib) == (True, "calibrated: TPR 1.00, TNR 1.00")

    bad = Fake(by_claim({f"unsupported {i}" for i in range(10)}))
    assert cal.main(["claim_supported"], transport=bad, calibration_dir=calib, meter=BudgetMeter(5.0), model=MODEL, cache=j.JudgeCache(None)) == 1
    assert j.gating_status("claim_supported", MODEL, calibration_dir=calib)[0] is False  # the failed run replaced the record

    assert cal.main([], calibration_dir=calib) == 2
    assert cal.main(["nope"], calibration_dir=calib) == 2
    assert cal.main(["plan_coverage"], transport=good, calibration_dir=calib, meter=BudgetMeter(5.0), model=MODEL, cache=j.JudgeCache(None)) == 2
    capsys.readouterr()


def test_main_refuses_without_a_spend_cap(dirs, monkeypatch, capsys):
    _, calib = dirs
    write_labels(calib, "claim_supported", labelled(20, 20))
    monkeypatch.delenv("EVAL_MAX_USD", raising=False)
    assert cal.main(["claim_supported"], transport=Fake(by_claim()), calibration_dir=calib, model=MODEL, cache=j.JudgeCache(None)) == 2
    assert "EVAL_MAX_USD" in capsys.readouterr().err


def test_gating_needs_a_fresh_matching_record(dirs):
    prompts, calib = dirs
    assert j.gating_status("claim_supported", MODEL, prompts_dir=prompts, calibration_dir=calib)[0] is False
    write_labels(calib, "claim_supported", labelled(20, 20))
    cal.main(["claim_supported"], transport=Fake(by_claim()), calibration_dir=calib, meter=BudgetMeter(5.0), model=MODEL, cache=j.JudgeCache(None))

    def status(model=MODEL, cache=j.JudgeCache(None)):
        return j.gating_status("claim_supported", model, prompts_dir=prompts, calibration_dir=calib)

    # the record was written against the real prompt; the temp copy is identical
    assert status()[0] is True
    assert status("judge/other")[0] is False and "calibrated with" in status("judge/other")[1]
    (prompts / "claim_supported.md").write_text("changed prompt")
    ok, why = status()
    assert not ok and "prompt changed" in why


def test_record_below_threshold_never_gates(dirs):
    prompts, calib = dirs
    calib.mkdir(parents=True)
    j.record_path("claim_supported", calib).write_text(
        json.dumps(
            {"model": MODEL, "prompt_sha256": j.prompt_hash("claim_supported", prompts), "tpr": 0.84, "tnr": 1.0, "passed": True}
        )
    )
    ok, why = j.gating_status("claim_supported", MODEL, prompts_dir=prompts, calibration_dir=calib)
    assert not ok and "below" in why


# --- judge graders over a trial -------------------------------------------------------------------------------------

REPORT = "Acme sells three plans. The Team plan costs 29 EUR per seat [1]. Enterprise is by quote [2].\n\nNo cite here."
LEARNINGS = [
    {"fact": "Team: 29 EUR per seat per month", "source_url": "https://acme.test/pricing"},
    {"fact": "Enterprise is priced by quote", "source_url": "https://acme.test/enterprise"},
]


def ctx(**files):
    return GradeContext(trace=Trace(root=Span("model", "root")), files=files)


def test_claim_supported_judges_each_cited_sentence(dirs):
    t = Fake(reply("yes"), reply("no", "not stated"))
    out = run(
        runner(t, dirs).grade(
            GraderSpec(id="cs", type="judge", params={"judge": "claim_supported"}),
            ctx(**{"report.md": REPORT, "learnings": LEARNINGS}),
        )
    )
    assert not out["passed"] and out["score"] == 0.5 and "1 of 2 no" in out["detail"]
    first, second = t.calls
    assert "Team plan costs 29 EUR" in first.user and "29 EUR per seat per month" in first.user
    assert "Enterprise is priced by quote" in second.user and "Team: 29" not in second.user
    assert "No cite here" not in first.user + second.user


def test_all_yes_passes_and_an_error_fails(dirs):
    spec = GraderSpec(id="cs", type="judge", params={"judge": "claim_supported"})
    files = {"report.md": REPORT, "learnings": LEARNINGS}
    assert run(runner(Fake(reply()), dirs).grade(spec, ctx(**files)))["passed"]
    out = run(runner(Fake("junk"), dirs).grade(spec, ctx(**files)))
    assert not out["passed"] and "errored" in out["detail"]


def test_citation_without_a_learning_gets_a_marker(dirs):
    t = Fake(reply("no"))
    run(
        runner(t, dirs).grade(
            GraderSpec(id="cs", type="judge", params={"judge": "claim_supported"}),
            ctx(**{"report.md": "Fact [7].", "learnings": LEARNINGS}),
        )
    )
    assert "(no learning for this citation)" in t.calls[0].user


def test_missing_inputs_fail_closed_without_a_model_call(dirs):
    t = Fake(reply())
    r = runner(t, dirs)
    for judge, files in [
        ("claim_supported", {}),
        ("no_unsourced_facts", {"report.md": "x"}),
        ("plan_coverage", {"plan": "p"}),
        ("memory_applied", {"memory": "m"}),
        ("recall_probe", {"fact": "f"}),
        ("bogus", {}),
    ]:
        out = run(r.grade(GraderSpec(id="g", type="judge", params={"judge": judge}), ctx(**files)))
        assert not out["passed"], judge
    assert not t.calls


def test_other_extractors(dirs):
    t = Fake(reply())
    r = runner(t, dirs)
    run(r.grade(GraderSpec(id="p", type="judge", params={"judge": "plan_coverage", "request": "Compare A and B"}), ctx(plan="1. A")))
    run(r.grade(GraderSpec(id="u", type="judge", params={"judge": "no_unsourced_facts"}), ctx(**{"report.md": "R", "learnings": LEARNINGS})))
    run(
        r.grade(
            GraderSpec(id="m", type="judge", params={"judge": "memory_applied", "request": "q", "memory": "- use EUR"}),
            ctx(response="Prices in EUR"),
        )
    )
    run(
        r.grade(
            GraderSpec(id="rp", type="judge", params={"judge": "recall_probe", "fact": "port 8443", "question": "which port?"}),
            ctx(probe_answer="8443"),
        )
    )
    users = [c.user for c in t.calls]
    assert "<REQUEST>\nCompare A and B" in users[0] and "<PLAN>\n1. A" in users[0]
    assert "Enterprise is priced by quote" in users[1]
    assert "<MEMORY>\n- use EUR" in users[2] and "Prices in EUR" in users[2]
    assert "<FACT>\nport 8443" in users[3] and "<ANSWER>\n8443" in users[3]


def test_final_assistant_text_comes_from_the_raw_trace(dirs):
    raw = {
        "entries": [
            {"kind": "user", "payload": {"content": "hello?"}},
            {"kind": "assistant", "payload": {"message": {"blocks": [{"type": "text", "text": "8443"}]}}},
            {"kind": "assistant", "payload": {"message": {"blocks": [{"type": "tool_call", "name": "x"}]}}},
        ]
    }
    t = Fake(reply())
    c = GradeContext(trace=Trace(root=Span("model", "root")), raw=raw)
    run(runner(t, dirs).grade(GraderSpec(id="rp", type="judge", params={"judge": "recall_probe", "fact": "f", "question": "q"}), c))
    assert "<ANSWER>\n8443" in t.calls[0].user


def test_grade_all_gating_only_when_calibrated(dirs):
    prompts, calib = dirs
    specs = [
        GraderSpec(id="cs", type="judge", gating=True, params={"judge": "claim_supported"}),
        GraderSpec(id="other", type="budget", params={"max_usd": 1}),
        GraderSpec(id="adv", type="judge", gating=False, params={"judge": "claim_supported"}),
    ]
    c = ctx(**{"report.md": REPORT, "learnings": LEARNINGS})
    r = runner(Fake(reply()), dirs)
    res = run(r.grade_all(specs, c))
    assert [x.grader_id for x in res] == ["cs", "adv"]  # code graders are not its business
    assert res[0].gating is False and "advisory: not calibrated" in res[0].detail and res[0].passed
    assert res[1].gating is False and "advisory" not in res[1].detail

    write_labels(calib, "claim_supported", labelled(20, 20))
    cal.main(["claim_supported"], transport=Fake(by_claim()), calibration_dir=calib, meter=BudgetMeter(5.0), model=MODEL, cache=j.JudgeCache(None))
    res = run(runner(Fake(reply()), dirs).grade_all(specs, c))
    assert res[0].gating is True and res[1].gating is False and "advisory" not in res[0].detail


def test_a_failing_calibrated_judge_fails_a_gating_result(dirs):
    _, calib = dirs
    write_labels(calib, "claim_supported", labelled(20, 20))
    cal.main(["claim_supported"], transport=Fake(by_claim()), calibration_dir=calib, meter=BudgetMeter(5.0), model=MODEL, cache=j.JudgeCache(None))
    spec = GraderSpec(id="cs", type="judge", gating=True, params={"judge": "claim_supported"})
    (res,) = run(runner(Fake(reply("no", "bad")), dirs).grade_all([spec], ctx(**{"report.md": REPORT, "learnings": LEARNINGS})))
    assert res.gating and not res.passed


def test_real_transport_needs_a_key(monkeypatch):
    for v in j.KEY_VARS:
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(j.JudgeConfigError):
        j.llm_direct_transport()
