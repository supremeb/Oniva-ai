"""
Parameter Extractor Node Executor
Uses LLM to extract structured parameters from unstructured text

Features:
- LLM-powered extraction with JSON schema validation
- Multiple extraction strategies: LLM, regex, combined
- Type coercion and validation
- Configurable fallback behavior
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
import re
import logging
import asyncio
from typing import Any, Dict, List, Optional
from uuid import UUID

import litellm
from app.config import get_settings
from app.services.llm_gateway import (
    resolve_key_with_fallback, normalize_model_id, get_provider_from_model,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class ParameterExtractorExecutor(BaseNodeExecutor):
    """Executor for Parameter Extractor node - LLM-powered parameter extraction"""

    node_type = "parameter_extractor"

    # LLM configuration
    LLM_TIMEOUT_SECONDS = 30.0
    DEFAULT_MODEL = "openai/gpt-4o-mini"

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            input_ref = node_data.get("inputText", "")
            parameters = node_data.get("parameters", [])
            model = node_data.get("model", self.DEFAULT_MODEL)
            instructions = node_data.get("instructions", "")
            strict_mode = node_data.get("strictMode", False)
            extraction_strategy = node_data.get("extractionStrategy", "llm")  # llm, regex, combined

            # Resolve input text
            input_text = self._resolve_input(input_ref, context.variables, context.inputs)

            if not input_text:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Input text not found: {input_ref}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            if not parameters:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="No parameters defined for extraction",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Extract parameters based on strategy
            extracted = {}
            extraction_method = "none"

            if extraction_strategy in ["llm", "combined"]:
                # Try LLM-based extraction first
                try:
                    extracted = await self._extract_with_llm(
                        input_text,
                        parameters,
                        instructions,
                        model,
                        db,
                        context.user_id,
                        tenant_id=context.tenant_id,
                    )
                    extraction_method = "llm"
                    logger.info(f"LLM extraction successful for node {context.node_id}")
                except Exception as e:
                    logger.warning(f"LLM extraction failed for node {context.node_id}: {e}")
                    if extraction_strategy == "llm":
                        # LLM-only mode, fall back to regex
                        extracted = await self._extract_with_regex(
                            input_text, parameters, instructions
                        )
                        extraction_method = "regex_fallback"

            if extraction_strategy == "regex" or (extraction_strategy == "combined" and not extracted):
                # Use regex-based extraction
                extracted = await self._extract_with_regex(
                    input_text, parameters, instructions
                )
                extraction_method = "regex"

            # If combined strategy, merge LLM results with regex for missing values
            if extraction_strategy == "combined":
                regex_extracted = await self._extract_with_regex(
                    input_text, parameters, instructions
                )
                for param in parameters:
                    name = param.get("name", "")
                    if name not in extracted or extracted.get(name) is None:
                        if name in regex_extracted and regex_extracted[name] is not None:
                            extracted[name] = regex_extracted[name]
                            extraction_method = "combined"

            # Apply default values for missing parameters
            for param in parameters:
                name = param.get("name", "")
                default = param.get("defaultValue")
                param_type = param.get("type", "string")

                if name not in extracted or extracted.get(name) is None:
                    if default is not None:
                        extracted[name] = self._coerce_type(default, param_type)
                    else:
                        extracted[name] = None

            # Validate required parameters
            validation_errors = []
            if strict_mode:
                for param in parameters:
                    if param.get("required", False):
                        param_name = param.get("name", "")
                        if param_name not in extracted or extracted[param_name] is None:
                            validation_errors.append(f"Required parameter '{param_name}' not found")

            if validation_errors and strict_mode:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="; ".join(validation_errors),
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": extracted,
                    "parameters": extracted,
                    "extraction_count": len([v for v in extracted.values() if v is not None]),
                    "total_parameters": len(parameters),
                    "extraction_method": extraction_method,
                    "validation_warnings": validation_errors if not strict_mode else [],
                },
                elapsed_ms=elapsed_ms
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Parameter extraction failed for node {context.node_id}: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
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

    async def _extract_with_llm(
        self,
        text: str,
        parameters: List[Dict],
        instructions: str,
        model: str,
        db: AsyncSession,
        user_id: str,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract parameters using LLM via LiteLLM"""

        prompt = self._build_extraction_prompt(text, parameters, instructions)
        model_id = normalize_model_id(model)

        # Resolve API key via unified gateway (with OpenRouter fallback)
        tenant_uuid = UUID(tenant_id) if tenant_id else None
        api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)

        if not api_key:
            provider_type = get_provider_from_model(model_id)
            raise ValueError(f"API key not configured for provider '{provider_type}'")

        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": "You are a precise data extraction assistant. Extract structured information from text and return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2048,
                api_key=api_key,
            ),
            timeout=self.LLM_TIMEOUT_SECONDS,
        )

        output_text = response.choices[0].message.content or "{}"

        # Parse JSON response
        extracted = self._parse_llm_response(output_text, parameters)
        return extracted

    def _parse_llm_response(self, response_text: str, parameters: List[Dict]) -> Dict[str, Any]:
        """Parse LLM response and extract JSON values"""
        # Try to find JSON in the response
        response_text = response_text.strip()

        # Try direct JSON parse first
        try:
            data = json.loads(response_text)
            return self._coerce_extracted_values(data, parameters)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        json_patterns = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
            r'\{[\s\S]*\}',
        ]

        for pattern in json_patterns:
            match = re.search(pattern, response_text)
            if match:
                try:
                    json_str = match.group(1) if match.lastindex else match.group(0)
                    data = json.loads(json_str)
                    return self._coerce_extracted_values(data, parameters)
                except (json.JSONDecodeError, IndexError):
                    continue

        logger.warning(f"Could not parse JSON from LLM response: {response_text[:200]}")
        return {}

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
