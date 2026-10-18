"""The scripted model for the workflow e2e scenarios (test-only).

A workflow ``llm`` node runs as a tool-less child conversation whose only user message is the node's prompt, so
one rule keyed on the marker in the prompt answers it. The token counts are distinct from every other script's,
so a ledger row can be matched to the node request that made it.
"""
from typing import Any, Dict

LLM_MARKER = "WFLLM_SUMMARY"
LLM_ANSWER = "Workflow summary (e2e-wf-llm)."
LLM_TOKENS = (301, 17)


def build() -> Dict[str, Any]:
    return {"rules": [{"when": {"role": "user", "includes": LLM_MARKER},
                       "respond": {"steps": [{"type": "text", "text": LLM_ANSWER}],
                                   "usage": {"inputTokens": LLM_TOKENS[0], "outputTokens": LLM_TOKENS[1]}}}]}
