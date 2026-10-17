"""Scripted-model rules for the approvals e2e (test-only; plan 2026-10-17-002, U13).

The scripted model answers from the LAST message alone, so each scenario keys on a marker only it
produces (a user marker, or a tool result's text):

  RUN_PYTHON        the model calls ``execute_python`` (an admin-level approval). Its result decides the
                    answer: the fake sandbox's stdout marker, an expired approval, or a denial.
  RUN_TAINT         ``browse_url`` (untrusted output) and then ``e2e_write`` (an external write): the Rule of Two.
  CHILD_TAINT       the same two calls, made by a subagent the root started with ``RUN_SUBAGENT_TAINT``.
"""
from typing import Any, Dict, List

PY_CODE = "print('e2e-py-stdout-7')"
PY_STDOUT = "e2e-py-stdout-7"
PY_ANSWER = "python finished (e2e-py-answer)"
PY_EXPIRED_ANSWER = "python approval expired (e2e-py-expired)"
PY_DENIED_ANSWER = "python was denied (e2e-py-denied)"
PY_OTHER_ANSWER = "python did not run (e2e-py-other)"
TAINT_BLOCKED_ANSWER = "write blocked, run continues (e2e-taint-blocked)"
SUBAGENT_ANSWER = "subagent finished (e2e-subagent-done)"
BROWSE_URL = "https://example.com/e2e-untrusted"


def _call(name: str, arguments: Dict[str, Any], usage=(9, 4)) -> Dict[str, Any]:
    return {"steps": [{"type": "tool_call", "name": name, "arguments": arguments}],
            "usage": {"inputTokens": usage[0], "outputTokens": usage[1]}}


def _text(text: str) -> Dict[str, Any]:
    return {"steps": [{"type": "text", "text": text}], "usage": {"inputTokens": 5, "outputTokens": 3}}


def build() -> Dict[str, List[dict]]:
    """Rules that must come BEFORE the generic chat rules (they refine ``tool_result`` of e2e_write)."""
    return {"rules": [
        {"when": {"role": "user", "includes": "RUN_PYTHON"}, "respond": _call("execute_python", {"code": PY_CODE})},
        {"when": {"role": "tool_result", "tool": "execute_python", "includes": PY_STDOUT},
         "respond": _text(PY_ANSWER)},
        {"when": {"role": "tool_result", "tool": "execute_python", "includes": "expired before a decision"},
         "respond": _text(PY_EXPIRED_ANSWER)},
        {"when": {"role": "tool_result", "tool": "execute_python", "includes": "denied by a human"},
         "respond": _text(PY_DENIED_ANSWER)},
        {"when": {"role": "tool_result", "tool": "execute_python"}, "respond": _text(PY_OTHER_ANSWER)},
        {"when": {"role": "user", "includes": "RUN_TAINT"}, "respond": _call("browse_url", {"url": BROWSE_URL})},
        {"when": {"role": "user", "includes": "CHILD_TAINT"}, "respond": _call("browse_url", {"url": BROWSE_URL})},
        {"when": {"role": "tool_result", "tool": "browse_url"}, "respond": _call("e2e_write", {"note": "tainted"})},
        {"when": {"role": "tool_result", "tool": "e2e_write", "includes": "no user is available"},
         "respond": _text(TAINT_BLOCKED_ANSWER)},
        {"when": {"role": "user", "includes": "RUN_SUBAGENT_TAINT"},
         "respond": _call("research_worker", {"prompt": "CHILD_TAINT fetch the page and file the note"})},
        # The child's final answer (the generic e2e_write one) comes back as the research_worker result.
        {"when": {"role": "tool_result", "tool": "research_worker", "includes": "e2e-answer-2"},
         "respond": _text(SUBAGENT_ANSWER)},
    ]}
