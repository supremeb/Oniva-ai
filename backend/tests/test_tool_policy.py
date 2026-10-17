"""U3 - the pure Rule-of-Two matrix (KTD6) and the canonical argument hash (KTD2)."""
import json
from pathlib import Path

import pytest

from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec
from app.services.tool_policy import (
    NATIVE_INTERNAL_TOOLS,
    TenantPolicy,
    ToolCallInfo,
    canonical_args_hash,
    canonical_json,
    decide_tool_call,
)

ENGINE_SUPPORT = Path(__file__).resolve().parents[2] / "engine" / "test" / "support"
POLICY = TenantPolicy()


def call(name, tainted=False, source="gateway", role=None):
    return ToolCallInfo(tool_name=name, tool_source=source, tainted=tainted, agent_role=role)


def decide(name, tainted=False, attended=True, policy=POLICY, source="gateway", role=None):
    spec = TOOL_SPECS.get(name)
    return decide_tool_call(spec, call(name, tainted, source, role), policy, attended)


def test_read_tool_allows_tainted_or_not():
    assert decide("web_search").decision == "allow"
    assert decide("web_search", tainted=True).decision == "allow"


def test_requires_approval_carries_the_spec_role():
    py = decide("execute_python")
    assert (py.decision, py.approver_role, py.rule) == ("requireApproval", "admin", "requires_approval")
    assert decide("delete_file").approver_role == "member"
    assert decide("invoke_connector_tool").approver_role == "admin"


def test_tainted_external_write_requires_approval_under_rule_of_two():
    spec = ToolSpec(side_effect="write", scope="external")
    d = decide_tool_call(spec, call("some_write", tainted=True), POLICY, attended=True)
    assert (d.decision, d.rule, d.reason) == ("requireApproval", "rule_of_two", "This run read untrusted content")
    assert d.approver_role == "member"


def test_worker_conversation_is_requireApproval_not_block_when_attended():
    spec = ToolSpec(side_effect="write", scope="external")
    d = decide_tool_call(spec, call("some_write", tainted=True, role="research_worker"), POLICY, attended=True)
    assert d.decision == "requireApproval"


def test_unattended_blocks_with_the_cause():
    spec = ToolSpec(side_effect="write", scope="external")
    d = decide_tool_call(spec, call("some_write", tainted=True), POLICY, attended=False)
    assert (d.decision, d.code) == ("block", "untrusted_input_no_approver")
    d = decide("execute_python", attended=False)
    assert (d.decision, d.code) == ("block", "no_approver")
    d = decide("mystery_native", source="native", attended=False)
    assert (d.decision, d.code) == ("block", "no_approver")


def test_unattended_still_allows_reads_sandbox_and_allowlisted_native():
    assert decide("web_search", tainted=True, attended=False).decision == "allow"
    assert decide("shell_exec", tainted=True, attended=False).decision == "allow"
    assert decide("update_todo", source="native", attended=False).decision == "allow"


@pytest.mark.parametrize("name", ["shell_exec", "dw_browser_navigate"])
def test_sandbox_scope_allows_even_when_tainted(name):
    assert decide(name, tainted=True, role="research_worker").decision == "allow"


def test_tainted_workspace_write_allows():
    assert decide("write_file", tainted=True).decision == "allow"


def test_untainted_external_write_without_approval_allows():
    spec = ToolSpec(side_effect="write", scope="external")
    assert decide_tool_call(spec, call("some_write"), POLICY, attended=True).decision == "allow"


def test_native_allowlist_and_default_untrusted():
    assert decide("update_todo", source="native").decision == "allow"
    assert decide("spawn_stub", source="native").decision == "requireApproval"
    d = decide("mystery_native", source="native")
    assert (d.decision, d.rule, d.reason) == ("requireApproval", "requires_approval",
                                              "Tool is not on the internal allowlist")


def test_unknown_gateway_tool_blocks():
    d = decide("no_such_tool")
    assert (d.decision, d.code) == ("block", "unknown_tool")
    # a declared but not-served tool is also unknown to the gateway
    assert decide("present_plan").code == "unknown_tool"


def test_rule_of_two_opt_out_keeps_requires_approval():
    off = TenantPolicy(rule_of_two=False)
    spec = ToolSpec(side_effect="write", scope="external")
    assert decide_tool_call(spec, call("some_write", tainted=True), off, attended=True).decision == "allow"
    assert decide("execute_python", policy=off).decision == "requireApproval"


def test_ttl_from_tenant_policy():
    assert decide("execute_python").ttl_ms == 168 * 3_600_000
    assert decide("execute_python", policy=TenantPolicy(ttl_hours=0)).ttl_ms is None
    assert TenantPolicy.from_settings({"approvals": {"ttl_hours": 2}}).ttl_ms == 7_200_000
    assert TenantPolicy.from_settings({"approvals": {"ttl_hours": "x"}}).ttl_hours == 168
    assert TenantPolicy.from_settings(None).rule_of_two is True


# --- classification ------------------------------------------------------------------------

UNTRUSTED = {"web_search", "browse_url", "extract_structured_data", "browser_navigate", "browser_screenshot",
             "dw_browser_navigate", "dw_browser_screenshot", "search_knowledge", "read_file", "parse_document",
             "parse_pdf", "linear_search_issues", "servicenow_search_incidents", "invoke_connector_tool",
             "inspect_artifact"}
SANDBOX = {"shell_exec", "dw_browser_navigate", "dw_browser_screenshot"}
WORKSPACE = {"write_file", "delete_file", "update_todo", "create_task", "update_task", "store_decision",
             "browser_screenshot", "publish_artifact", "dw_start_round", "dw_save_learnings",
             "dw_record_reflection", "dw_write_section", "dw_finalize"}


def test_every_spec_has_an_explicit_scope_and_untrusted_decision():
    for name, spec in TOOL_SPECS.items():
        assert spec.scope in ("sandbox", "workspace", "external"), name
        assert isinstance(spec.untrusted_output, bool), name
        assert spec.approver_role in ("member", "admin"), name
        expected_scope = "sandbox" if name in SANDBOX else "workspace" if name in WORKSPACE else "external"
        assert spec.scope == expected_scope, name
        assert spec.untrusted_output == (name in UNTRUSTED), name


def test_native_internal_tools_match_the_engine_fixture():
    engine = json.loads((ENGINE_SUPPORT / "internal-native-tools.json").read_text())
    assert NATIVE_INTERNAL_TOOLS == frozenset(engine)


# --- canonical hash ------------------------------------------------------------------------

VECTORS = json.loads((ENGINE_SUPPORT / "args-hash-vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_canonical_args_hash_matches_shared_vectors(case):
    assert canonical_json(case["value"]) == case["canonical"]
    assert canonical_args_hash(case["value"]) == case["hash"]


def test_hash_is_key_order_independent_and_value_sensitive():
    assert canonical_args_hash({"a": 1, "b": {"y": 1, "x": 2}}) == canonical_args_hash({"b": {"x": 2, "y": 1}, "a": 1})
    assert canonical_args_hash({"a": 1}) != canonical_args_hash({"a": 2})


def test_float_formatting_follows_javascript():
    assert canonical_json([1.0, 1e21, 1e-7, 0.000001, 1.5e300, -0.0, 123456789.125]) == \
        "[1,1e+21,1e-7,0.000001,1.5e+300,0,123456789.125]"
