"""
Deterministic Evaluator

Evaluates agent outputs using deterministic checks:
- Schema validation (JSON structure matches expected)
- Exact value matching
- Numeric comparison with tolerance
- String containment checks
- Regex pattern matching
"""

import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum


class CheckType(str, Enum):
    """Types of deterministic checks."""
    EXACT_MATCH = "exact_match"
    CONTAINS = "contains"
    CONTAINS_ALL = "contains_all"
    CONTAINS_ANY = "contains_any"
    NUMERIC_EQUALS = "numeric_equals"
    NUMERIC_RANGE = "numeric_range"
    REGEX_MATCH = "regex_match"
    SCHEMA_VALID = "schema_valid"
    NOT_EMPTY = "not_empty"
    LENGTH_EQUALS = "length_equals"
    LENGTH_RANGE = "length_range"


@dataclass
class EvaluationResult:
    """Result of a single evaluation check."""
    passed: bool
    check_type: CheckType
    expected: Any
    actual: Any
    message: str
    field_path: Optional[str] = None


@dataclass
class EvalCaseResult:
    """Result of evaluating a complete test case."""
    test_id: str
    passed: bool
    score: float  # 0-1 based on checks passed
    checks: List[EvaluationResult]
    agent_type: str
    input_text: str
    output: Any
    elapsed_ms: float


class DeterministicEvaluator:
    """
    Evaluates agent outputs using deterministic checks.

    Usage:
        evaluator = DeterministicEvaluator()
        result = evaluator.evaluate(
            test_id="compliance-001",
            agent_type="compliance",
            input_text="Check OFAC for John Doe",
            output={"result": "PASS", "rationale": "No match found in OFAC SDN"},
            expected={
                "result": {"check": "exact_match", "value": "PASS"},
                "rationale_contains": ["No match found", "OFAC"]
            }
        )
    """

    def __init__(self, numeric_tolerance: float = 0.001):
        self.numeric_tolerance = numeric_tolerance

    def evaluate(
        self,
        test_id: str,
        agent_type: str,
        input_text: str,
        output: Any,
        expected: Dict[str, Any],
        elapsed_ms: float = 0,
    ) -> EvalCaseResult:
        """
        Evaluate agent output against expected criteria.

        Args:
            test_id: Unique test case identifier
            agent_type: Type of agent being tested
            input_text: Original input to the agent
            output: Agent's output to evaluate
            expected: Dictionary of expected values/checks
            elapsed_ms: Time taken for agent execution

        Returns:
            TestCaseResult with pass/fail status and details
        """
        checks: List[EvaluationResult] = []

        for field, criteria in expected.items():
            check_results = self._evaluate_field(field, criteria, output)
            checks.extend(check_results)

        passed_count = sum(1 for c in checks if c.passed)
        total_count = len(checks)
        score = passed_count / total_count if total_count > 0 else 1.0

        return EvalCaseResult(
            test_id=test_id,
            passed=all(c.passed for c in checks),
            score=score,
            checks=checks,
            agent_type=agent_type,
            input_text=input_text,
            output=output,
            elapsed_ms=elapsed_ms,
        )

    def _evaluate_field(
        self,
        field: str,
        criteria: Any,
        output: Any,
    ) -> List[EvaluationResult]:
        """Evaluate a single field against criteria."""
        results = []

        # Extract actual value from output
        actual = self._get_value(output, field)

        # Handle different criteria formats
        if isinstance(criteria, dict):
            check_type = criteria.get("check", CheckType.EXACT_MATCH)
            expected_value = criteria.get("value")

            result = self._run_check(check_type, expected_value, actual, criteria, field)
            results.append(result)

        elif field.endswith("_contains"):
            # Shorthand: field_name_contains: ["value1", "value2"]
            base_field = field.replace("_contains", "")
            actual = self._get_value(output, base_field)

            if isinstance(criteria, list):
                result = self._run_check(
                    CheckType.CONTAINS_ALL, criteria, actual, {}, base_field
                )
            else:
                result = self._run_check(
                    CheckType.CONTAINS, criteria, actual, {}, base_field
                )
            results.append(result)

        elif field.endswith("_matches"):
            # Shorthand: field_name_matches: "pattern"
            base_field = field.replace("_matches", "")
            actual = self._get_value(output, base_field)
            result = self._run_check(
                CheckType.REGEX_MATCH, criteria, actual, {}, base_field
            )
            results.append(result)

        else:
            # Default: exact match
            result = self._run_check(
                CheckType.EXACT_MATCH, criteria, actual, {}, field
            )
            results.append(result)

        return results

    def _run_check(
        self,
        check_type: CheckType,
        expected: Any,
        actual: Any,
        criteria: Dict[str, Any],
        field_path: str,
    ) -> EvaluationResult:
        """Execute a specific check type."""
        if check_type == CheckType.EXACT_MATCH:
            passed = actual == expected
            message = f"Expected exact match" if not passed else "Exact match"

        elif check_type == CheckType.CONTAINS:
            passed = expected in str(actual) if actual else False
            message = f"Expected to contain '{expected}'" if not passed else "Contains"

        elif check_type == CheckType.CONTAINS_ALL:
            actual_str = str(actual) if actual else ""
            missing = [v for v in expected if v not in actual_str]
            passed = len(missing) == 0
            message = f"Missing: {missing}" if not passed else "Contains all"

        elif check_type == CheckType.CONTAINS_ANY:
            actual_str = str(actual) if actual else ""
            found = [v for v in expected if v in actual_str]
            passed = len(found) > 0
            message = f"None found from {expected}" if not passed else f"Found: {found}"

        elif check_type == CheckType.NUMERIC_EQUALS:
            try:
                tolerance = criteria.get("tolerance", self.numeric_tolerance)
                passed = abs(float(actual) - float(expected)) <= tolerance
                message = f"Numeric match within tolerance {tolerance}"
            except (TypeError, ValueError):
                passed = False
                message = "Could not compare as numbers"

        elif check_type == CheckType.NUMERIC_RANGE:
            try:
                min_val = criteria.get("min", float("-inf"))
                max_val = criteria.get("max", float("inf"))
                actual_num = float(actual)
                passed = min_val <= actual_num <= max_val
                message = f"Expected in range [{min_val}, {max_val}]"
            except (TypeError, ValueError):
                passed = False
                message = "Could not compare as number"

        elif check_type == CheckType.REGEX_MATCH:
            try:
                passed = bool(re.match(expected, str(actual)))
                message = f"Expected to match pattern: {expected}"
            except re.error:
                passed = False
                message = f"Invalid regex pattern: {expected}"

        elif check_type == CheckType.NOT_EMPTY:
            passed = actual is not None and (
                (isinstance(actual, str) and len(actual.strip()) > 0) or
                (isinstance(actual, (list, dict)) and len(actual) > 0) or
                (not isinstance(actual, (str, list, dict)))
            )
            message = "Expected non-empty value" if not passed else "Not empty"

        elif check_type == CheckType.LENGTH_EQUALS:
            try:
                passed = len(actual) == expected
                message = f"Expected length {expected}, got {len(actual)}"
            except TypeError:
                passed = False
                message = "Cannot get length of value"

        elif check_type == CheckType.LENGTH_RANGE:
            try:
                min_len = criteria.get("min", 0)
                max_len = criteria.get("max", float("inf"))
                actual_len = len(actual)
                passed = min_len <= actual_len <= max_len
                message = f"Expected length in [{min_len}, {max_len}], got {actual_len}"
            except TypeError:
                passed = False
                message = "Cannot get length of value"

        elif check_type == CheckType.SCHEMA_VALID:
            # Validate against a simple schema
            schema = expected
            passed, message = self._validate_schema(actual, schema)

        else:
            passed = False
            message = f"Unknown check type: {check_type}"

        return EvaluationResult(
            passed=passed,
            check_type=check_type,
            expected=expected,
            actual=actual,
            message=message,
            field_path=field_path,
        )

    def _get_value(self, obj: Any, path: str) -> Any:
        """Get nested value from object using dot notation."""
        if obj is None:
            return None

        parts = path.split(".")
        current = obj

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None

            if current is None:
                return None

        return current

    def _validate_schema(
        self,
        value: Any,
        schema: Dict[str, Any],
    ) -> tuple[bool, str]:
        """Validate value against a simple schema definition."""
        if not isinstance(value, dict):
            return False, "Expected dict for schema validation"

        required_fields = schema.get("required", [])
        missing = [f for f in required_fields if f not in value]

        if missing:
            return False, f"Missing required fields: {missing}"

        field_types = schema.get("types", {})
        type_errors = []

        for field, expected_type in field_types.items():
            if field in value:
                actual_type = type(value[field]).__name__
                if actual_type != expected_type:
                    type_errors.append(f"{field}: expected {expected_type}, got {actual_type}")

        if type_errors:
            return False, f"Type mismatches: {type_errors}"

        return True, "Schema valid"
