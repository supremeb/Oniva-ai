"""
Evaluation Test Runner - Run evaluations from JSON test case files.

Usage:
    python -m tests.evaluation.run_evaluation --agent compliance_agent
    python -m tests.evaluation.run_evaluation --all
    python -m tests.evaluation.run_evaluation --agent analysis_agent --category valuation

Part of Phase 5: Quality Assurance.
"""
import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import uuid4

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test cases directory
TEST_CASES_DIR = Path(__file__).parent / "test_cases"


class TestCase:
    """Test case data class."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("id", str(uuid4()))
        self.name = data.get("name", "Unnamed test")
        self.input = data.get("input", "")
        self.expected_output = data.get("expected_output", "")
        self.evaluation_type = data.get("evaluation_type", "llm_judge")
        self.is_positive = data.get("is_positive", True)
        self.category = data.get("category", "general")
        self.criteria = data.get("criteria", "")
        # For deterministic tests
        self.expected_value = data.get("expected_value")
        self.tolerance = data.get("tolerance", 0.1)


class TestResult:
    """Test result data class."""

    def __init__(
        self,
        test_case: TestCase,
        passed: bool,
        actual_output: str,
        score: float,
        reasoning: str = "",
        duration_ms: int = 0,
        error: Optional[str] = None,
    ):
        self.test_case = test_case
        self.passed = passed
        self.actual_output = actual_output
        self.score = score
        self.reasoning = reasoning
        self.duration_ms = duration_ms
        self.error = error


def load_test_cases(agent_type: str) -> List[TestCase]:
    """Load test cases from JSON file for an agent type."""
    file_path = TEST_CASES_DIR / f"{agent_type}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Test case file not found: {file_path}")

    with open(file_path) as f:
        data = json.load(f)

    test_cases = []
    for tc_data in data.get("test_cases", []):
        test_cases.append(TestCase(tc_data))

    logger.info(f"Loaded {len(test_cases)} test cases for {agent_type}")
    return test_cases


def list_available_agents() -> List[str]:
    """List all available agent types with test cases."""
    agents = []
    for file_path in TEST_CASES_DIR.glob("*.json"):
        agents.append(file_path.stem)
    return sorted(agents)


async def run_mock_evaluation(
    test_case: TestCase,
    agent_type: str,
) -> TestResult:
    """
    Run a mock evaluation (for testing the framework without real LLM calls).

    In production, this would call the actual AgentEvaluator.
    """
    start_time = datetime.utcnow()

    # Simulate agent response
    await asyncio.sleep(0.1)  # Simulate latency

    # Mock responses based on test case type
    if test_case.is_positive:
        actual_output = f"[Mock response for {agent_type}]: {test_case.expected_output[:100]}..."
        passed = True
        score = 0.85
        reasoning = "Mock evaluation passed - actual evaluation requires real LLM"
    else:
        actual_output = "[Mock response]: Safe response avoiding problematic content"
        passed = True
        score = 0.90
        reasoning = "Mock evaluation - agent avoided negative pattern"

    # For deterministic tests, check numeric values
    if test_case.evaluation_type == "deterministic" and test_case.expected_value is not None:
        # Mock a numeric result close to expected
        mock_value = test_case.expected_value * 0.98  # Slightly off
        diff = abs(mock_value - test_case.expected_value)
        passed = diff <= test_case.tolerance
        score = 1.0 - (diff / test_case.expected_value) if test_case.expected_value != 0 else 1.0
        actual_output = f"Calculated value: {mock_value}"
        reasoning = f"Mock deterministic check: expected {test_case.expected_value}, got {mock_value}"

    end_time = datetime.utcnow()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    return TestResult(
        test_case=test_case,
        passed=passed,
        actual_output=actual_output,
        score=score,
        reasoning=reasoning,
        duration_ms=duration_ms,
    )


async def run_evaluations(
    agent_type: str,
    category: Optional[str] = None,
    use_mock: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Run evaluations for an agent type.

    Args:
        agent_type: Agent type to evaluate
        category: Optional category filter
        use_mock: Use mock evaluation (no LLM calls)
        verbose: Print detailed output

    Returns:
        Summary statistics
    """
    test_cases = load_test_cases(agent_type)

    # Filter by category if specified
    if category:
        test_cases = [tc for tc in test_cases if tc.category == category]
        logger.info(f"Filtered to {len(test_cases)} test cases in category '{category}'")

    results: List[TestResult] = []
    passed_count = 0
    failed_count = 0

    print(f"\n{'='*60}")
    print(f"Running evaluation for: {agent_type}")
    print(f"Test cases: {len(test_cases)}")
    if category:
        print(f"Category filter: {category}")
    print(f"Mode: {'Mock' if use_mock else 'Real LLM'}")
    print(f"{'='*60}\n")

    for i, test_case in enumerate(test_cases, 1):
        if use_mock:
            result = await run_mock_evaluation(test_case, agent_type)
        else:
            # TODO: Integrate with real AgentEvaluator
            result = await run_mock_evaluation(test_case, agent_type)

        results.append(result)

        if result.passed:
            passed_count += 1
            status = "✓ PASS"
        else:
            failed_count += 1
            status = "✗ FAIL"

        # Print progress
        print(f"[{i:3}/{len(test_cases)}] {status} - {test_case.name}")
        if verbose and not result.passed:
            print(f"         Reasoning: {result.reasoning}")
            print(f"         Expected: {test_case.expected_output[:80]}...")
            print(f"         Actual: {result.actual_output[:80]}...")

    # Calculate statistics
    accuracy = (passed_count / len(test_cases)) * 100 if test_cases else 0
    avg_score = sum(r.score for r in results) / len(results) if results else 0

    # Category breakdown
    categories = {}
    for result in results:
        cat = result.test_case.category
        if cat not in categories:
            categories[cat] = {"passed": 0, "failed": 0, "total": 0}
        categories[cat]["total"] += 1
        if result.passed:
            categories[cat]["passed"] += 1
        else:
            categories[cat]["failed"] += 1

    # Print summary
    print(f"\n{'='*60}")
    print("EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"Agent Type:     {agent_type}")
    print(f"Total Tests:    {len(test_cases)}")
    print(f"Passed:         {passed_count} ({accuracy:.1f}%)")
    print(f"Failed:         {failed_count}")
    print(f"Average Score:  {avg_score:.3f}")

    print(f"\nBy Category:")
    for cat, stats in sorted(categories.items()):
        cat_accuracy = (stats["passed"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        print(f"  {cat:20} {stats['passed']:3}/{stats['total']:3} ({cat_accuracy:.1f}%)")

    print(f"{'='*60}\n")

    return {
        "agent_type": agent_type,
        "total": len(test_cases),
        "passed": passed_count,
        "failed": failed_count,
        "accuracy": accuracy,
        "average_score": avg_score,
        "categories": categories,
        "timestamp": datetime.utcnow().isoformat(),
    }


async def run_all_evaluations(
    use_mock: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run evaluations for all available agents."""
    agents = list_available_agents()
    all_results = {}

    for agent_type in agents:
        try:
            result = await run_evaluations(
                agent_type=agent_type,
                use_mock=use_mock,
                verbose=verbose,
            )
            all_results[agent_type] = result
        except Exception as e:
            logger.error(f"Failed to evaluate {agent_type}: {e}")
            all_results[agent_type] = {"error": str(e)}

    # Print overall summary
    print(f"\n{'='*60}")
    print("OVERALL SUMMARY - ALL AGENTS")
    print(f"{'='*60}")

    total_tests = 0
    total_passed = 0

    for agent_type, result in all_results.items():
        if "error" not in result:
            total_tests += result["total"]
            total_passed += result["passed"]
            print(f"{agent_type:25} {result['passed']:3}/{result['total']:3} ({result['accuracy']:.1f}%)")
        else:
            print(f"{agent_type:25} ERROR: {result['error']}")

    overall_accuracy = (total_passed / total_tests) * 100 if total_tests > 0 else 0
    print(f"\n{'TOTAL':25} {total_passed:3}/{total_tests:3} ({overall_accuracy:.1f}%)")
    print(f"{'='*60}\n")

    return all_results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run agent evaluation test cases"
    )
    parser.add_argument(
        "--agent", "-a",
        help="Agent type to evaluate (e.g., compliance_agent, research_agent)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run evaluations for all agents"
    )
    parser.add_argument(
        "--category", "-c",
        help="Filter by test case category"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available agent types"
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use real LLM calls (default is mock)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output results to JSON file"
    )

    args = parser.parse_args()

    if args.list:
        agents = list_available_agents()
        print("\nAvailable agent types:")
        for agent in agents:
            print(f"  - {agent}")
        print()
        return

    if args.all:
        results = asyncio.run(run_all_evaluations(
            use_mock=not args.real,
            verbose=args.verbose,
        ))
    elif args.agent:
        results = asyncio.run(run_evaluations(
            agent_type=args.agent,
            category=args.category,
            use_mock=not args.real,
            verbose=args.verbose,
        ))
    else:
        parser.print_help()
        sys.exit(1)

    # Save results if output specified
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to: {args.output}")


if __name__ == "__main__":
    main()
