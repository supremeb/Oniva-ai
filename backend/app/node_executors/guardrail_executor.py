"""
Guardrail Node Executor
Vellum-inspired runtime evaluation for output validation and safety

Guardrails evaluate outputs against configurable rules:
- LLM-based evaluation (semantic checks, safety, quality)
- Regex pattern matching
- JSON schema validation
- Threshold-based scoring (no arbitrary code execution)

Usage:
{
    "type": "guardrail",
    "data": {
        "name": "Content Safety Check",
        "evaluationType": "llm",  // llm, regex, schema, threshold, composite
        "rules": [
            {
                "type": "llm",
                "name": "toxicity_check",
                "prompt": "Is this content toxic or harmful? Answer YES or NO.",
                "expectedValue": "NO",
                "weight": 1.0
            },
            {
                "type": "regex",
                "name": "no_pii",
                "pattern": "\\b\\d{3}-\\d{2}-\\d{4}\\b",
                "shouldMatch": false,
                "weight": 0.5
            }
        ],
        "failureAction": "reject",  // reject, warn, passthrough
        "failureThreshold": 0.7,  // Score threshold for pass/fail
        "model": "gpt-4o-mini"  // For LLM evaluations
    }
}
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

import litellm
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import jsonschema

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from app.services.llm_gateway import (
    resolve_key_with_fallback, normalize_model_id,
)

logger = logging.getLogger(__name__)


class RuleResult(BaseModel):
    """Result of a single guardrail rule evaluation"""
    rule_name: str
    rule_type: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)  # 0 = fail, 1 = pass
    weight: float = 1.0
    details: Optional[str] = None
    raw_output: Optional[Any] = None
    duration_ms: int = 0


class GuardrailOutput(BaseModel):
    """Output schema for Guardrail node"""
    passed: bool
    score: float = Field(ge=0.0, le=1.0)  # Weighted average score
    failure_action: str
    rule_results: List[RuleResult] = Field(default_factory=list)
    total_rules: int
    passed_rules: int
    failed_rules: int
    input_data: Optional[Any] = None  # The data that was evaluated
    message: Optional[str] = None  # Human-readable summary


class GuardrailExecutor(BaseNodeExecutor):
    """
    Executor for Guardrail node - runtime evaluation and safety checks

    Supports multiple evaluation types:
    - llm: Use LLM to evaluate content semantically
    - regex: Pattern matching (should/shouldn't match)
    - schema: JSON schema validation
    - threshold: Numeric threshold comparison
    - length: String/list length checks
    - contains: Substring/item containment checks
    """

    node_type = "guardrail"

    async def _evaluate_llm_rule(
        self,
        rule: Dict[str, Any],
        content: str,
        model_id: str,
        api_key: str,
    ) -> RuleResult:
        """Evaluate content using LLM via LiteLLM"""
        start_time = time.time()
        rule_name = rule.get("name", "llm_check")

        try:
            eval_prompt = rule.get("prompt", "Is this content appropriate?")
            expected_value = rule.get("expectedValue", "YES")

            system_prompt = (
                "You are an evaluator. Answer concisely based on the evaluation criteria. "
                "Respond with exactly the expected format specified."
            )

            full_prompt = f"{eval_prompt}\n\nContent to evaluate:\n{content}"

            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_prompt},
                ],
                temperature=0.1,
                max_tokens=100,
                api_key=api_key,
            )

            response_text = response.choices[0].message.content or ""
            response_clean = response_text.strip().upper()

            expected_clean = expected_value.strip().upper()
            passed = expected_clean in response_clean or response_clean == expected_clean
            score = 1.0 if passed else 0.0

            return RuleResult(
                rule_name=rule_name,
                rule_type="llm",
                passed=passed,
                score=score,
                weight=rule.get("weight", 1.0),
                details=f"Expected '{expected_value}', got '{response_text}'",
                raw_output=response_text,
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            logger.error(f"LLM rule evaluation failed: {e}")
            return RuleResult(
                rule_name=rule_name,
                rule_type="llm",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Evaluation error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def _evaluate_regex_rule(
        self,
        rule: Dict[str, Any],
        content: str
    ) -> RuleResult:
        """Evaluate content using regex pattern"""
        start_time = time.time()
        rule_name = rule.get("name", "regex_check")

        try:
            pattern = rule.get("pattern", "")
            should_match = rule.get("shouldMatch", True)
            flags = re.IGNORECASE if rule.get("caseInsensitive", False) else 0

            matches = re.findall(pattern, content, flags)
            has_match = len(matches) > 0

            # Pass if match status matches expectation
            passed = has_match == should_match
            score = 1.0 if passed else 0.0

            details = f"Pattern '{pattern}' "
            if has_match:
                details += f"found {len(matches)} match(es)"
            else:
                details += "not found"

            return RuleResult(
                rule_name=rule_name,
                rule_type="regex",
                passed=passed,
                score=score,
                weight=rule.get("weight", 1.0),
                details=details,
                raw_output=matches if matches else None,
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except re.error as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="regex",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Invalid regex pattern: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def _evaluate_schema_rule(
        self,
        rule: Dict[str, Any],
        content: Any
    ) -> RuleResult:
        """Validate content against JSON schema"""
        start_time = time.time()
        rule_name = rule.get("name", "schema_check")

        try:
            schema = rule.get("schema", {})

            # Parse content if it's a string
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    return RuleResult(
                        rule_name=rule_name,
                        rule_type="schema",
                        passed=False,
                        score=0.0,
                        weight=rule.get("weight", 1.0),
                        details="Content is not valid JSON",
                        duration_ms=int((time.time() - start_time) * 1000)
                    )

            # Validate against schema
            jsonschema.validate(content, schema)

            return RuleResult(
                rule_name=rule_name,
                rule_type="schema",
                passed=True,
                score=1.0,
                weight=rule.get("weight", 1.0),
                details="Schema validation passed",
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except jsonschema.ValidationError as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="schema",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Schema validation failed: {e.message}",
                duration_ms=int((time.time() - start_time) * 1000)
            )
        except jsonschema.SchemaError as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="schema",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Invalid schema: {e.message}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def _evaluate_threshold_rule(
        self,
        rule: Dict[str, Any],
        content: Any
    ) -> RuleResult:
        """Check if numeric value meets threshold"""
        start_time = time.time()
        rule_name = rule.get("name", "threshold_check")

        try:
            # Extract value (try to get numeric from content)
            field = rule.get("field")
            if field and isinstance(content, dict):
                value = content.get(field, content)
            else:
                value = content

            # Convert to numeric
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    return RuleResult(
                        rule_name=rule_name,
                        rule_type="threshold",
                        passed=False,
                        score=0.0,
                        weight=rule.get("weight", 1.0),
                        details=f"Cannot convert '{value}' to number",
                        duration_ms=int((time.time() - start_time) * 1000)
                    )

            # Check threshold using safe comparison
            operator = rule.get("operator", ">=")
            threshold = rule.get("threshold", 0)

            # Safe operator implementations (no eval)
            if operator == ">=":
                passed = value >= threshold
            elif operator == ">":
                passed = value > threshold
            elif operator == "<=":
                passed = value <= threshold
            elif operator == "<":
                passed = value < threshold
            elif operator == "==":
                passed = value == threshold
            elif operator == "!=":
                passed = value != threshold
            else:
                passed = value >= threshold  # Default to >=

            score = 1.0 if passed else 0.0

            return RuleResult(
                rule_name=rule_name,
                rule_type="threshold",
                passed=passed,
                score=score,
                weight=rule.get("weight", 1.0),
                details=f"Value {value} {operator} {threshold} = {passed}",
                raw_output=value,
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="threshold",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Threshold evaluation error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def _evaluate_length_rule(
        self,
        rule: Dict[str, Any],
        content: Any
    ) -> RuleResult:
        """Check length of content (string, list, dict)"""
        start_time = time.time()
        rule_name = rule.get("name", "length_check")

        try:
            # Get length
            if hasattr(content, '__len__'):
                length = len(content)
            else:
                length = len(str(content))

            # Check against min/max
            min_length = rule.get("minLength", 0)
            max_length = rule.get("maxLength", float('inf'))

            passed = min_length <= length <= max_length
            score = 1.0 if passed else 0.0

            return RuleResult(
                rule_name=rule_name,
                rule_type="length",
                passed=passed,
                score=score,
                weight=rule.get("weight", 1.0),
                details=f"Length {length} (min: {min_length}, max: {max_length})",
                raw_output=length,
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="length",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Length check error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def _evaluate_contains_rule(
        self,
        rule: Dict[str, Any],
        content: Any
    ) -> RuleResult:
        """Check if content contains specified value(s)"""
        start_time = time.time()
        rule_name = rule.get("name", "contains_check")

        try:
            values = rule.get("values", [])
            should_contain = rule.get("shouldContain", True)
            match_all = rule.get("matchAll", False)

            content_str = str(content) if not isinstance(content, str) else content

            # Check each value
            matches = []
            for value in values:
                if value in content_str:
                    matches.append(value)

            if match_all:
                found_all = len(matches) == len(values)
                passed = found_all == should_contain
            else:
                found_any = len(matches) > 0
                passed = found_any == should_contain

            score = 1.0 if passed else 0.0

            return RuleResult(
                rule_name=rule_name,
                rule_type="contains",
                passed=passed,
                score=score,
                weight=rule.get("weight", 1.0),
                details=f"Found {len(matches)} of {len(values)} values",
                raw_output=matches,
                duration_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            return RuleResult(
                rule_name=rule_name,
                rule_type="contains",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details=f"Contains check error: {str(e)}",
                duration_ms=int((time.time() - start_time) * 1000)
            )

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """Execute guardrail evaluation"""
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get configuration
            rules = node_data.get("rules", [])
            failure_action = node_data.get("failureAction", "reject")
            failure_threshold = node_data.get("failureThreshold", 0.7)
            model_id = node_data.get("model", "gpt-4o-mini")

            # Get content to evaluate from inputs
            content = None
            for input_key, input_value in context.inputs.items():
                if isinstance(input_value, dict):
                    # Try common output fields
                    content = (
                        input_value.get("response") or
                        input_value.get("output") or
                        input_value.get("result") or
                        input_value.get("text") or
                        input_value
                    )
                else:
                    content = input_value
                break

            if content is None:
                content = context.variables.get("_input", "")

            # Convert to string for text-based rules
            content_str = str(content) if not isinstance(content, str) else content

            if not rules:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output=GuardrailOutput(
                        passed=True,
                        score=1.0,
                        failure_action=failure_action,
                        rule_results=[],
                        total_rules=0,
                        passed_rules=0,
                        failed_rules=0,
                        input_data=content,
                        message="No rules configured - passed by default"
                    ).model_dump(),
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Get LLM configuration if needed
            model_id = normalize_model_id(model_id)
            tenant_uuid = UUID(context.tenant_id) if context.tenant_id else None
            api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)

            # Evaluate all rules
            rule_results: List[RuleResult] = []

            for rule in rules:
                rule_type = rule.get("type", "regex")

                if rule_type == "llm":
                    if not api_key:
                        rule_results.append(RuleResult(
                            rule_name=rule.get("name", "llm_check"),
                            rule_type="llm",
                            passed=False,
                            score=0.0,
                            weight=rule.get("weight", 1.0),
                            details="No API key available for LLM evaluation"
                        ))
                    else:
                        result = await self._evaluate_llm_rule(
                            rule, content_str, model_id, api_key
                        )
                        rule_results.append(result)

                elif rule_type == "regex":
                    result = await self._evaluate_regex_rule(rule, content_str)
                    rule_results.append(result)

                elif rule_type == "schema":
                    result = await self._evaluate_schema_rule(rule, content)
                    rule_results.append(result)

                elif rule_type == "threshold":
                    result = await self._evaluate_threshold_rule(rule, content)
                    rule_results.append(result)

                elif rule_type == "length":
                    result = await self._evaluate_length_rule(rule, content)
                    rule_results.append(result)

                elif rule_type == "contains":
                    result = await self._evaluate_contains_rule(rule, content)
                    rule_results.append(result)

                else:
                    logger.warning(f"Unknown rule type: {rule_type}")

            # Calculate weighted score
            total_weight = sum(r.weight for r in rule_results)
            if total_weight > 0:
                weighted_score = sum(r.score * r.weight for r in rule_results) / total_weight
            else:
                weighted_score = 1.0

            # Determine pass/fail
            passed = weighted_score >= failure_threshold
            passed_count = sum(1 for r in rule_results if r.passed)
            failed_count = len(rule_results) - passed_count

            # Build summary message
            if passed:
                message = f"All guardrails passed (score: {weighted_score:.2f})"
            else:
                failed_names = [r.rule_name for r in rule_results if not r.passed]
                message = f"Guardrails failed (score: {weighted_score:.2f}). Failed rules: {', '.join(failed_names)}"

            output = GuardrailOutput(
                passed=passed,
                score=weighted_score,
                failure_action=failure_action,
                rule_results=rule_results,
                total_rules=len(rule_results),
                passed_rules=passed_count,
                failed_rules=failed_count,
                input_data=content,
                message=message
            )

            # Determine node status based on failure action
            if not passed and failure_action == "reject":
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    output=output.model_dump(),
                    error=message,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                elapsed_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            logger.exception(f"Guardrail execution failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Guardrail execution failed: {str(e)}",
                elapsed_ms=int((time.time() - start_time) * 1000)
            )
