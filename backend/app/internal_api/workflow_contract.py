"""Workflow node contract (plan 2026-10-17-003, U33, KTD4/KTD5/KTD16).

Typed pydantic models for the four ``prepare`` shapes, the ``finish`` input and the
``finalize`` input, mirroring the engine's committed JSON schemas under
``engine/contracts/workflow-nodes/``. Those schemas are normative; the models here are the
backend's proof that it agrees with them (``tests/internal/test_workflow_contract.py``).

The validators accept a body over the wire as text or as a parsed value. A body larger than
``MAX_BODY_BYTES`` is refused (KTD17: a body can never become an unbounded checkpoint) and a
malformed one is refused with a stable :class:`ContractError` code, the same codes the engine's
``workflow/contract.ts`` uses.
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

#: The version stamped on every node gateway body.
CONTRACT_VERSION = 1
#: Longest contract body accepted, in bytes.
MAX_BODY_BYTES = 64 * 1024
#: Bound mirrored from ``contracts/workflow-nodes/limits.json``.
MAX_PARALLEL = 50
#: Bound mirrored from ``contracts/workflow-nodes/limits.json``.
MAX_FANOUT_ITEMS = 1000
#: Bound mirrored from ``contracts/workflow-nodes/limits.json``.
MAX_SUBWORKFLOW_DEPTH = 3

#: A node's own result status, matching ``prepare.final``.
NodeStatus = Literal["success", "failed", "blocked", "skipped"]
#: The run's terminal status, matching ``finalize.input``.
RunStatus = Literal["completed", "failed", "cancelled"]
ResponseFormat = Literal["text", "json"]
#: Why a ``wait`` node woke.
WakeReason = Literal["signal", "deadline"]
#: The prepare kinds a ``finish`` call reports on.
FinishKind = Literal["llm", "wait", "fanout"]


def fanout_item_id(run: Any, node_id: str, index: int, run_attempt: int = 0) -> uuid.UUID:
    """The execution row id of fan-out item ``index`` of ``node_id`` in ``run``.

    ``uuid5(run, "{nodeId}:{index}")`` for a first attempt; a run retry (``run_attempt`` > 0) gets its own
    rows, ``uuid5(run, "{nodeId}:r{n}:{index}")``, so it never reuses the terminal rows of an earlier
    attempt. The engine derives the same id (``itemRunId`` in ``workflow/fanout-lane-task.ts``).
    """
    base = run if isinstance(run, uuid.UUID) else uuid.UUID(str(run))
    name = f"{node_id}:{index}" if run_attempt <= 0 else f"{node_id}:r{run_attempt}:{index}"
    return uuid.uuid5(base, name)


class ContractError(ValueError):
    """A contract body was rejected; ``code`` is stable across the engine and the backend."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Body(BaseModel):
    """Base for every contract body: exactly the schema's fields, nothing else."""

    model_config = ConfigDict(extra="forbid")


class PrepareFinal(_Body):
    """``prepare`` finished in the backend; the node is done."""

    contractVersion: Literal[1]
    kind: Literal["final"]
    status: NodeStatus
    output: Any
    tokensUsed: int = Field(ge=0)
    error: Optional[Any] = None
    classification: Optional[str] = None
    selectedHandles: Optional[List[str]] = None


class PrepareLlm(_Body):
    """The backend asks the engine to run a generation (KTD6)."""

    contractVersion: Literal[1]
    kind: Literal["llm"]
    system: str
    prompt: str
    replaySafe: bool
    tier: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[List[str]] = None
    maxRounds: Optional[int] = Field(default=None, gt=0)
    responseFormat: Optional[ResponseFormat] = None


class PrepareWait(_Body):
    """The node parks; duration waits are relative to the engine clock (KTD7)."""

    contractVersion: Literal[1]
    kind: Literal["wait"]
    durationMs: Optional[int] = Field(default=None, gt=0)
    untilEpochMs: Optional[int] = Field(default=None, gt=0)
    signal: Optional[str] = None
    slaUntilEpochMs: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _exactly_one_duration(self) -> "PrepareWait":
        if (self.durationMs is None) == (self.untilEpochMs is None):
            raise ValueError("exactly one of durationMs or untilEpochMs is required")
        return self


class FanoutTargetSubgraph(_Body):
    subgraph: str


class FanoutTargetWorkflow(_Body):
    workflowId: str


FanoutTarget = Union[FanoutTargetSubgraph, FanoutTargetWorkflow]


class PrepareFanout(_Body):
    """The node fans out; counts and a backend reference only, never item payloads (KTD17)."""

    contractVersion: Literal[1]
    kind: Literal["fanout"]
    count: int = Field(ge=0, le=MAX_FANOUT_ITEMS)
    parallel: int = Field(ge=1, le=MAX_PARALLEL)
    failFast: bool
    target: FanoutTarget
    itemsRef: str


#: The four prepare shapes as one discriminated union on ``kind``.
PrepareResponse = Annotated[
    Union[PrepareFinal, PrepareLlm, PrepareWait, PrepareFanout], Field(discriminator="kind")
]


class FanoutSummary(_Body):
    """The counts-only summary the run task delivers when a fan-out settles (KTD17)."""

    count: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    firstError: Optional[Any] = None


class FinishInput(_Body):
    """What the engine sends to the backend ``finish`` route. The reply is a ``final``."""

    contractVersion: Literal[1]
    kind: FinishKind
    answer: Optional[Any] = None
    signalPayload: Optional[Any] = None
    wakeReason: Optional[WakeReason] = None
    fanout: Optional[FanoutSummary] = None


class FinalizeInput(_Body):
    """What the ``workflow_run`` task sends to the backend ``finalize`` route."""

    contractVersion: Literal[1]
    status: RunStatus
    nodeSummaries: Dict[str, NodeStatus]
    attempts: int = Field(ge=1)
    error: Optional[Any] = None


_PREPARE_RESPONSE = TypeAdapter(PrepareResponse)
_FINISH_INPUT = TypeAdapter(FinishInput)
_FINALIZE_INPUT = TypeAdapter(FinalizeInput)


def _size_of(value: Any) -> int:
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _validate(adapter: TypeAdapter, value: Any) -> Any:
    if _size_of(value) > MAX_BODY_BYTES:
        raise ContractError("body_too_large", f"contract body exceeds {MAX_BODY_BYTES} bytes")
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ContractError("invalid_body", "contract body is not valid JSON") from exc
    try:
        return adapter.validate_python(value)
    except ValidationError as exc:
        raise ContractError("invalid_body", str(exc)) from exc


def validate_prepare_response(value: Any) -> Any:
    """Validate a ``prepare`` response body (JSON text or a parsed value)."""
    return _validate(_PREPARE_RESPONSE, value)


def validate_finish_input(value: Any) -> Any:
    """Validate a ``finish`` input body (JSON text or a parsed value)."""
    return _validate(_FINISH_INPUT, value)


def validate_finalize_input(value: Any) -> Any:
    """Validate a ``finalize`` input body (JSON text or a parsed value)."""
    return _validate(_FINALIZE_INPUT, value)
