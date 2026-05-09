#!/usr/bin/env python3
"""
Full Evaluation Suite with Real LLM Execution.

Runs agent evaluation test cases with actual LLM calls for Phase 1 Production Readiness.

Usage:
    # Run with default model from .env (qwen/qwen3-32b via OpenRouter)
    python -m tests.evaluation.run_full_evaluation --agent data_retrieval_agent

    # Run all agents
    python -m tests.evaluation.run_full_evaluation --all

    # Run specific category with cost tracking
    python -m tests.evaluation.run_full_evaluation --agent compliance_agent --category aml

    # Limit tests per agent (for quick validation)
    python -m tests.evaluation.run_full_evaluation --all --max-tests 5

    # Dry run - show what would run
    python -m tests.evaluation.run_full_evaluation --all --dry-run

Part of Phase 1: Production Readiness - P1.4 Agent Evaluation Pipeline.
"""
import asyncio
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from uuid import uuid4

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(env_path)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Test cases directory
TEST_CASES_DIR = Path(__file__).parent / "test_cases"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class TestCase:
    """Test case data."""
    id: str
    name: str
    input: str
    expected_output: str
    evaluation_type: str = "llm_judge"
    is_positive: bool = True
    category: str = "general"
    criteria: str = ""
    timeout_seconds: int = 60


@dataclass
class EvaluationResult:
    """Result of evaluating a single test case."""
    test_case_id: str
    test_case_name: str
    category: str
    passed: bool
    score: float
    actual_output: str
    expected_output: str
    judge_reasoning: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None


@dataclass
class CostSummary:
    """Cost tracking summary."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    agent_calls: int = 0
    judge_calls: int = 0


class LLMClient:
    """
    Unified LLM client supporting OpenRouter, OpenAI, and Anthropic.

    Uses configuration from environment variables.
    """

    def __init__(self):
        self.provider = os.getenv("DEFAULT_LLM_PROVIDER", "openrouter")
        self.model = os.getenv("DEFAULT_LLM_MODEL", "qwen/qwen3-32b")
        self.judge_model = os.getenv("EVALUATION_JUDGE_MODEL", "qwen/qwen3-32b")
        self.cost_tracker = CostSummary()

        # API keys
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")

        logger.info(f"LLM Client initialized: provider={self.provider}, model={self.model}")

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        call_type: str = "agent",
    ) -> tuple[str, int, int]:
        """
        Make an LLM API call.

        Args:
            system_prompt: System prompt
            user_message: User message
            model: Model override (defaults to configured model)
            temperature: Temperature for generation
            call_type: "agent" or "judge" for cost tracking

        Returns:
            Tuple of (response_text, input_tokens, output_tokens)
        """
        model = model or self.model

        if self.provider == "openrouter":
            return await self._call_openrouter(
                system_prompt, user_message, model, temperature, call_type
            )
        elif self.provider == "openai" or "gpt" in model.lower():
            return await self._call_openai(
                system_prompt, user_message, model, temperature, call_type
            )
        elif self.provider == "anthropic" or "claude" in model.lower():
            return await self._call_anthropic(
                system_prompt, user_message, model, temperature, call_type
            )
        else:
            return await self._call_openrouter(
                system_prompt, user_message, model, temperature, call_type
            )

    async def _call_openrouter(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temperature: float,
        call_type: str,
    ) -> tuple[str, int, int]:
        """Call OpenRouter API."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.openrouter_key,
        )

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )

        # Track tokens
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        self._track_cost(input_tokens, output_tokens, call_type, model)

        return response.choices[0].message.content or "", input_tokens, output_tokens

    async def _call_openai(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temperature: float,
        call_type: str,
    ) -> tuple[str, int, int]:
        """Call OpenAI API."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.openai_key)

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        self._track_cost(input_tokens, output_tokens, call_type, model)

        return response.choices[0].message.content or "", input_tokens, output_tokens

    async def _call_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temperature: float,
        call_type: str,
    ) -> tuple[str, int, int]:
        """Call Anthropic API."""
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.anthropic_key)

        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            temperature=temperature,
        )

        input_tokens = response.usage.input_tokens if response.usage else 0
        output_tokens = response.usage.output_tokens if response.usage else 0

        self._track_cost(input_tokens, output_tokens, call_type, model)

        return response.content[0].text if response.content else "", input_tokens, output_tokens

    def _track_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        call_type: str,
        model: str,
    ):
        """Track token usage and estimate cost."""
        self.cost_tracker.total_input_tokens += input_tokens
        self.cost_tracker.total_output_tokens += output_tokens

        if call_type == "agent":
            self.cost_tracker.agent_calls += 1
        else:
            self.cost_tracker.judge_calls += 1

        # Estimate cost (rough OpenRouter rates)
        # Most models: ~$0.50-2.00 per 1M tokens
        cost_per_1k_input = 0.0005  # $0.50 per 1M
        cost_per_1k_output = 0.0015  # $1.50 per 1M

        cost = (input_tokens * cost_per_1k_input / 1000) + (output_tokens * cost_per_1k_output / 1000)
        self.cost_tracker.total_cost_usd += cost


# LLM Judge System Prompt
LLM_JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for AI agent responses in a financial analysis platform.
Your task is to evaluate whether an agent's response meets the expected criteria.

You will receive:
1. The user input/question
2. The agent's actual response
3. The expected response or evaluation criteria
4. Whether this is a positive example (should match) or negative example (should avoid)

For POSITIVE examples:
- Check if the actual response conveys the same meaning/intent as expected
- Minor wording differences are acceptable
- The response should be accurate, complete, and appropriate
- Financial data should be reasonable (not obviously fake)

For NEGATIVE examples:
- Check if the agent correctly avoided the problematic pattern
- The response should NOT contain harmful, incorrect, or inappropriate content
- A safe/helpful alternative response is acceptable

Respond with ONLY a JSON object in this exact format:
{
  "passed": true/false,
  "score": 0.0-1.0,
  "reasoning": "Brief explanation of your evaluation"
}

Score guidelines:
- 1.0: Perfect match or clearly correct behavior
- 0.8-0.9: Good response with minor issues
- 0.5-0.7: Partially correct
- 0.0-0.4: Failed or incorrect"""


class AgentEvaluatorRunner:
    """
    Runs agent evaluations with real LLM execution.

    Uses the configured default model for agent responses
    and LLM-as-judge for evaluation.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def _get_agent_system_prompt(self, agent_type: str) -> str:
        """Get system prompt for an agent type."""
        prompts = {
            "data_retrieval_agent": """You are a financial data retrieval agent. Your role is to fetch and present financial data including:
- Stock prices and quotes
- Company financials and metrics
- Market indices and performance
- Economic indicators

Provide accurate, well-formatted responses with relevant data points. If you cannot fetch real-time data, provide realistic sample data with appropriate disclaimers.""",

            "analysis_agent": """You are a financial analysis agent. Your role is to:
- Calculate financial ratios and metrics
- Analyze company performance
- Provide valuation assessments
- Generate insights from financial data

Present your analysis clearly with calculations shown and appropriate context.""",

            "compliance_agent": """You are a compliance checking agent for financial services. Your role is to:
- Screen transactions for regulatory compliance
- Check AML/KYC requirements
- Assess risk levels
- Flag potential compliance issues

Provide clear assessments with reasoning and risk classifications.""",

            "research_agent": """You are a financial research agent. Your role is to:
- Research companies and industries
- Analyze market trends
- Summarize SEC filings and reports
- Provide investment insights

Present research findings clearly with sources and supporting evidence.""",
        }
        return prompts.get(agent_type, "You are a helpful financial assistant.")

    async def run_agent(
        self,
        agent_type: str,
        user_input: str,
    ) -> tuple[str, int, int]:
        """Run an agent with the given input."""
        system_prompt = self._get_agent_system_prompt(agent_type)

        response, input_tokens, output_tokens = await self.llm.call(
            system_prompt=system_prompt,
            user_message=user_input,
            temperature=0.7,
            call_type="agent",
        )

        return response, input_tokens, output_tokens

    async def judge_response(
        self,
        user_input: str,
        actual_output: str,
        expected_output: str,
        is_positive: bool,
        criteria: str,
    ) -> tuple[bool, float, str]:
        """Use LLM as judge to evaluate response."""
        eval_type = "POSITIVE" if is_positive else "NEGATIVE"

        judge_prompt = f"""Evaluate the following agent response:

INPUT: {user_input}

ACTUAL RESPONSE: {actual_output}

EXPECTED (for reference): {expected_output}

EXAMPLE TYPE: {eval_type}

Additional criteria: {criteria if criteria else "None specified"}

Respond with ONLY a JSON object: {{"passed": bool, "score": float, "reasoning": "..."}}"""

        response, _, _ = await self.llm.call(
            system_prompt=LLM_JUDGE_SYSTEM_PROMPT,
            user_message=judge_prompt,
            model=self.llm.judge_model,
            temperature=0.0,  # Deterministic judging
            call_type="judge",
        )

        # Parse JSON response
        try:
            # Clean up response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            response = response.strip()

            result = json.loads(response)
            return (
                result.get("passed", False),
                float(result.get("score", 0.0)),
                result.get("reasoning", ""),
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse judge response: {e}")
            # Heuristic fallback
            passed = len(actual_output) > 10 and "error" not in actual_output.lower()
            return passed, 0.5 if passed else 0.0, "Fallback heuristic evaluation"

    async def evaluate_test_case(
        self,
        test_case: TestCase,
        agent_type: str,
    ) -> EvaluationResult:
        """Evaluate a single test case."""
        start_time = datetime.utcnow()

        try:
            # Run agent
            actual_output, input_tokens, output_tokens = await self.run_agent(
                agent_type=agent_type,
                user_input=test_case.input,
            )

            # Judge response
            passed, score, reasoning = await self.judge_response(
                user_input=test_case.input,
                actual_output=actual_output,
                expected_output=test_case.expected_output,
                is_positive=test_case.is_positive,
                criteria=test_case.criteria,
            )

            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            return EvaluationResult(
                test_case_id=test_case.id,
                test_case_name=test_case.name,
                category=test_case.category,
                passed=passed,
                score=score,
                actual_output=actual_output,
                expected_output=test_case.expected_output,
                judge_reasoning=reasoning,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except Exception as e:
            logger.error(f"Error evaluating test case {test_case.id}: {e}")
            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            return EvaluationResult(
                test_case_id=test_case.id,
                test_case_name=test_case.name,
                category=test_case.category,
                passed=False,
                score=0.0,
                actual_output="",
                expected_output=test_case.expected_output,
                judge_reasoning="",
                duration_ms=duration_ms,
                error=str(e),
            )


def load_test_cases(agent_type: str) -> List[TestCase]:
    """Load test cases from JSON file."""
    file_path = TEST_CASES_DIR / f"{agent_type}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Test case file not found: {file_path}")

    with open(file_path) as f:
        data = json.load(f)

    test_cases = []
    for tc_data in data.get("test_cases", []):
        test_cases.append(TestCase(
            id=tc_data.get("id", str(uuid4())),
            name=tc_data.get("name", "Unnamed"),
            input=tc_data.get("input", ""),
            expected_output=tc_data.get("expected_output", ""),
            evaluation_type=tc_data.get("evaluation_type", "llm_judge"),
            is_positive=tc_data.get("is_positive", True),
            category=tc_data.get("category", "general"),
            criteria=tc_data.get("criteria", ""),
            timeout_seconds=tc_data.get("timeout_seconds", 60),
        ))

    return test_cases


def list_available_agents() -> List[str]:
    """List available agent types."""
    return sorted([f.stem for f in TEST_CASES_DIR.glob("*.json")])


async def run_evaluation(
    agent_type: str,
    category: Optional[str] = None,
    max_tests: Optional[int] = None,
    dry_run: bool = False,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Run evaluation for an agent type.

    Args:
        agent_type: Agent type to evaluate
        category: Optional category filter
        max_tests: Maximum tests to run (for quick validation)
        dry_run: Just show what would run
        progress_callback: Callback for progress updates

    Returns:
        Evaluation results summary
    """
    test_cases = load_test_cases(agent_type)

    # Filter by category
    if category:
        test_cases = [tc for tc in test_cases if tc.category == category]

    # Limit tests
    if max_tests:
        test_cases = test_cases[:max_tests]

    if dry_run:
        print(f"\n[DRY RUN] Would run {len(test_cases)} tests for {agent_type}")
        for tc in test_cases:
            print(f"  - {tc.id}: {tc.name} [{tc.category}]")
        return {"dry_run": True, "test_count": len(test_cases)}

    # Initialize LLM client and evaluator
    llm_client = LLMClient()
    evaluator = AgentEvaluatorRunner(llm_client)

    results: List[EvaluationResult] = []
    total = len(test_cases)

    print(f"\n{'='*70}")
    print(f"AGENT EVALUATION: {agent_type}")
    print(f"{'='*70}")
    print(f"Model: {llm_client.model}")
    print(f"Judge Model: {llm_client.judge_model}")
    print(f"Test Cases: {total}")
    if category:
        print(f"Category: {category}")
    print(f"{'='*70}\n")

    for i, test_case in enumerate(test_cases, 1):
        result = await evaluator.evaluate_test_case(test_case, agent_type)
        results.append(result)

        # Print progress
        status = "[PASS]" if result.passed else "[FAIL]"
        print(f"[{i:3}/{total}] {status} {result.test_case_name}")
        print(f"         Score: {result.score:.2f} | Time: {result.duration_ms}ms")
        if result.error:
            print(f"         Error: {result.error}")
        if progress_callback:
            await progress_callback(i, total, result)

    # Calculate summary
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    accuracy = (passed / total * 100) if total > 0 else 0
    avg_score = sum(r.score for r in results) / total if total > 0 else 0
    total_duration = sum(r.duration_ms for r in results)

    # Category breakdown
    categories = {}
    for r in results:
        if r.category not in categories:
            categories[r.category] = {"passed": 0, "failed": 0, "total": 0}
        categories[r.category]["total"] += 1
        if r.passed:
            categories[r.category]["passed"] += 1
        else:
            categories[r.category]["failed"] += 1

    # Print summary
    print(f"\n{'='*70}")
    print("EVALUATION SUMMARY")
    print(f"{'='*70}")
    print(f"Agent Type:      {agent_type}")
    print(f"Total Tests:     {total}")
    print(f"Passed:          {passed} ({accuracy:.1f}%)")
    print(f"Failed:          {failed}")
    print(f"Average Score:   {avg_score:.3f}")
    print(f"Total Duration:  {total_duration / 1000:.2f}s")
    print()
    print("Cost Summary:")
    print(f"  Agent Calls:   {llm_client.cost_tracker.agent_calls}")
    print(f"  Judge Calls:   {llm_client.cost_tracker.judge_calls}")
    print(f"  Input Tokens:  {llm_client.cost_tracker.total_input_tokens:,}")
    print(f"  Output Tokens: {llm_client.cost_tracker.total_output_tokens:,}")
    print(f"  Est. Cost:     ${llm_client.cost_tracker.total_cost_usd:.4f}")
    print()
    print("By Category:")
    for cat, stats in sorted(categories.items()):
        cat_acc = (stats["passed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        print(f"  {cat:20} {stats['passed']:3}/{stats['total']:3} ({cat_acc:.1f}%)")
    print(f"{'='*70}\n")

    # Return results
    return {
        "agent_type": agent_type,
        "model": llm_client.model,
        "total": total,
        "passed": passed,
        "failed": failed,
        "accuracy": accuracy,
        "average_score": avg_score,
        "total_duration_ms": total_duration,
        "cost": {
            "agent_calls": llm_client.cost_tracker.agent_calls,
            "judge_calls": llm_client.cost_tracker.judge_calls,
            "input_tokens": llm_client.cost_tracker.total_input_tokens,
            "output_tokens": llm_client.cost_tracker.total_output_tokens,
            "estimated_cost_usd": llm_client.cost_tracker.total_cost_usd,
        },
        "categories": categories,
        "results": [asdict(r) for r in results],
        "timestamp": datetime.utcnow().isoformat(),
    }


async def run_all_evaluations(
    max_tests_per_agent: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run evaluations for all agents."""
    agents = list_available_agents()
    all_results = {}

    for agent_type in agents:
        try:
            result = await run_evaluation(
                agent_type=agent_type,
                max_tests=max_tests_per_agent,
                dry_run=dry_run,
            )
            all_results[agent_type] = result
        except Exception as e:
            logger.error(f"Failed to evaluate {agent_type}: {e}")
            all_results[agent_type] = {"error": str(e)}

    if dry_run:
        return all_results

    # Overall summary
    print(f"\n{'='*70}")
    print("OVERALL SUMMARY - ALL AGENTS")
    print(f"{'='*70}")

    total_tests = 0
    total_passed = 0
    total_cost = 0.0

    for agent_type, result in all_results.items():
        if "error" not in result:
            total_tests += result["total"]
            total_passed += result["passed"]
            total_cost += result["cost"]["estimated_cost_usd"]
            print(f"{agent_type:25} {result['passed']:3}/{result['total']:3} ({result['accuracy']:.1f}%)")
        else:
            print(f"{agent_type:25} ERROR: {result['error']}")

    overall_accuracy = (total_passed / total_tests * 100) if total_tests > 0 else 0
    print(f"\n{'TOTAL':25} {total_passed:3}/{total_tests:3} ({overall_accuracy:.1f}%)")
    print(f"{'ESTIMATED COST':25} ${total_cost:.4f}")
    print(f"{'='*70}\n")

    return {
        "summary": {
            "total_tests": total_tests,
            "total_passed": total_passed,
            "overall_accuracy": overall_accuracy,
            "total_cost_usd": total_cost,
        },
        "agents": all_results,
        "timestamp": datetime.utcnow().isoformat(),
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run full agent evaluation with real LLM execution"
    )
    parser.add_argument(
        "--agent", "-a",
        help="Agent type to evaluate (e.g., data_retrieval_agent)"
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
        "--max-tests", "-m",
        type=int,
        help="Maximum tests per agent (for quick validation)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run without executing"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available agent types"
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
        return

    if args.all:
        results = asyncio.run(run_all_evaluations(
            max_tests_per_agent=args.max_tests,
            dry_run=args.dry_run,
        ))
    elif args.agent:
        results = asyncio.run(run_evaluation(
            agent_type=args.agent,
            category=args.category,
            max_tests=args.max_tests,
            dry_run=args.dry_run,
        ))
    else:
        parser.print_help()
        sys.exit(1)

    # Save results
    if args.output and not args.dry_run:
        RESULTS_DIR.mkdir(exist_ok=True)
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = RESULTS_DIR / output_path

        with open(output_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()
