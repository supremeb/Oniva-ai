"""
XML Tool Call Parser (detection only)

Parses XML-formatted tool calls leaked by models (e.g., DeepSeek via OpenRouter)
that emit tool invocations as plain text instead of using the function-calling API.

SECURITY: leaked text is never executed. Executing it dispatched any registered
skill, bypassing the agent allow-list and harness guards. This module only
detects and strips such text; callers log and drop the calls.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedToolCall:
    tool_name: str
    arguments: dict[str, Any]
    raw_xml: str  # for logging/debugging


# ---------------------------------------------------------------------------
# XML Normalization + Parsing
# ---------------------------------------------------------------------------

# Match <invoke name="...">...</invoke> blocks (after normalization)
_INVOKE_BLOCK_RE = re.compile(
    r'<invoke\s+name\s*=\s*"([^"]+)"\s*>(.*?)</invoke>',
    re.DOTALL | re.IGNORECASE,
)

# Match <parameter name="...">...</parameter> inside an invoke block
_PARAMETER_RE = re.compile(
    r'<parameter\s+name\s*=\s*"([^"]+)"\s*>(.*?)</parameter>',
    re.DOTALL | re.IGNORECASE,
)


def _normalize_xml(text: str) -> str:
    """Normalize various XML tool-call formats to a canonical <invoke> form."""
    # Strip DSML wrappers: <|DSML|> ... </|DSML|>
    text = re.sub(r'<\|?\s*DSML\s*\|?\s*>', '', text, flags=re.IGNORECASE)
    # Strip function_calls wrappers
    text = re.sub(r'</?[^>]*function_calls[^>]*>', '', text, flags=re.IGNORECASE)
    # <functioninvoke name="..."> → <invoke name="...">
    text = re.sub(r'<functioninvoke\b', '<invoke', text, flags=re.IGNORECASE)
    # <function invoke name="..."> (space variant)
    text = re.sub(r'<function\s+invoke\b', '<invoke', text, flags=re.IGNORECASE)
    # function_cinvoke → invoke (mangled variant)
    text = re.sub(r'function_c\w*invoke\b', 'invoke', text, flags=re.IGNORECASE)
    # </functioninvoke> → </invoke>
    text = re.sub(r'</functioninvoke\s*>', '</invoke>', text, flags=re.IGNORECASE)
    # </function invoke> → </invoke>
    text = re.sub(r'</function\s+invoke\s*>', '</invoke>', text, flags=re.IGNORECASE)
    # Broken closing tags: invfunction_calls>, andfunction_calls>
    text = re.sub(r'(?:inv|and)function_calls\s*>', '', text, flags=re.IGNORECASE)
    return text


def _coerce_value(raw: str, expected_type: str) -> Any:
    """Coerce a string value to the expected JSON schema type."""
    if expected_type == "number":
        try:
            return float(raw) if '.' in raw else int(raw)
        except (ValueError, TypeError):
            return raw
    elif expected_type == "boolean":
        return raw.lower() in ("true", "1", "yes")
    elif expected_type == "array":
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return [raw]
    elif expected_type == "object":
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return raw
    # Default: string
    return raw


def _coerce_args(args: dict[str, Any], skill_name: str) -> dict[str, Any]:
    """Coerce string arguments to the types declared in the skill's schema."""
    from app.agents.skills.registry import SkillRegistry

    schemas = SkillRegistry.get_tools_as_openai_functions([skill_name])
    if not schemas:
        return args

    properties = schemas[0].get("function", {}).get("parameters", {}).get("properties", {})
    coerced = {}
    for key, value in args.items():
        if isinstance(value, str) and key in properties:
            expected_type = properties[key].get("type", "string")
            coerced[key] = _coerce_value(value, expected_type)
        else:
            coerced[key] = value
    return coerced


def parse_xml_tool_calls(xml_text: str) -> list[ParsedToolCall]:
    """Parse XML tool-call text into structured ParsedToolCall objects.

    Returns an empty list if no valid tool calls are found (caller falls
    back to the existing strip-only behavior).
    """
    normalized = _normalize_xml(xml_text)
    results: list[ParsedToolCall] = []

    for match in _INVOKE_BLOCK_RE.finditer(normalized):
        tool_name = match.group(1).strip()
        body = match.group(2)

        # Try extracting parameters from <parameter> tags
        params: dict[str, Any] = {}
        for pm in _PARAMETER_RE.finditer(body):
            param_name = pm.group(1).strip()
            param_value = pm.group(2).strip()
            # Try JSON-parsing the value (handles dicts, lists, numbers)
            try:
                param_value = json.loads(param_value)
            except (json.JSONDecodeError, TypeError):
                pass
            params[param_name] = param_value

        # Fallback: if no <parameter> tags, try parsing entire body as JSON
        if not params:
            body_stripped = body.strip()
            if body_stripped:
                try:
                    parsed_body = json.loads(body_stripped)
                    if isinstance(parsed_body, dict):
                        params = parsed_body
                except (json.JSONDecodeError, TypeError):
                    pass

        # Coerce argument types based on skill schema
        try:
            params = _coerce_args(params, tool_name)
        except Exception:
            pass  # Best-effort coercion

        results.append(ParsedToolCall(
            tool_name=tool_name,
            arguments=params,
            raw_xml=match.group(0),
        ))

    if results:
        logger.info(
            f"XML tool rescue: parsed {len(results)} tool call(s) from leaked XML: "
            f"{[r.tool_name for r in results]}"
        )

    return results
