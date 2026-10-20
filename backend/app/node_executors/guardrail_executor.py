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

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import jsonschema

from app.node_executors import (
    BaseNodeExecutor,
    NodeExecutionContext,
    NodeExecutionResult,
    PrepareResult,
    legacy_complete,
)
from app.services.llm_gateway import normalize_model_id

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

    def _judge_llm_rule(self, rule: Dict[str, Any], response_text: Optional[str], duration_ms: int = 0) -> RuleResult:
        """Pure: judge one LLM rule against the model's answer (``None`` means no answer arrived)."""
        rule_name = rule.get("name", "llm_check")
        expected_value = rule.get("expectedValue", "YES")
        if response_text is None:
            return RuleResult(
                rule_name=rule_name,
                rule_type="llm",
                passed=False,
                score=0.0,
                weight=rule.get("weight", 1.0),
                details="Evaluation error: no answer for this rule",
                duration_ms=duration_ms,
            )
        response_clean = response_text.strip().upper()
        expected_clean = str(expected_value).strip().upper()
        passed = expected_clean in response_clean or response_clean == expected_clean
        return RuleResult(
            rule_name=rule_name,
            rule_type="llm",
            passed=passed,
            score=1.0 if passed else 0.0,
            weight=rule.get("weight", 1.0),
            details=f"Expected '{expected_value}', got '{response_text}'",
            raw_output=response_text,
            duration_ms=duration_ms,
        )

    def build_prompt(self, context: NodeExecutionContext) -> Optional[Dict[str, Any]]:
        """Pure: one prompt that asks every ``llm`` rule's question, or None when no rule needs a model."""
        rules = context.node_data.get("rules", [])
        llm_rules = [r for r in rules if r.get("type", "regex") == "llm"]
        if not llm_rules:
            return None
        _, content_str = self._extract_content(context)
        questions = "\n\n".join(
            f"{i}. {r.get('prompt', 'Is this content appropriate?')}" for i, r in enumerate(llm_rules, 1)
        )
        system = (
            "You are an evaluator. Answer concisely based on the evaluation criteria. "
            "Respond with exactly the expected format specified."
        )
        prompt = (
            f"Content to evaluate:\n---\n{content_str}\n---\n\n"
            f"Answer each numbered question about the content, in the format the question asks for.\n\n"
            f"{questions}\n\n"
            f'Respond with ONLY a JSON object: {{"answers": ["<answer 1>", "<answer 2>", ...]}} '
            f"with exactly {len(llm_rules)} answer(s), in order."
        )
        return {
            "system": system,
            "prompt": prompt,
            "count": len(llm_rules),
            "model": normalize_model_id(context.node_data.get("model", "gpt-4o-mini")),
        }

    @staticmethod
    def parse_llm_answers(text: str, count: int) -> List[Optional[str]]:
        """Pure: the per-rule answers from the model's JSON reply (a lone rule also accepts plain text)."""
        raw = (text or "").strip()
        candidate = raw
        if "```json" in raw:
            candidate = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            candidate = raw.split("```")[1].split("```")[0]
        try:
            data = json.loads(candidate.strip())
            answers = data.get("answers") if isinstance(data, dict) else data
            if isinstance(answers, list):
                out = [None if a is None else str(a) for a in answers[:count]]
                return out + [None] * (count - len(out))
        except (json.JSONDecodeError, AttributeError):
            pass
        if count == 1 and raw:
            return [raw]
        return [None] * count

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

    def _extract_content(self, context: NodeExecutionContext) -> tuple:
        """The content to evaluate: the first predecessor's output, else the ``_input`` variable."""
        content = None
        for input_value in context.inputs.values():
            if isinstance(input_value, dict):
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
        return content, str(content) if not isinstance(content, str) else content

    async def _evaluate(
        self,
        context: NodeExecutionContext,
        llm_answers: Optional[List[Optional[str]]],
        *,
        reject_status: str,
        tokens: int = 0,
    ) -> NodeExecutionResult:
        """Evaluate every rule (LLM rules from ``llm_answers``) and build the node result."""
        node_data = context.node_data
        rules = node_data.get("rules", [])
        failure_action = node_data.get("failureAction", "reject")
        failure_threshold = node_data.get("failureThreshold", 0.7)
        content, content_str = self._extract_content(context)

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
                    message="No rules configured - passed by default",
                ).model_dump(),
            )

        rule_results: List[RuleResult] = []
        llm_index = 0
        for rule in rules:
            rule_type = rule.get("type", "regex")
            if rule_type == "llm":
                answer = None
                if llm_answers is not None and llm_index < len(llm_answers):
                    answer = llm_answers[llm_index]
                llm_index += 1
                rule_results.append(self._judge_llm_rule(rule, answer))
            elif rule_type == "regex":
                rule_results.append(await self._evaluate_regex_rule(rule, content_str))
            elif rule_type == "schema":
                rule_results.append(await self._evaluate_schema_rule(rule, content))
            elif rule_type == "threshold":
                rule_results.append(await self._evaluate_threshold_rule(rule, content))
            elif rule_type == "length":
                rule_results.append(await self._evaluate_length_rule(rule, content))
            elif rule_type == "contains":
                rule_results.append(await self._evaluate_contains_rule(rule, content))
            else:
                logger.warning(f"Unknown rule type: {rule_type}")

        total_weight = sum(r.weight for r in rule_results)
        if total_weight > 0:
            weighted_score = sum(r.score * r.weight for r in rule_results) / total_weight
        else:
            weighted_score = 1.0

        passed = weighted_score >= failure_threshold
        passed_count = sum(1 for r in rule_results if r.passed)
        failed_count = len(rule_results) - passed_count

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
            message=message,
        )

        if not passed and failure_action == "reject":
            return NodeExecutionResult(
                node_id=context.node_id,
                status=reject_status,
                output=output.model_dump(),
                error=message,
                tokens_used=tokens or None,
            )
        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output=output.model_dump(),
            tokens_used=tokens or None,
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        """Rules that need no model are decided here; ``llm`` rules become one generation."""
        built = self.build_prompt(context)
        if built is None:
            return await self._evaluate(context, None, reject_status="blocked")
        return PrepareResult.llm(
            system=built["system"],
            prompt=built["prompt"],
            model=built["model"] if context.node_data.get("model") else None,
            tier="low",
            response_format="json",
        )

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        """A reject verdict is ``blocked`` (with the reason); a pass is ``success``."""
        built = self.build_prompt(context)
        answers = (
            self.parse_llm_answers(PrepareResult.answer_text(finish_input), built["count"]) if built else None
        )
        return await self._evaluate(
            context, answers, reject_status="blocked", tokens=PrepareResult.answer_tokens(finish_input)
        )

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """Legacy in-process path over the same functions (removed by U30); a reject stays ``failed``."""
        start_time = time.time()
        try:
            built = self.build_prompt(context)
            answers = None
            tokens = 0
            if built is not None:
                try:
                    completion = await legacy_complete(
                        db,
                        context.tenant_id,
                        built["model"],
                        built["system"],
                        built["prompt"],
                        temperature=0.1,
                        max_tokens=100 * built["count"],
                    )
                    answers = self.parse_llm_answers(completion["text"], built["count"])
                    tokens = completion["tokens"]
                except Exception as e:
                    logger.error(f"LLM rule evaluation failed: {e}")
            result = await self._evaluate(context, answers, reject_status="failed", tokens=tokens)
            result.elapsed_ms = int((time.time() - start_time) * 1000)
            return result
        except Exception as e:
            logger.exception(f"Guardrail execution failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Guardrail execution failed: {str(e)}",
                elapsed_ms=int((time.time() - start_time) * 1000)
            )
