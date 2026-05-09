"""
Agent tool harness — wraps tool functions with loop detection, credential
scanning, and recitation. Follows the same closure pattern as TodoMiddleware.

Three guardrails, applied in order per tool call:
  1. Loop detection (pre-call): warn at 3x, block at 5x identical calls
  2. Credential scanning (post-call): regex redaction of secrets in output
  3. Recitation (post-call): inject plan.md + todo.md every 7th call
"""

from __future__ import annotations

import functools
import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOP_SOFT_THRESHOLD = 3   # Warning (tool still executes)
LOOP_HARD_THRESHOLD = 5   # Block (tool does not execute)
RECITATION_INTERVAL = 7

# ---------------------------------------------------------------------------
# Credential patterns
# ---------------------------------------------------------------------------

# All patterns use word boundaries (\b) to avoid matching substrings of
# financial identifiers (CUSIP, ISIN, SEDOL, LEI).
CREDENTIAL_PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "openai_key": (
        re.compile(r'\bsk-[A-Za-z0-9]{20,}\b'),
        "OpenAI API key",
    ),
    "anthropic_key": (
        re.compile(r'\bsk-ant-[A-Za-z0-9\-]{20,}\b'),
        "Anthropic API key",
    ),
    "openrouter_key": (
        re.compile(r'\bsk-or-[A-Za-z0-9\-]{20,}\b'),
        "OpenRouter API key",
    ),
    "aws_access_key": (
        re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
        "AWS access key",
    ),
    "private_key": (
        re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'),
        "Private key",
    ),
    "connection_string": (
        re.compile(r'\b(postgresql|redis|mongodb|mysql)://[^\s]+'),
        "Database connection string",
    ),
    "bearer_token": (
        re.compile(r'\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*\b'),
        "Bearer token",
    ),
    "github_token": (
        re.compile(r'\bgh[ps]_[A-Za-z0-9]{36,}\b'),
        "GitHub token",
    ),
    "stripe_key": (
        re.compile(r'\b(sk|pk)_(test|live)_[A-Za-z0-9]{20,}\b'),
        "Stripe API key",
    ),
    "generic_secret": (
        # Min length 32 to reduce false positives on financial data.
        # Only matches key=value patterns where key says "secret", "password", etc.
        # "token" excluded from trigger words to avoid matching "token_count".
        re.compile(
            r'\b(?:api[_-]?key|secret|password)\s*[=:]\s*["\']?'
            r'([A-Za-z0-9\-._~+/]{32,})["\']?',
            re.I,
        ),
        "Generic secret in key=value format",
    ),
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _check_loop(
    tool_name: str,
    args: dict[str, Any],
    call_counts: dict[str, int],
) -> str | None:
    """Check for repeated identical tool calls. Returns message or None."""
    try:
        args_str = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_str = str(args)

    key = f"{tool_name}:{args_str}"
    call_counts[key] = call_counts.get(key, 0) + 1
    count = call_counts[key]

    if count >= LOOP_HARD_THRESHOLD:
        return (
            f"[HARNESS: Loop blocked] '{tool_name}' has been called {count} times "
            f"with the same arguments. This tool call has been blocked. "
            f"Use a different tool or approach."
        )
    elif count >= LOOP_SOFT_THRESHOLD:
        return (
            f"[HARNESS: Loop detected] You have called '{tool_name}' with the same "
            f"arguments {count} times. Consider a different approach or report "
            f"what is blocking you."
        )
    return None


def _scan_credentials(text: str, tool_name: str, session_id: str | None) -> tuple[str, int]:
    """Scan text for credential patterns and redact matches.

    Returns (redacted_text, redaction_count).
    """
    found: list[tuple[str, str, str]] = []
    for name, (pattern, desc) in CREDENTIAL_PATTERNS.items():
        for match in pattern.finditer(text):
            found.append((name, desc, match.group()))

    if not found:
        return text, 0

    redacted = text
    for _name, desc, match_str in found:
        redacted = redacted.replace(match_str, f"[REDACTED: {desc}]")

    logger.warning(
        "Credential detected in tool output",
        extra={
            "tool_name": tool_name,
            "pattern_types": [name for name, _, _ in found],
            "redaction_count": len(found),
            "session_id": session_id,
        },
    )

    return redacted, len(found)


def _scan_dict_values(
    d: dict[str, Any],
    tool_name: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Recursively scan dict string values for credentials."""
    result = {}
    for key, value in d.items():
        if isinstance(value, str):
            scanned, _ = _scan_credentials(value, tool_name, session_id)
            result[key] = scanned
        elif isinstance(value, dict):
            result[key] = _scan_dict_values(value, tool_name, session_id)
        elif isinstance(value, list):
            result[key] = [
                _scan_credentials(item, tool_name, session_id)[0]
                if isinstance(item, str)
                else _scan_dict_values(item, tool_name, session_id)
                if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


async def _maybe_inject_recitation(
    result: str,
    tool_counter: list[int],
    session_id: str,
    tenant_id: str,
) -> str:
    """Inject plan.md + todo.md every RECITATION_INTERVAL tool calls.

    Behavioral parity with TodoMiddleware.create_todo_recitation_wrapper().
    """
    tool_counter[0] += 1
    if tool_counter[0] % RECITATION_INTERVAL != 0:
        return result

    try:
        from app.services.session_storage import get_session_storage

        storage = get_session_storage()
        plan_content = await storage.read(tenant_id, session_id, "plan.md")
        todo_content = await storage.read(tenant_id, session_id, "todo.md")

        if not todo_content and not plan_content:
            return result

        # Skip recitation if files are still default templates
        from app.services.session_memory import _is_default_content
        if _is_default_content(plan_content or "", todo_content or ""):
            return result

        parts = ["\n\n[PROGRESS CHECK]"]
        if plan_content:
            parts.append(f"## Plan\n{plan_content}")
        if todo_content:
            parts.append(f"## Todo\n{todo_content}")
        parts.append("Continue with the next incomplete step.")

        return result + "\n".join(parts)

    except Exception as e:
        logger.debug(f"Todo recitation skipped: {e}")
        return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def wrap_tools_with_harness(
    tools: list[Callable],
    session_id: str,
    tenant_id: str,
) -> list[Callable]:
    """Wrap tool functions with loop detection, credential scanning, and recitation.

    Creates per-run closure state (same pattern as TodoMiddleware's counter_state).
    Safe for concurrent use — each call to wrap_tools_with_harness() creates
    independent state captured by closures.

    Args:
        tools: List of async tool functions to wrap
        session_id: Session ID for recitation (reading plan.md/todo.md)
        tenant_id: Tenant ID for multi-tenant session storage

    Returns:
        List of wrapped tool functions (same length, same order)
    """
    # Per-run mutable state — captured by closures, never shared
    call_counts: dict[str, int] = {}
    tool_counter: list[int] = [0]

    def _wrap_single(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            tool_name = (
                getattr(fn, '__name__', None)
                or getattr(fn, '__skill_name__', 'unknown')
            )

            # --- 1. Loop detection (pre-call) ---
            loop_msg = _check_loop(tool_name, kwargs, call_counts)
            if loop_msg and loop_msg.startswith("[HARNESS: Loop blocked]"):
                return loop_msg  # Hard block — tool does not execute

            # --- 2. Execute tool ---
            result = await fn(*args, **kwargs)

            # --- 2b. Cap oversized tool results (prevent context overflow) ---
            _TOOL_RESULT_MAX_CHARS = 10_000
            if isinstance(result, str) and len(result) > _TOOL_RESULT_MAX_CHARS:
                result = (
                    result[:_TOOL_RESULT_MAX_CHARS]
                    + f"\n[Truncated. Full result: {len(result)} chars. Request specific sections if needed.]"
                )

            # --- 3. Credential scanning (post-call) ---
            if isinstance(result, str):
                result, _ = _scan_credentials(result, tool_name, session_id)
            elif isinstance(result, dict):
                result = _scan_dict_values(result, tool_name, session_id)

            # Append loop warning AFTER credential scanning
            if loop_msg:
                if isinstance(result, str):
                    result = f"{result}\n\n{loop_msg}"
                elif isinstance(result, dict):
                    result["_harness_warning"] = loop_msg

            # --- 4. Recitation (post-call) ---
            if session_id:
                result_str = result if isinstance(result, str) else json.dumps(result, default=str)
                recited = await _maybe_inject_recitation(
                    result_str, tool_counter, session_id, tenant_id,
                )
                # If recitation was injected, use the string form
                if recited != result_str:
                    result = recited
                elif isinstance(result, str):
                    result = result_str

            return result

        return wrapped

    from pydantic_ai.tools import Tool as PydanticTool
    return [fn if isinstance(fn, PydanticTool) else _wrap_single(fn) for fn in tools]
