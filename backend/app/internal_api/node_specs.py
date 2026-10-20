"""Per-node-type gateway facts (plan 2026-10-17-003, U13, KTD4).

``replay_safe``: re-running the node after an interrupted claim cannot repeat an outside effect, so a
stale ``in_flight`` claim may be taken over instead of answered ``interrupted`` (the same distinction
``ToolSpec.idempotent`` makes for tools). ``makes_external_calls`` mirrors the executor flag and is
the fallback when no executor is registered. ``fanout`` marks the types whose ``prepare`` creates item
rows and returns a ``fanout`` body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

DEFAULT_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class NodeSpec:
    replay_safe: bool = False
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    makes_external_calls: bool = False
    fanout: Optional[str] = None  # "iteration" | "subworkflow" | "batch"


_PURE = NodeSpec(replay_safe=True)

NODE_SPECS: Dict[str, NodeSpec] = {
    "start": _PURE,
    "output": _PURE,
    "template": _PURE,
    "ifelse": _PURE,
    "conditional": _PURE,
    "aggregator": _PURE,
    "merge": _PURE,
    "variable_assigner": _PURE,
    "list_operator": _PURE,
    # Their prepare only computes a deadline (the approval row is keyed, a replay keeps it) and their finish
    # only turns the wake into an output, so a re-run after a crash repeats nothing.
    "wait": _PURE,
    "human_in_loop": _PURE,
    "iteration": NodeSpec(replay_safe=True, fanout="iteration"),
    "subworkflow": NodeSpec(replay_safe=True, fanout="subworkflow"),
    "batch": NodeSpec(replay_safe=True, fanout="batch"),  # U18: the single node of a /execute-batch run
    "classifier": NodeSpec(timeout_ms=120_000, makes_external_calls=True),
    "llm": NodeSpec(timeout_ms=120_000, makes_external_calls=True),
    "guardrail": NodeSpec(timeout_ms=120_000, makes_external_calls=True),
    "parameter_extractor": NodeSpec(timeout_ms=120_000, makes_external_calls=True),
    "agent": NodeSpec(timeout_ms=300_000, makes_external_calls=True),
    "http": NodeSpec(makes_external_calls=True),
    "connector": NodeSpec(makes_external_calls=True),
    "notification": NodeSpec(makes_external_calls=True),
}


def node_spec_for(node_type: str) -> NodeSpec:
    """The spec for a node type; unknown types get the conservative default (not replay-safe)."""
    return NODE_SPECS.get((node_type or "").lower(), NodeSpec())
