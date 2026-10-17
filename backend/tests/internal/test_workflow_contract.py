"""Workflow node contract (plan 2026-10-17-003, U33, KTD4/KTD5/KTD16): the backend side of
the engine's ``contracts/workflow-nodes`` schemas.

The committed JSON schemas and fixtures are normative. This suite proves the backend's pydantic
models agree with them:

* every valid fixture validates and every invalid one is rejected with the manifest's stable code;
* every property and enum value named in a committed schema exists in the matching pydantic model;
* ``limits.json.maxNodes`` equals ``settings.MAX_WORKFLOW_NODES``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest
from pydantic import TypeAdapter

from app.config import Settings
from app.internal_api import workflow_contract as wc

CONTRACTS = Path(__file__).resolve().parents[3] / "engine/contracts/workflow-nodes"
FIXTURES = CONTRACTS / "fixtures"
pytestmark = pytest.mark.skipif(not CONTRACTS.exists(), reason="engine not checked out")

VALIDATORS = {
    "prepare.response": wc.validate_prepare_response,
    "finish.input": wc.validate_finish_input,
    "finalize.input": wc.validate_finalize_input,
}

MODELS: Dict[str, Any] = {
    "prepare.final.schema.json": wc.PrepareFinal,
    "prepare.llm.schema.json": wc.PrepareLlm,
    "prepare.wait.schema.json": wc.PrepareWait,
    "prepare.fanout.schema.json": wc.PrepareFanout,
    "prepare.response.schema.json": wc.PrepareResponse,
    "finish.input.schema.json": wc.FinishInput,
    "finalize.input.schema.json": wc.FinalizeInput,
}

_MANIFEST: List[Dict[str, Any]] = json.loads((FIXTURES / "manifest.json").read_text())["cases"]


@pytest.mark.parametrize("case", _MANIFEST, ids=lambda c: c["file"])
def test_fixtures_match_the_manifest(case: Dict[str, Any]) -> None:
    text = (FIXTURES / case["file"]).read_text()
    validate = VALIDATORS[case["schema"]]
    if case["valid"]:
        validate(text)
        return
    with pytest.raises(wc.ContractError) as exc:
        validate(text)
    assert exc.value.code == case["code"]


def test_limits_match_backend() -> None:
    limits = json.loads((CONTRACTS / "limits.json").read_text())
    assert limits["maxNodes"] == Settings(_env_file=None).MAX_WORKFLOW_NODES
    assert limits["maxParallel"] == wc.MAX_PARALLEL
    assert limits["maxFanoutItems"] == wc.MAX_FANOUT_ITEMS


def test_contract_version_is_one() -> None:
    assert wc.CONTRACT_VERSION == 1


def _load_schema(name: str, path: Tuple[str, ...] = ()) -> Any:
    if name in path:
        raise AssertionError(f"cyclic $ref: {' -> '.join((*path, name))}")
    raw = json.loads((CONTRACTS / name).read_text())
    return _resolve_refs(raw, (*path, name))


def _resolve_refs(node: Any, path: Tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_resolve_refs(item, path) for item in node]
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            file = ref.split("#", 1)[0]
            if file:
                return _load_schema(file, path)
        return {k: _resolve_refs(v, path) for k, v in node.items()}
    return node


def _surface(node: Any, props: Set[str] | None = None, values: Set[str] | None = None) -> Tuple[Set[str], Set[str]]:
    props = set() if props is None else props
    values = set() if values is None else values
    if isinstance(node, list):
        for item in node:
            _surface(item, props, values)
        return props, values
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for name, sub in value.items():
                    props.add(name)
                    _surface(sub, props, values)
                continue
            if key == "enum" and isinstance(value, list):
                values.update(json.dumps(v) for v in value)
                continue
            if key == "const":
                values.add(json.dumps(value))
                continue
            _surface(value, props, values)
    return props, values


@pytest.mark.parametrize("file", list(MODELS))
def test_schema_property_parity(file: str) -> None:
    committed_props, committed_values = _surface(_load_schema(file))
    generated_props, generated_values = _surface(TypeAdapter(MODELS[file]).json_schema())
    assert generated_props == committed_props
    assert generated_values == committed_values
