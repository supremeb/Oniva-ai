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


DEFAULT_SPEC = ToolSpec()


def _read(timeout_ms: int = DEFAULT_MS, **kw) -> ToolSpec:
    return ToolSpec(side_effect="none", idempotent=True, timeout_ms=timeout_ms, **kw)


def _write(idempotent: bool = False, timeout_ms: int = DEFAULT_MS, **kw) -> ToolSpec:
    return ToolSpec(side_effect="write", idempotent=idempotent, timeout_ms=timeout_ms, **kw)


_INTERACTIVE = ToolSpec(in_manifest=False)

TOOL_SPECS: Dict[str, ToolSpec] = {
    # Web
    "web_search": _read(WEB_MS, needs_keys=("tavily", "parallelai", "exa", "serpapi")),
    "browse_url": _read(WEB_MS),
    "extract_structured_data": _read(),
    # A browser task may submit forms or act on a site: uncertain, so write.
    "browser_navigate": _write(timeout_ms=BROWSER_MS, requires_approval=True),
    # Overwrites one fixed workspace path.
    "browser_screenshot": _write(idempotent=True, timeout_ms=BROWSER_MS),
    # Code
    "analyze_code": _read(),
    "generate_chart": _read(),
    "execute_python": _write(timeout_ms=SANDBOX_MS, requires_approval=True, needs_db=True),
    # Files (workspace)
    "read_file": _read(),
    "list_workspace": _read(),
    "write_file": _write(idempotent=True),
    "delete_file": _write(requires_approval=True),
    "parse_document": _read(),
    "parse_pdf": _read(),
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
    "store_decision": _write(needs_db=True),
    "search_past_decisions": _read(needs_db=True),
    "search_entities": _read(needs_db=True),
    "get_workflow_entities": _read(needs_db=True),
    "search_knowledge": _read(needs_db=True),
    # Planning
    "update_todo": _write(idempotent=True),
    "create_task": _write(),
    "update_task": _write(idempotent=True),
    "list_tasks": _read(),
    # Connectors
    "list_available_connectors": _read(needs_connectors=True),
    "get_connector_schema": _read(needs_connectors=True),
    "invoke_connector_tool": _write(requires_approval=True, needs_connectors=True),
    "linear_create_issue": _write(requires_approval=True, needs_connectors=True),
    "linear_search_issues": _read(needs_connectors=True),
    "servicenow_create_incident": _write(requires_approval=True, needs_connectors=True),
    "servicenow_search_incidents": _read(needs_connectors=True),
    # Deep Work (engine path, U4). Every write replaces by key (round, worker id, heading, path) or
    # is a no-op when repeated, so all are idempotent; they open their own tenant-scoped sessions.
    "dw_start_round": _write(idempotent=True),
    "dw_save_learnings": _write(idempotent=True),
    "dw_record_reflection": _write(idempotent=True),
    "dw_write_section": _write(idempotent=True),
    "dw_finalize": _write(idempotent=True),
    "dw_get_learnings": _read(),
    "dw_get_report": _read(),
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

