"""
Parameter Extractor Node Executor
Uses LLM to extract structured parameters from unstructured text

Plan 003 U14: ``build_prompt`` and ``parse_answer`` are pure; ``prepare`` returns an ``llm`` body (or a
``final`` for the regex strategy) and ``finish`` parses the answer. ``execute`` is a thin legacy wrapper.

Features:
- LLM-powered extraction with JSON schema validation
- Multiple extraction strategies: LLM, regex, combined
- Type coercion and validation
- Configurable fallback behavior
"""

from app.node_executors import (
    BaseNodeExecutor,
    NodeExecutionContext,
    NodeExecutionResult,
    PrepareResult,
    legacy_complete,
)
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
import re
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.llm_gateway import normalize_model_id

logger = logging.getLogger(__name__)
settings = get_settings()


class ParameterExtractorExecutor(BaseNodeExecutor):
    """Executor for Parameter Extractor node - LLM-powered parameter extraction"""

    node_type = "parameter_extractor"

    # LLM configuration
    LLM_TIMEOUT_SECONDS = 30.0
    DEFAULT_MODEL = "openai/gpt-4o-mini"

    def build_prompt(self, context: NodeExecutionContext) -> Dict[str, Any]:
        """Pure: the extraction prompt. Raises ``ValueError`` when the node cannot run."""
        node_data = context.node_data
        input_ref = node_data.get("inputText", "")
        parameters = node_data.get("parameters", [])
        input_text = self._resolve_input(input_ref, context.variables, context.inputs)
        if not input_text:
            raise ValueError(f"Input text not found: {input_ref}")
        if not parameters:
            raise ValueError("No parameters defined for extraction")
        return {
            "system": (
                "You are a precise data extraction assistant. Extract structured information from text "
                "and return valid JSON only."
            ),
            "prompt": self._build_extraction_prompt(input_text, parameters, node_data.get("instructions", "")),
            "input_text": input_text,
            "model": normalize_model_id(node_data.get("model", self.DEFAULT_MODEL)),
        }

    def parse_answer(
        self, context: NodeExecutionContext, text: Optional[str], *, parse_failure_is_error: bool = True
    ) -> NodeExecutionResult:
        """Pure: turn the model's answer (``None``: no answer) into the node result.

        Invalid JSON from an ``llm`` strategy node is a ``failed`` result with the parse error and the
        raw text kept, so the engine retry policy may re-ask. ``combined`` falls back to regex.
        """
        node_data = context.node_data
        parameters = node_data.get("parameters", [])
        strict_mode = node_data.get("strictMode", False)
        strategy = node_data.get("extractionStrategy", "llm")  # llm, regex, combined
        input_text = self._resolve_input(node_data.get("inputText", ""), context.variables, context.inputs) or ""

        extracted: Dict[str, Any] = {}
        method = "none"

        if strategy in ("llm", "combined"):
            parsed = self._try_parse_json(text, parameters) if text is not None else None
            if parsed is not None:
                extracted, method = parsed, "llm"
            elif strategy == "llm":
                if text is not None and parse_failure_is_error:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error="Could not parse JSON from the model answer",
                        output={"raw_text": text},
                    )
                extracted, method = self._regex_extract(input_text, parameters), "regex_fallback"

        if strategy == "regex" or (strategy == "combined" and not extracted):
            extracted, method = self._regex_extract(input_text, parameters), "regex"

        # Combined: fill missing LLM values from regex
        if strategy == "combined":
            regex_extracted = self._regex_extract(input_text, parameters)
            for param in parameters:
                name = param.get("name", "")
                if extracted.get(name) is None and regex_extracted.get(name) is not None:
                    extracted[name] = regex_extracted[name]
                    method = "combined"

        # Defaults for missing parameters
        for param in parameters:
            name = param.get("name", "")
            default = param.get("defaultValue")
            if extracted.get(name) is None:
                extracted[name] = self._coerce_type(default, param.get("type", "string")) if default is not None else None

        validation_errors = []
        if strict_mode:
            for param in parameters:
                if param.get("required", False) and extracted.get(param.get("name", "")) is None:
                    validation_errors.append(f"Required parameter '{param.get('name', '')}' not found")
        if validation_errors and strict_mode:
            return NodeExecutionResult(
                node_id=context.node_id, status="failed", error="; ".join(validation_errors)
            )

        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={
                "output": extracted,
                "parameters": extracted,
                "extraction_count": len([v for v in extracted.values() if v is not None]),
                "total_parameters": len(parameters),
                "extraction_method": method,
                "validation_warnings": validation_errors if not strict_mode else [],
            },
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        try:
            built = self.build_prompt(context)
        except ValueError as exc:
            return NodeExecutionResult(node_id=context.node_id, status="failed", error=str(exc))
        if context.node_data.get("extractionStrategy", "llm") == "regex":
            return self.parse_answer(context, None)
        return PrepareResult.llm(
            system=built["system"],
            prompt=built["prompt"],
            model=built["model"] if context.node_data.get("model") else None,
            tier="low",
            response_format="json",
        )

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        result = self.parse_answer(context, PrepareResult.answer_text(finish_input))
        tokens = PrepareResult.answer_tokens(finish_input)
        if tokens:
            result.tokens_used = tokens
        return result

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """Legacy in-process path over the same pure functions (removed by U30)."""
        start_time = time.time()
        try:
            built = self.build_prompt(context)
            text: Optional[str] = None
            tokens = 0
            if context.node_data.get("extractionStrategy", "llm") in ("llm", "combined"):
                try:
                    completion = await legacy_complete(
                        db,
                        context.tenant_id,
                        built["model"],
                        built["system"],
                        built["prompt"],
                        temperature=0.1,
                        max_tokens=2048,
                        timeout=self.LLM_TIMEOUT_SECONDS,
                    )
                    text, tokens = completion["text"] or "{}", completion["tokens"]
                except Exception as e:
                    logger.warning(f"LLM extraction failed for node {context.node_id}: {e}")
            result = self.parse_answer(context, text, parse_failure_is_error=False)
            result.tokens_used = tokens or None
            result.elapsed_ms = int((time.time() - start_time) * 1000)
            return result
        except Exception as e:
            logger.error(f"Parameter extraction failed for node {context.node_id}: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )

    def _resolve_input(self, input_ref: str, variables: dict, inputs: dict) -> Optional[str]:
        """Resolve input text from reference"""
        ref = input_ref.replace("{{", "").replace("}}", "").strip()

        if not ref:
            return None

        if ref in inputs:
            val = inputs[ref]
            if isinstance(val, dict):
                return val.get("output") or val.get("text") or str(val)
            return str(val)

        if "." in ref:
            parts = ref.split(".", 1)
            if parts[0] in inputs:
                node_output = inputs[parts[0]]
                if isinstance(node_output, dict):
                    return str(node_output.get(parts[1], ""))

        if ref in variables:
            return str(variables[ref])

        return None


    def _build_json_schema(self, parameters: List[Dict]) -> Dict[str, Any]:
        """Build a JSON schema from parameter definitions"""
        properties = {}
        required = []

        for param in parameters:
            name = param.get("name", "")
            param_type = param.get("type", "string")
            description = param.get("description", param.get("extractionHint", ""))
            is_required = param.get("required", False)

            # Map parameter types to JSON schema types
            type_mapping = {
                "string": {"type": "string"},
                "number": {"type": "number"},
                "boolean": {"type": "boolean"},
                "date": {"type": "string", "format": "date"},
                "array": {"type": "array", "items": {"type": "string"}},
                "object": {"type": "object"},
            }

            schema_type = type_mapping.get(param_type, {"type": "string"})
            properties[name] = {
                **schema_type,
                "description": description or f"Extract the {name} value",
            }

            if is_required:
                required.append(name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def _build_extraction_prompt(
        self,
        input_text: str,
        parameters: List[Dict],
        instructions: str
    ) -> str:
        """Build the extraction prompt for the LLM"""
        param_descriptions = []
        for param in parameters:
            name = param.get("name", "")
            param_type = param.get("type", "string")
            hint = param.get("extractionHint", "")
            required = "required" if param.get("required", False) else "optional"
            desc = f"- {name} ({param_type}, {required})"
            if hint:
                desc += f": {hint}"
            param_descriptions.append(desc)

        prompt = f"""Extract the following parameters from the text below.

Parameters to extract:
{chr(10).join(param_descriptions)}

{f'Additional instructions: {instructions}' if instructions else ''}

Text to analyze:
---
{input_text}
---

Return a JSON object with the extracted values. For any parameter that cannot be found in the text, use null.
Ensure the response is valid JSON only, with no additional text or explanation."""

        return prompt

    def _try_parse_json(self, response_text: str, parameters: List[Dict]) -> Optional[Dict[str, Any]]:
        """The coerced JSON object in the answer, or None when there is none."""
        response_text = (response_text or "").strip()
        try:
            data = json.loads(response_text)
            if isinstance(data, dict):
                return self._coerce_extracted_values(data, parameters)
        except json.JSONDecodeError:
            pass

        for pattern in (r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```', r'\{[\s\S]*\}'):
            match = re.search(pattern, response_text)
            if match:
                try:
                    data = json.loads(match.group(1) if match.lastindex else match.group(0))
                    if isinstance(data, dict):
                        return self._coerce_extracted_values(data, parameters)
                except (json.JSONDecodeError, IndexError):
                    continue
        return None

    def _parse_llm_response(self, response_text: str, parameters: List[Dict]) -> Dict[str, Any]:
        """Parse an answer into values ({} when there is no JSON object)."""
        parsed = self._try_parse_json(response_text, parameters)
        if parsed is None:
            logger.warning(f"Could not parse JSON from LLM response: {(response_text or '')[:200]}")
            return {}
        return parsed

    def _coerce_extracted_values(self, data: Dict, parameters: List[Dict]) -> Dict[str, Any]:
        """Coerce extracted values to their specified types"""
        result = {}

        param_types = {p.get("name", ""): p.get("type", "string") for p in parameters}

        for key, value in data.items():
            if key in param_types:
                result[key] = self._coerce_type(value, param_types[key])
            else:
                result[key] = value

        return result

    async def _extract_with_regex(
        self,
        text: str,
        parameters: List[Dict],
        instructions: str
    ) -> Dict[str, Any]:
        """Extract parameters from text using pattern matching (fallback)"""
        return self._regex_extract(text, parameters)

    def _regex_extract(self, text: str, parameters: List[Dict]) -> Dict[str, Any]:
        extracted = {}

        for param in parameters:
            name = param.get("name", "")
            param_type = param.get("type", "string")
            hint = param.get("extractionHint", "")

            # Try to extract based on type and hints
            value = self._extract_single_parameter(text, name, param_type, hint)
            extracted[name] = value

        return extracted

    def _extract_single_parameter(
        self,
        text: str,
        name: str,
        param_type: str,
        hint: str
    ) -> Optional[Any]:
        """Extract a single parameter from text"""

        # Try common patterns based on parameter name
        patterns = self._get_extraction_patterns(name, param_type, hint)

        for pattern in patterns:
            try:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    raw_value = match.group(1) if match.lastindex else match.group(0)
                    return self._coerce_type(raw_value.strip(), param_type)
            except re.error:
                continue

        return None

    def _get_extraction_patterns(self, name: str, param_type: str, hint: str) -> List[str]:
        """Generate extraction patterns based on parameter info"""
        patterns = []

        # Custom hint pattern takes priority
        if hint:
            patterns.append(hint)

        # Common label patterns
        name_variations = [
            name,
            name.replace("_", " "),
            name.replace("_", "-"),
        ]

        for var in name_variations:
            # "name: value" pattern
            patterns.append(rf'{var}\s*[:=]\s*(.+?)(?:\n|$)')
            # "name is value" pattern
            patterns.append(rf'{var}\s+is\s+(.+?)(?:\n|$)')

        # Type-specific patterns
        if param_type == "number":
            patterns.append(r'\$?([\d,]+(?:\.\d+)?)')
            patterns.append(r'([\d,]+(?:\.\d+)?)\s*(?:USD|EUR|GBP|%)?')

        elif param_type == "date":
            patterns.append(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})')
            patterns.append(r'(\d{4}[/-]\d{1,2}[/-]\d{1,2})')
            patterns.append(r'(\w+\s+\d{1,2},?\s+\d{4})')

        elif param_type == "boolean":
            patterns.append(r'\b(yes|no|true|false)\b')

        return patterns

    def _coerce_type(self, value: Any, param_type: str) -> Any:
        """Convert extracted value to the specified type"""
        if value is None:
            return None

        # Already correct type
        if param_type == "number" and isinstance(value, (int, float)):
            return value
        if param_type == "boolean" and isinstance(value, bool):
            return value
        if param_type == "array" and isinstance(value, list):
            return value
        if param_type == "object" and isinstance(value, dict):
            return value

        value_str = str(value).strip()

        if param_type == "string":
            return value_str

        elif param_type == "number":
            # Remove currency symbols and commas
            cleaned = re.sub(r'[$,\s]', '', value_str)
            try:
                if "." in cleaned:
                    return float(cleaned)
                return int(cleaned)
            except ValueError:
                return None

        elif param_type == "boolean":
            if isinstance(value, bool):
                return value
            return value_str.lower() in ("true", "yes", "1", "on")

        elif param_type == "date":
            # Return as string for now
            return value_str

        elif param_type == "array":
            if isinstance(value, list):
                return value
            try:
                return json.loads(value_str)
            except json.JSONDecodeError:
                return [v.strip() for v in value_str.split(",")]

        elif param_type == "object":
            if isinstance(value, dict):
                return value
            try:
                return json.loads(value_str)
            except json.JSONDecodeError:
                return {"value": value_str}

        return value_str
