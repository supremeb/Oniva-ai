"""Tool policy: the pure Rule-of-Two matrix and the argument hash (Approvals & Policy plan, U3 / KTD6, KTD2).

``decide_tool_call`` is pure: the authorize handler gathers the spec, the tenant's ``approvals``
settings and whether a human owns the root conversation, and this module answers
``allow`` / ``block`` / ``requireApproval``. ``canonical_args_hash`` is shared with the engine
(``engine/src/worker/args-hash.ts``) through ``engine/test/support/args-hash-vectors.json``.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional

from app.internal_api.tool_specs import ToolSpec

# Mirrors INTERNAL_NATIVE_TOOLS in engine/src/tools/registry.ts; the shared fixture
# engine/test/support/internal-native-tools.json keeps the two lists from drifting.
NATIVE_INTERNAL_TOOLS = frozenset({"ask_user", "update_todo", "present_plan", "complete_task"})

DEFAULT_TTL_HOURS = 168

Decision = Literal["allow", "block", "requireApproval"]


@dataclass(frozen=True)
class TenantPolicy:
    ttl_hours: int = DEFAULT_TTL_HOURS  # 0 means the approval never expires
    require_separate_approver: bool = False
    rule_of_two: bool = True

    @classmethod
    def from_settings(cls, settings: Optional[dict]) -> "TenantPolicy":
        cfg = (settings or {}).get("approvals") or {}
        ttl = cfg.get("ttl_hours", DEFAULT_TTL_HOURS)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0:
            ttl = DEFAULT_TTL_HOURS
        return cls(
            ttl_hours=ttl,
            require_separate_approver=cfg.get("require_separate_approver") is True,
            rule_of_two=cfg.get("rule_of_two") is not False,
        )

    @property
    def ttl_ms(self) -> Optional[int]:
        return None if self.ttl_hours == 0 else self.ttl_hours * 3_600_000


@dataclass(frozen=True)
class ToolCallInfo:
    tool_name: str
    tool_source: Literal["gateway", "native"]
    tainted: bool = False
    agent_role: Optional[str] = None


@dataclass(frozen=True)
class ToolDecision:
    decision: Decision
    code: Optional[str] = None  # block only
    reason: Optional[str] = None
    rule: Optional[str] = None  # requireApproval only: ledger rule
    approver_role: str = "member"
    ttl_ms: Optional[int] = None


_ALLOW = ToolDecision("allow")


def _block(code: str, reason: str) -> ToolDecision:
    return ToolDecision("block", code=code, reason=reason)


def decide_tool_call(
    spec: Optional[ToolSpec], request: ToolCallInfo, tenant_policy: TenantPolicy, attended: bool
) -> ToolDecision:
    """The KTD6 matrix, evaluated in order. ``spec`` is ``None`` for a tool the backend does not know."""
    ttl = tenant_policy.ttl_ms

    # 1. Unknown or disabled gateway tool.
    if request.tool_source == "gateway" and (spec is None or not spec.in_manifest):
        return _block("unknown_tool", f"Tool '{request.tool_name}' is not available.")

    # 2. Native tools: the internal allowlist is allowed; everything else is untrusted by default.
    if request.tool_source == "native":
        if request.tool_name in NATIVE_INTERNAL_TOOLS:
            return _ALLOW
        if not attended:
            return _block("no_approver", "No user is available to approve this tool call.")
        return ToolDecision("requireApproval", reason="Tool is not on the internal allowlist",
                            rule="requires_approval", approver_role="member", ttl_ms=ttl)

    assert spec is not None
    # 3. requires_approval specs (4: unattended runs have nobody to ask).
    if spec.requires_approval:
        if not attended:
            return _block("no_approver", "No user is available to approve this tool call.")
        return ToolDecision("requireApproval", reason=f"{request.tool_name} requires approval",
                            rule="requires_approval", approver_role=spec.approver_role, ttl_ms=ttl)

    # 5. Sandboxes have no external side effects, even when the run is tainted.
    if spec.scope == "sandbox":
        return _ALLOW
    # 6. Not tainted.
    if not request.tainted:
        return _ALLOW
    # 7. Tainted external write.
    if spec.side_effect == "write" and spec.scope == "external" and tenant_policy.rule_of_two:
        if not attended:
            return _block("untrusted_input_no_approver",
                          "This run read untrusted content and no user is available to approve this action.")
        return ToolDecision("requireApproval", reason="This run read untrusted content",
                            rule="rule_of_two", approver_role="member", ttl_ms=ttl)
    # 8. Reads and workspace writes.
    return _ALLOW


# --- argument hash ---------------------------------------------------------------------------


def _js_number(n: float) -> str:
    """``Number.prototype.toString`` for a finite double (what ``JSON.stringify`` emits)."""
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    d = Decimal(repr(abs(n)))
    digits = "".join(str(x) for x in d.as_tuple().digits).rstrip("0") or "0"
    # value = 0.digits * 10**point
    point = d.adjusted() + 1
    k = len(digits)
    if k <= point <= 21:
        return sign + digits + "0" * (point - k)
    if 0 < point <= 21:
        return sign + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * (-point) + digits
    e = point - 1
    exp = f"e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + (digits[0] if k == 1 else digits[0] + "." + digits[1:]) + exp


def _utf16_key(s: str) -> bytes:
    return s.encode("utf-16-be", "surrogatepass")  # JS sorts keys by UTF-16 code unit


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"  # JSON.stringify(NaN | Infinity)
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(str(k), ensure_ascii=False) + ":" + _canonical(value[k])
            for k in sorted(value, key=lambda k: _utf16_key(str(k)))
        ) + "}"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return _canonical(value)


def canonical_args_hash(arguments: Any) -> str:
    """sha256 hex of the canonical form of the (unredacted) final tool arguments (KTD2)."""
    return hashlib.sha256(_canonical(arguments).encode("utf-8")).hexdigest()
