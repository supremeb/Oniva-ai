"""Tool specs: what the engine is told about each skill (KTD7).

The table is code, not inference. A skill with no entry is served as the most
cautious tool (write, non-idempotent, 30 s, no keys, no DB) and
`test_every_skill_has_a_tool_spec` fails until it is declared.

Classification rules:
  * reads are "none";
  * a write is idempotent only when repeating it under the same key has the same
    effect (a fixed-path overwrite, set-to-value updates);
  * anything uncertain stays write + non-idempotent, so the engine reconciles
    instead of re-running;
  * requires_approval for code execution, deletion and connector writes.
"""
from dataclasses import dataclass
from typing import Dict, Literal

SideEffect = Literal["none", "write"]
Scope = Literal["sandbox", "workspace", "external"]
ApproverRole = Literal["member", "admin"]

WEB_MS = 30_000
SANDBOX_MS = 90_000
BROWSER_MS = 120_000
DEFAULT_MS = 30_000


@dataclass(frozen=True)
class ToolSpec:
    side_effect: SideEffect = "write"
    idempotent: bool = False
    timeout_ms: int = DEFAULT_MS
    requires_approval: bool = False
    # Credentials the tool may receive (provider names); consumed by U4/U5.
    needs_keys: tuple = ()
    needs_db: bool = False
    needs_connectors: bool = False
    # False for interactive-only skills, which the engine has no use for.
    in_manifest: bool = True
    # Rule of Two (KTD6). untrusted_output: the result may carry attacker-controlled text, so reading
    # it taints the run. scope: where a write lands; "sandbox" and "workspace" are not outside-world
    # actions, "external" (the default for an undeclared tool) is. approver_role: who may approve.
    untrusted_output: bool = False
    scope: Scope = "external"
    approver_role: ApproverRole = "member"


DEFAULT_SPEC = ToolSpec()


def _read(timeout_ms: int = DEFAULT_MS, **kw) -> ToolSpec:
    return ToolSpec(side_effect="none", idempotent=True, timeout_ms=timeout_ms, **kw)


def _write(idempotent: bool = False, timeout_ms: int = DEFAULT_MS, **kw) -> ToolSpec:
    return ToolSpec(side_effect="write", idempotent=idempotent, timeout_ms=timeout_ms, **kw)


_INTERACTIVE = ToolSpec(in_manifest=False)

TOOL_SPECS: Dict[str, ToolSpec] = {
    # Web
    "web_search": _read(WEB_MS, needs_keys=("tavily", "parallelai", "exa", "serpapi"), untrusted_output=True),
    "browse_url": _read(WEB_MS, untrusted_output=True),
    "extract_structured_data": _read(untrusted_output=True),
    # A browser task may submit forms or act on a site: uncertain, so write.
    "browser_navigate": _write(timeout_ms=BROWSER_MS, requires_approval=True, untrusted_output=True),
    # Overwrites one fixed workspace path.
    "browser_screenshot": _write(idempotent=True, timeout_ms=BROWSER_MS, untrusted_output=True, scope="workspace"),
    # Code
    "analyze_code": _read(),
    "generate_chart": _read(),
    "execute_python": _write(timeout_ms=SANDBOX_MS, requires_approval=True, needs_db=True, approver_role="admin"),
    # Files (workspace)
    "read_file": _read(untrusted_output=True),
    "list_workspace": _read(),
    "write_file": _write(idempotent=True, scope="workspace"),
    "delete_file": _write(requires_approval=True, scope="workspace"),
    "parse_document": _read(untrusted_output=True),
    "parse_pdf": _read(untrusted_output=True),
    # Document analysis (pure functions of their input)
    "extract_entities": _read(),
    "summarize_document": _read(),
    # Financial and compliance (simulated, pure)
    "fetch_market_data": _read(WEB_MS),
    "get_financial_statements": _read(WEB_MS),
    "analyze_risk_metrics": _read(),
    "fetch_economic_indicators": _read(WEB_MS),
    "get_trading_volume": _read(WEB_MS),
    "kyc_verification": _read(),
    "aml_screening": _read(),
    "sanctions_check": _read(),
    "batch_sanctions_check": _read(),
    "batch_kyc_verification": _read(),
    "regulatory_filing": _read(WEB_MS),
    "risk_assessment": _read(),
    # Memory
    "store_decision": _write(needs_db=True, scope="workspace"),
    "search_past_decisions": _read(needs_db=True, untrusted_output=True),
    "search_entities": _read(needs_db=True, untrusted_output=True),
    "get_workflow_entities": _read(needs_db=True, untrusted_output=True),
    "search_knowledge": _read(needs_db=True, untrusted_output=True),
    # Planning
    "update_todo": _write(idempotent=True, scope="workspace"),
    "create_task": _write(scope="workspace"),
    "update_task": _write(idempotent=True, scope="workspace"),
    "list_tasks": _read(untrusted_output=True),
    # Connectors
    "list_available_connectors": _read(needs_connectors=True),
    "get_connector_schema": _read(needs_connectors=True),
    "invoke_connector_tool": _write(requires_approval=True, needs_connectors=True, untrusted_output=True, approver_role="admin"),
    "linear_create_issue": _write(requires_approval=True, needs_connectors=True, approver_role="admin"),
    "linear_search_issues": _read(needs_connectors=True, untrusted_output=True),
    "servicenow_create_incident": _write(requires_approval=True, needs_connectors=True, approver_role="admin"),
    "servicenow_search_incidents": _read(needs_connectors=True, untrusted_output=True),
    # Deep Work (engine path, U4). Every write replaces by key (round, worker id, heading, path) or
    # is a no-op when repeated, so all are idempotent; they open their own tenant-scoped sessions.
    "dw_start_round": _write(idempotent=True, scope="workspace"),
    "dw_save_learnings": _write(idempotent=True, scope="workspace"),
    "dw_record_reflection": _write(idempotent=True, scope="workspace"),
    "dw_write_section": _write(idempotent=True, scope="workspace"),
    "dw_finalize": _write(idempotent=True, scope="workspace"),
    "dw_get_learnings": _read(untrusted_output=True),
    "dw_get_report": _read(untrusted_output=True),
    # Deep Work sandbox and output tools (engine path, U5). Sandboxes are isolated and the worker cannot
    # ask for approval, so none requires it. Only inspect is a pure read; a repeated publish replaces by
    # filename but stays non-idempotent so the engine reconciles instead of re-running. The E2B key is
    # resolved by the tool itself (env, then the user's stored key), so no needs_keys.
    "shell_exec": _write(timeout_ms=SANDBOX_MS, untrusted_output=True, scope="sandbox"),
    "dw_browser_navigate": _write(timeout_ms=BROWSER_MS, untrusted_output=True, scope="sandbox"),
    "dw_browser_screenshot": _write(timeout_ms=BROWSER_MS, untrusted_output=True, scope="sandbox"),
    "inspect_artifact": _read(SANDBOX_MS, untrusted_output=True),
    "publish_artifact": _write(timeout_ms=SANDBOX_MS, scope="workspace"),
    # Interactive or loop-internal: declared, but not served to the engine.
    "ask_user": _INTERACTIVE,
    "present_plan": _INTERACTIVE,
    "complete_task": _INTERACTIVE,
    "assess_query_complexity": _INTERACTIVE,
}


def spec_for(name: str) -> ToolSpec:
    return TOOL_SPECS.get(name, DEFAULT_SPEC)


def is_declared(name: str) -> bool:
    return name in TOOL_SPECS

