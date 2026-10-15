"""Wire models for the tool gateway and JSON Schema generation from skill signatures (KTD7)."""
import inspect
import re
import typing
from typing import Any, Callable, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, create_model


class ManifestTool(BaseModel):
    """One manifest entry, exactly the shape engine/src/tools/gateway.ts parses."""

    name: str
    description: str
    parameters: Dict[str, Any]
    sideEffect: Literal["none", "write"]
    idempotent: bool
    timeoutMs: PositiveInt
    requiresApproval: bool


class ManifestResponse(BaseModel):
    tools: List[ManifestTool]


_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters):\s*$")
_ARG_LINE = re.compile(r"^\s{0,12}(\w+)\s*(?:\([^)]*\))?:\s*(.+)$")
_SECTION = re.compile(r"^\s*(Returns?|Raises?|Yields?|Example|Examples|Usage|Note|Notes):")


def parse_arg_descriptions(doc: str | None) -> Dict[str, str]:
    """Descriptions from a Google-style `Args:` block; continuation lines are joined."""
    if not doc:
        return {}
    out: Dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in inspect.cleandoc(doc).splitlines():
        if _ARGS_HEADER.match(line):
            in_args, current = True, None
            continue
        if not in_args:
            continue
        if _SECTION.match(line) or (line and not line[0].isspace() and not _ARG_LINE.match(line)):
            break
        m = _ARG_LINE.match(line)
        if m and len(line) - len(line.lstrip()) <= 4:
            current = m.group(1)
            out[current] = m.group(2).strip()
        elif current and line.strip():
            out[current] += " " + line.strip()
    return out


def _resolve_refs(node: Any, defs: Dict[str, Any], seen: tuple = ()) -> Any:
    """Inline `$ref`s (the engine's z.fromJSONSchema cannot follow `$defs` reliably)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.rsplit("/", 1)[1]
            if key in seen or key not in defs:
                return {}  # recursive model: accept any value rather than loop
            merged = {**_resolve_refs(defs[key], defs, seen + (key,)),
                      **{k: v for k, v in node.items() if k != "$ref"}}
            return merged
        return {k: _resolve_refs(v, defs, seen) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_resolve_refs(v, defs, seen) for v in node]
    return node


def _strip_titles(node: Any, in_properties: bool = False) -> Any:
    """Drop pydantic's auto `title`s (noise for the model), keeping a property literally named title."""
    if isinstance(node, dict):
        return {
            k: _strip_titles(v, in_properties=(k == "properties"))
            for k, v in node.items()
            if in_properties or k != "title"
        }
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def build_parameters_schema(handler: Callable, tool_name: str = "tool") -> Dict[str, Any]:
    """JSON Schema (type=object, additionalProperties=false) for a skill's arguments, minus `ctx`."""
    fn = inspect.unwrap(handler)
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    docs = parse_arg_descriptions(inspect.getdoc(fn))

    fields: Dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname == "ctx" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        ann = hints.get(pname, param.annotation)
        if ann is inspect.Parameter.empty:
            ann = Any
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[pname] = (ann, Field(default, description=docs.get(pname)))

    model = create_model(
        f"{tool_name}_args",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    schema = _strip_titles(_resolve_refs(schema, defs))
    schema["type"] = "object"
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    return schema
