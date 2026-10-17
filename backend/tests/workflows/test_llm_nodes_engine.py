"""U14: LLM, classifier, guardrail, parameter-extractor and agent nodes as prepare/finish (R8, R12)."""
import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.internal_api import workflow_contract as wc
from app.node_executors import NodeExecutionContext, NodeExecutionResult
from app.node_executors.agent_executor import AgentExecutor
from app.node_executors.classifier_executor import ClassifierExecutor
from app.node_executors.guardrail_executor import GuardrailExecutor
from app.node_executors.llm_executor import LLMExecutor
from app.node_executors.parameter_extractor_executor import ParameterExtractorExecutor
from app.services import llm_direct

USER_ID = str(uuid.uuid4())
NODE_DIR = Path(__file__).resolve().parents[2] / "app" / "node_executors"
FIVE = ["llm", "classifier", "guardrail", "parameter_extractor", "agent"]


def ctx(node_type, inputs=None, variables=None, **data):
    return NodeExecutionContext(
        node_id="n1", node_type=node_type, node_data=data, inputs=inputs or {}, variables=variables or {},
        user_id=USER_ID, tenant_id=str(uuid.uuid4()),
    )


def fin(answer):
    return wc.validate_finish_input({"contractVersion": 1, "kind": "llm", "answer": answer})


def valid_llm(body):
    """The prepare body validates against the U33 contract."""
    parsed = wc.validate_prepare_response(body)
    assert parsed.kind == "llm"
    return parsed


# ---- classifier ------------------------------------------------------------------------------

CLASSES = [{"id": "billing", "name": "Billing"}, {"id": "tech", "name": "Technical"}, {"id": "other", "name": "Other"}]


async def test_classifier_prepare_returns_prompt_with_the_label_set():
    c = ctx("classifier", variables={"sys.query": "my invoice is wrong"}, classes=CLASSES,
            inputText="{{sys.query}}", instructions="be brief")
    body = await ClassifierExecutor().prepare(c, MagicMock())
    parsed = valid_llm(body)
    for cls in CLASSES:
        assert f"- {cls['id']}: {cls['name']}" in parsed.system
    assert "be brief" in parsed.system
    assert "my invoice is wrong" in parsed.prompt
    assert parsed.responseFormat == "json"
    assert parsed.tools is None
    assert not {"apiKey", "api_key", "key"} & set(body)  # a tier or model hint only, never a key


async def test_classifier_finish_selects_the_label_handle():
    c = ctx("classifier", variables={"sys.query": "q"}, classes=CLASSES, inputText="{{sys.query}}")
    answer = json.dumps({"class_id": "tech", "class_name": "Technical", "confidence": 0.9})
    result = await ClassifierExecutor().finish(c, MagicMock(), fin({"text": answer}))
    assert result.status == "success"
    assert result.next_node_id == "tech"  # the gateway turns this into selectedHandles = ["tech"]
    assert result.output["class_id"] == "tech" and result.output["confidence"] == 0.9


async def test_classifier_unknown_label_routes_to_the_default_handle():
    c = ctx("classifier", variables={"sys.query": "q"}, classes=CLASSES, inputText="{{sys.query}}",
            defaultClass="other")
    answer = json.dumps({"class_id": "martian", "class_name": "?", "confidence": 0.99})
    result = await ClassifierExecutor().finish(c, MagicMock(), fin(answer))
    assert result.status == "success"
    assert result.next_node_id == "other"
    assert result.output["class_id"] == "other"


async def test_classifier_unparseable_answer_with_no_default_uses_the_first_class():
    c = ctx("classifier", variables={"sys.query": "q"}, classes=CLASSES, inputText="{{sys.query}}")
    result = await ClassifierExecutor().finish(c, MagicMock(), fin("no idea"))
    assert result.next_node_id == "billing"
    assert result.output["raw_response"] == "no idea"


async def test_classifier_without_classes_fails_in_prepare():
    result = await ClassifierExecutor().prepare(ctx("classifier", classes=[]), MagicMock())
    assert isinstance(result, NodeExecutionResult) and result.status == "failed"


# ---- guardrail -------------------------------------------------------------------------------

LLM_RULE = {"type": "llm", "name": "safe", "prompt": "Is the content safe? Answer YES or NO.", "expectedValue": "YES"}
REGEX_RULE = {"type": "regex", "name": "no-ssn", "pattern": r"\d{3}-\d{2}-\d{4}", "shouldMatch": False}


def guard(**data):
    return ctx("guardrail", inputs={"up": {"text": "hello world"}}, **data)


async def test_guardrail_block_verdict_is_blocked_with_the_reason():
    c = guard(rules=[LLM_RULE], failureAction="reject")
    result = await GuardrailExecutor().finish(c, MagicMock(), fin(json.dumps({"answers": ["NO"]})))
    assert result.status == "blocked"
    assert "safe" in result.error and "failed" in result.error.lower()
    assert result.output["passed"] is False


async def test_guardrail_pass_verdict_is_success():
    c = guard(rules=[LLM_RULE, REGEX_RULE])
    result = await GuardrailExecutor().finish(c, MagicMock(), fin(json.dumps({"answers": ["YES"]})))
    assert result.status == "success"
    assert result.output["passed"] is True and result.output["total_rules"] == 2


async def test_guardrail_prepare_asks_every_llm_rule_in_one_generation():
    second = {**LLM_RULE, "name": "polite", "prompt": "Is it polite? Answer YES or NO."}
    body = await GuardrailExecutor().prepare(guard(rules=[LLM_RULE, REGEX_RULE, second]), MagicMock())
    parsed = valid_llm(body)
    assert "Is the content safe?" in parsed.prompt and "Is it polite?" in parsed.prompt
    assert "hello world" in parsed.prompt and "exactly 2 answer" in parsed.prompt


async def test_guardrail_without_llm_rules_is_decided_in_prepare():
    result = await GuardrailExecutor().prepare(guard(rules=[REGEX_RULE]), MagicMock())
    assert isinstance(result, NodeExecutionResult) and result.status == "success"
    blocked = await GuardrailExecutor().prepare(
        ctx("guardrail", inputs={"up": {"text": "123-45-6789"}}, rules=[REGEX_RULE]), MagicMock()
    )
    assert blocked.status == "blocked"


async def test_guardrail_flag_action_does_not_block():
    c = guard(rules=[LLM_RULE], failureAction="flag")
    result = await GuardrailExecutor().finish(c, MagicMock(), fin("NO"))  # a lone rule accepts plain text
    assert result.status == "success" and result.output["passed"] is False


# ---- parameter extractor ---------------------------------------------------------------------

PARAMS = [
    {"name": "name", "type": "string", "required": True},
    {"name": "age", "type": "number"},
]


def extractor(**extra):
    return ctx("parameter_extractor", variables={"msg": "Ada is 36"}, inputText="{{msg}}", parameters=PARAMS, **extra)


async def test_extractor_valid_json_returns_the_variables():
    result = await ParameterExtractorExecutor().finish(
        extractor(), MagicMock(), fin('```json\n{"name": "Ada", "age": "36"}\n```')
    )
    assert result.status == "success"
    assert result.output["parameters"] == {"name": "Ada", "age": 36}
    assert result.output["extraction_method"] == "llm"


async def test_extractor_invalid_json_fails_with_a_parse_error_and_keeps_the_raw_text():
    result = await ParameterExtractorExecutor().finish(extractor(), MagicMock(), fin("Sure! The name is Ada."))
    assert result.status == "failed"
    assert "parse" in result.error.lower()
    assert result.output == {"raw_text": "Sure! The name is Ada."}


async def test_extractor_prepare_is_an_llm_body_and_regex_strategy_is_final():
    parsed = valid_llm(await ParameterExtractorExecutor().prepare(extractor(), MagicMock()))
    assert "Ada is 36" in parsed.prompt and parsed.responseFormat == "json"
    final = await ParameterExtractorExecutor().prepare(extractor(extractionStrategy="regex"), MagicMock())
    assert isinstance(final, NodeExecutionResult) and final.output["extraction_method"] == "regex"


async def test_extractor_combined_falls_back_to_regex_on_invalid_json():
    c = ctx("parameter_extractor", variables={"msg": "name: Ada"}, inputText="{{msg}}", parameters=PARAMS,
            extractionStrategy="combined")
    result = await ParameterExtractorExecutor().finish(c, MagicMock(), fin("not json"))
    assert result.status == "success" and result.output["parameters"]["name"] == "Ada"


# ---- llm node --------------------------------------------------------------------------------


async def test_llm_prepare_substitutes_variables_and_finish_matches_the_legacy_output():
    c = ctx("llm", inputs={"up": {"text": "Q3 numbers"}}, variables={"company": "Acme"},
            systemPrompt="You work for {{company}}.", userPrompt="Summarise: {{up.text}}", model="gpt-4o-mini")
    parsed = valid_llm(await LLMExecutor().prepare(c, MagicMock()))
    assert parsed.system == "You work for Acme."
    assert parsed.prompt == "Summarise: Q3 numbers"
    assert parsed.model == "openai/gpt-4o-mini"

    result = await LLMExecutor().finish(c, MagicMock(), fin({"text": "Done.", "tokensUsed": 12}))
    assert result.status == "success" and result.tokens_used == 12
    # byte for byte the legacy output shape
    assert result.output == {"text": "Done.", "model": "openai/gpt-4o-mini", "message": "Done."}


async def test_llm_legacy_execute_runs_the_same_prompt_through_llm_direct():
    c = ctx("llm", systemPrompt="sys", userPrompt="hi {{x}}", variables={"x": "there"}, model="openai/gpt-4o-mini")
    captured = {}

    async def complete(request, provider):
        captured["request"], captured["provider"] = request, provider
        return llm_direct.DirectResponse(content="hello", model="gpt-4o-mini", input_tokens=3, output_tokens=2)

    with patch("app.services.llm_gateway.resolve_key_with_fallback", AsyncMock(return_value=("k", "openai/gpt-4o-mini"))), \
         patch.object(llm_direct, "complete", complete):
        result = await LLMExecutor().execute(c, MagicMock())
    assert result.status == "success", result.error
    assert result.output["text"] == "hello" and result.tokens_used == 5
    assert [m["content"] for m in captured["request"].messages] == ["sys", "hi there"]
    assert captured["provider"].provider_type == "openai"


async def test_legacy_execute_without_a_key_fails_cleanly():
    c = ctx("llm", userPrompt="hi")
    with patch("app.services.llm_gateway.resolve_key_with_fallback", AsyncMock(return_value=(None, "openai/gpt-4o-mini"))):
        result = await LLMExecutor().execute(c, MagicMock())
    assert result.status == "failed" and "API key not configured" in result.error


# ---- agent -----------------------------------------------------------------------------------


def custom_agent(**kw):
    base = dict(
        id=uuid.uuid4(), name="Researcher", tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), is_active=True,
        model="openai/gpt-4o-mini", system_prompt="be helpful", skills=["web_search", "not_in_manifest"],
        connector_ids=None, usage_count=0, last_used_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def agent_db(agent):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=agent)))
    db.commit = AsyncMock()
    return db


def agent_ctx(agent, **extra):
    return ctx("agent", agentId=str(agent.id), userPrompt="find {{topic}}", variables={"topic": "x"}, **extra)


MODULE = "app.node_executors.agent_executor"


def agent_patches():
    return (
        patch(f"{MODULE}.get_redis_service", AsyncMock()),
        patch(f"{MODULE}.AgentCircuitBreaker", return_value=SimpleNamespace(check_health=AsyncMock())),
        patch.object(AgentExecutor, "manifest_tool_names", AsyncMock(return_value={"web_search", "browse_url"})),
    )


async def test_agent_prepare_max_rounds_equals_max_iterations_and_drops_tools_outside_the_manifest(caplog):
    agent = custom_agent()
    p1, p2, p3 = agent_patches()
    with p1, p2, p3, caplog.at_level(logging.WARNING):
        body = await AgentExecutor().prepare(agent_ctx(agent, maxIterations=4), agent_db(agent))
    parsed = valid_llm(body)
    assert parsed.maxRounds == 4
    assert parsed.tools == ["web_search"]
    assert parsed.replaySafe is False
    assert parsed.prompt == "find x"
    assert parsed.model == "openai/gpt-4o-mini"
    assert "not_in_manifest" in caplog.text


async def test_agent_default_max_rounds_and_no_tools_is_replay_safe():
    agent = custom_agent(skills=[])
    p1, p2, p3 = agent_patches()
    with p1, p2, p3:
        parsed = valid_llm(await AgentExecutor().prepare(agent_ctx(agent), agent_db(agent)))
    assert parsed.tools is None and parsed.maxRounds is None and parsed.replaySafe is True


async def test_agent_finish_builds_the_output_from_the_child_usage():
    agent = custom_agent()
    db = agent_db(agent)
    p1, p2, p3 = agent_patches()
    with p1, p2, p3:
        result = await AgentExecutor().finish(
            agent_ctx(agent, maxIterations=4), db, fin({"text": "found it", "tokensUsed": 321})
        )
    assert result.status == "success"
    assert result.tokens_used == 321
    assert result.output["response"] == "found it"
    assert result.output["skills_used"] == ["web_search"]
    assert agent.usage_count == 1


async def test_agent_prepare_failures_are_final_failed():
    agent = custom_agent(is_active=False)
    result = await AgentExecutor().prepare(agent_ctx(agent), agent_db(agent))
    assert isinstance(result, NodeExecutionResult) and result.status == "failed" and "not active" in result.error
    result = await AgentExecutor().prepare(ctx("agent", userPrompt="x"), MagicMock())
    assert result.status == "failed" and "Agent ID" in result.error


# ---- no litellm ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["llm", "classifier", "guardrail", "parameter_extractor", "agent"])
def test_the_five_executors_do_not_mention_litellm(name):
    text = (NODE_DIR / f"{name}_executor.py").read_text().lower()
    assert "litellm" not in text


def test_importing_the_five_executors_does_not_import_litellm():
    import subprocess
    import sys

    code = (
        "import sys, app.node_executors.llm_executor, app.node_executors.classifier_executor, "
        "app.node_executors.guardrail_executor, app.node_executors.parameter_extractor_executor, "
        "app.node_executors.agent_executor\n"
        "bad = [m for m in sys.modules if m == 'litellm' or m.startswith('litellm.')]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=NODE_DIR.parents[1]).returncode == 0
