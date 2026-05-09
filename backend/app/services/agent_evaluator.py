"""Agent Evaluator Service for LLM-as-judge evaluation.

Part of Phase 5: Quality Assurance.

Provides real agent execution and LLM-as-judge evaluation capabilities
for Agent Studio test cases. Supports:
- Real agent execution with configured LLM providers
- GPT-4/Claude-based evaluation judging
- Cost tracking and result caching
- Accuracy history tracking
"""
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID, uuid4
from datetime import datetime
import json
import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.agent_test_case import AgentTestCase
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.custom_agent import CustomAgent
from app.models.agent_version import AgentVersion
from app.config import get_settings
from app.config.llm_pricing import calculate_cost

logger = logging.getLogger(__name__)
settings = get_settings()

# LLM Judge evaluation prompt
LLM_JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for AI agent responses.
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


class EvaluationResult:
    """Result of evaluating a single test case."""

    def __init__(
        self,
        test_case_id: UUID,
        passed: bool,
        actual_output: str,
        expected_output: Optional[str],
        score: Optional[float],
        evaluation_type: str,
        duration_ms: int,
        tokens_used: Optional[int] = None,
        category: Optional[str] = None,
        cost_usd: Optional[float] = None,
        judge_reasoning: Optional[str] = None,
        cached: bool = False,
    ):
        self.test_case_id = test_case_id
        self.passed = passed
        self.actual_output = actual_output
        self.expected_output = expected_output
        self.score = score
        self.evaluation_type = evaluation_type
        self.duration_ms = duration_ms
        self.tokens_used = tokens_used
        self.category = category
        self.cost_usd = cost_usd
        self.judge_reasoning = judge_reasoning
        self.cached = cached

    def to_dict(self) -> dict:
        return {
            'test_case_id': str(self.test_case_id),
            'passed': self.passed,
            'actual_output': self.actual_output,
            'expected_output': self.expected_output,
            'score': self.score,
            'evaluation_type': self.evaluation_type,
            'duration_ms': self.duration_ms,
            'tokens_used': self.tokens_used,
            'category': self.category,
            'cost_usd': self.cost_usd,
            'judge_reasoning': self.judge_reasoning,
            'cached': self.cached,
        }


class CostTracker:
    """Tracks token usage and costs for evaluation runs.

    Uses centralized pricing from app.config.llm_pricing.
    """

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.calls = []

    def add_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        call_type: str = "agent"
    ):
        """Track a single LLM call."""
        # Use centralized pricing config
        total_cost = calculate_cost(model, input_tokens, output_tokens)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += total_cost

        self.calls.append({
            "model": model,
            "type": call_type,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": total_cost,
        })

        return total_cost

    def get_summary(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_calls": len(self.calls),
            "calls_by_type": {
                "agent": sum(1 for c in self.calls if c["type"] == "agent"),
                "judge": sum(1 for c in self.calls if c["type"] == "judge"),
            }
        }


class EvaluationCache:
    """In-memory cache for evaluation results to avoid duplicate LLM calls."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _make_key(
        self,
        agent_config_hash: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create cache key from agent config and input."""
        context_str = json.dumps(context, sort_keys=True) if context else ""
        content = f"{agent_config_hash}:{input_text}:{context_str}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get(
        self,
        agent_config_hash: str,
        input_text: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Get cached response if available."""
        key = self._make_key(agent_config_hash, input_text, context)
        cached = self._cache.get(key)
        if cached:
            logger.debug(f"Cache hit for key {key[:8]}...")
            return cached.get("response")
        return None

    def set(
        self,
        agent_config_hash: str,
        input_text: str,
        response: str,
        context: Optional[Dict[str, Any]] = None
    ):
        """Cache a response."""
        key = self._make_key(agent_config_hash, input_text, context)
        self._cache[key] = {
            "response": response,
            "cached_at": datetime.utcnow().isoformat(),
        }
        logger.debug(f"Cached response for key {key[:8]}...")

    def clear(self):
        """Clear the cache."""
        self._cache.clear()


class AgentEvaluator:
    """Evaluator for agent test cases using real LLM execution and LLM-as-judge."""

    def __init__(self, db: AsyncSession, tenant_id: Optional[UUID] = None, use_cache: bool = True):
        self.db = db
        self.tenant_id = tenant_id
        self.cost_tracker = CostTracker()
        self.cache = EvaluationCache() if use_cache else None

    async def evaluate_test_case(
        self,
        test_case: AgentTestCase,
        agent: CustomAgent,
        version: Optional[AgentVersion] = None,
    ) -> EvaluationResult:
        """
        Evaluate a single test case against an agent.

        Uses real agent execution and LLM-as-judge for evaluation.

        Args:
            test_case: The test case to evaluate
            agent: The agent to test
            version: Optional specific version to test

        Returns:
            EvaluationResult with pass/fail status and details
        """
        start_time = datetime.utcnow()
        cached = False
        tokens_used = None
        cost_usd = None
        judge_reasoning = None

        # Get agent config hash for caching
        agent_config = version.config if version else agent.config
        config_hash = hashlib.sha256(
            json.dumps(agent_config, sort_keys=True).encode()
        ).hexdigest()[:16]

        # Check cache first
        if self.cache:
            cached_response = self.cache.get(
                config_hash,
                test_case.input,
                test_case.context
            )
            if cached_response:
                actual_output = cached_response
                cached = True
            else:
                actual_output = await self._run_agent(
                    agent, test_case.input, test_case.context, agent_config
                )
                self.cache.set(config_hash, test_case.input, actual_output, test_case.context)
        else:
            actual_output = await self._run_agent(
                agent, test_case.input, test_case.context, agent_config
            )

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Evaluate based on type
        passed = False
        score = None

        if test_case.evaluation_type == 'exact':
            passed = actual_output.strip() == (test_case.expected_output or '').strip()
            score = 1.0 if passed else 0.0

        elif test_case.evaluation_type == 'contains':
            if test_case.expected_output:
                passed = test_case.expected_output.lower() in actual_output.lower()
            else:
                passed = len(actual_output) > 0
            score = 1.0 if passed else 0.0

        elif test_case.evaluation_type == 'llm_judge':
            passed, score, judge_reasoning, judge_tokens = await self._llm_judge(
                input_text=test_case.input,
                actual_output=actual_output,
                expected_output=test_case.expected_output,
                is_positive=test_case.is_positive,
                criteria=test_case.evaluation_criteria,
            )
            tokens_used = judge_tokens
            cost_usd = self.cost_tracker.total_cost_usd

        # Record result on test case
        test_case.record_result(passed, actual_output, score)

        return EvaluationResult(
            test_case_id=test_case.id,
            passed=passed,
            actual_output=actual_output,
            expected_output=test_case.expected_output,
            score=score,
            evaluation_type=test_case.evaluation_type,
            duration_ms=duration_ms,
            tokens_used=tokens_used,
            category=test_case.category,
            cost_usd=cost_usd,
            judge_reasoning=judge_reasoning,
            cached=cached,
        )

    async def evaluate_all(
        self,
        agent_id: UUID,
        tenant_id: UUID,
        version_id: Optional[UUID] = None,
        baseline_only: bool = False,
        test_case_ids: Optional[List[UUID]] = None,
        progress_callback=None,
    ) -> Tuple[List[EvaluationResult], AgentAccuracyHistory, dict]:
        """
        Evaluate all test cases for an agent.

        Args:
            agent_id: Agent to evaluate
            tenant_id: Tenant ID for multi-tenancy
            version_id: Optional specific version to evaluate
            baseline_only: Only evaluate baseline test cases
            test_case_ids: Optional list of specific test case IDs
            progress_callback: Optional async callback for progress updates

        Returns:
            Tuple of (list of results, accuracy history entry, cost summary)
        """
        evaluation_run_id = uuid4()
        start_time = datetime.utcnow()

        # Reset cost tracker for this run
        self.cost_tracker = CostTracker()

        # Get agent
        agent_result = await self.db.execute(
            select(CustomAgent).where(CustomAgent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        # Get version if specified
        version = None
        if version_id:
            version_result = await self.db.execute(
                select(AgentVersion).where(AgentVersion.id == version_id)
            )
            version = version_result.scalar_one_or_none()

        # Build test case query
        query = select(AgentTestCase).where(
            AgentTestCase.agent_id == agent_id,
            AgentTestCase.is_active == True
        )

        if baseline_only:
            query = query.where(AgentTestCase.is_baseline == True)

        if test_case_ids:
            query = query.where(AgentTestCase.id.in_(test_case_ids))

        result = await self.db.execute(query)
        test_cases = result.scalars().all()

        if not test_cases:
            raise ValueError("No test cases found for evaluation")

        # Get previous accuracy for delta calculation
        prev_result = await self.db.execute(
            select(AgentAccuracyHistory)
            .where(AgentAccuracyHistory.agent_id == agent_id)
            .order_by(AgentAccuracyHistory.evaluated_at.desc())
            .limit(1)
        )
        prev_accuracy_entry = prev_result.scalar_one_or_none()
        previous_accuracy = prev_accuracy_entry.accuracy if prev_accuracy_entry else None

        # Evaluate each test case
        results: List[EvaluationResult] = []
        total = len(test_cases)

        for i, test_case in enumerate(test_cases):
            eval_result = await self.evaluate_test_case(test_case, agent, version)
            results.append(eval_result)

            # Report progress
            if progress_callback:
                await progress_callback({
                    'total': total,
                    'completed': i + 1,
                    'passed': sum(1 for r in results if r.passed),
                    'failed': sum(1 for r in results if not r.passed),
                    'current_test_case_id': str(test_case.id),
                    'current_test_case_name': test_case.name,
                    'cost_so_far_usd': self.cost_tracker.total_cost_usd,
                })

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Create accuracy history entry
        accuracy_entry = AgentAccuracyHistory.create_from_results(
            tenant_id=tenant_id,
            agent_id=agent_id,
            test_results=[{'passed': r.passed, 'category': r.category} for r in results],
            agent_version_id=version_id,
            previous_accuracy=previous_accuracy,
            duration_ms=duration_ms,
            evaluation_run_id=evaluation_run_id,
        )

        self.db.add(accuracy_entry)
        await self.db.commit()
        await self.db.refresh(accuracy_entry)

        return results, accuracy_entry, self.cost_tracker.get_summary()

    async def _run_agent(
        self,
        agent: CustomAgent,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Run the agent with the given input using real LLM execution.

        Args:
            agent: The CustomAgent to run
            input_text: User input text
            context: Optional context data
            agent_config: Agent configuration (model, system prompt, etc.)

        Returns:
            Agent response text
        """
        config = agent_config or agent.config or {}

        # Get model configuration
        model = config.get("model", settings.DEFAULT_LLM_MODEL)
        system_prompt = config.get("system_prompt", agent.system_prompt or "")
        temperature = config.get("temperature", 0.7)

        # Build user message with context
        user_message = input_text
        if context:
            context_str = "\n".join([f"{k}: {v}" for k, v in context.items()])
            user_message = f"Context:\n{context_str}\n\nUser: {input_text}"

        try:
            response = await self._call_llm(
                model=model,
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                call_type="agent",
            )
            return response

        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            return f"Error: {str(e)}"

    async def _llm_judge(
        self,
        input_text: str,
        actual_output: str,
        expected_output: Optional[str],
        is_positive: bool,
        criteria: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, float, str, int]:
        """
        Use LLM as judge to evaluate response quality.

        Args:
            input_text: Original user input
            actual_output: Agent's response
            expected_output: Expected response (if any)
            is_positive: Whether this is a positive example
            criteria: Custom evaluation criteria

        Returns:
            Tuple of (passed, score, reasoning, tokens_used)
        """
        # Build evaluation prompt
        eval_type = "POSITIVE" if is_positive else "NEGATIVE"
        expected_str = expected_output or "(No specific expected output provided)"

        criteria_str = ""
        if criteria:
            criteria_str = f"\n\nCustom evaluation criteria:\n{json.dumps(criteria, indent=2)}"

        judge_prompt = f"""Evaluate the following agent response:

INPUT: {input_text}

ACTUAL RESPONSE: {actual_output}

EXPECTED (for reference): {expected_str}

EXAMPLE TYPE: {eval_type}
{criteria_str}

Remember:
- For POSITIVE examples, check if actual matches expected meaning
- For NEGATIVE examples, check if agent avoided the bad pattern

Respond with ONLY a JSON object: {{"passed": bool, "score": float, "reasoning": "..."}}"""

        try:
            # Use GPT-4o-mini for cost-effective judging
            judge_model = getattr(settings, 'EVALUATION_JUDGE_MODEL', 'gpt-4o-mini')

            response = await self._call_llm(
                model=judge_model,
                system_prompt=LLM_JUDGE_SYSTEM_PROMPT,
                user_message=judge_prompt,
                temperature=0.0,  # Deterministic judging
                call_type="judge",
            )

            # Parse JSON response
            # Clean up response (handle potential markdown code blocks)
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            response = response.strip()

            result = json.loads(response)

            passed = result.get("passed", False)
            score = float(result.get("score", 0.0))
            reasoning = result.get("reasoning", "")

            return passed, score, reasoning, 0  # tokens tracked separately

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse judge response: {e}")
            # Fall back to heuristic evaluation
            return self._heuristic_judge(
                actual_output, expected_output, is_positive
            )

        except Exception as e:
            logger.error(f"LLM judge failed: {e}")
            return self._heuristic_judge(
                actual_output, expected_output, is_positive
            )

    def _heuristic_judge(
        self,
        actual_output: str,
        expected_output: Optional[str],
        is_positive: bool
    ) -> Tuple[bool, float, str, int]:
        """
        Fallback heuristic evaluation when LLM judge fails.

        Uses simple word overlap and length checks.
        """
        if is_positive and expected_output:
            # Check for semantic similarity via word overlap
            expected_words = set(expected_output.lower().split())
            actual_words = set(actual_output.lower().split())
            overlap = len(expected_words & actual_words)
            total = len(expected_words | actual_words)
            score = overlap / total if total > 0 else 0.0
            passed = score >= 0.3  # Threshold for similarity
            reasoning = f"Heuristic: {overlap}/{len(expected_words)} keyword overlap"

        elif not is_positive:
            # For negative examples, check agent responded safely
            passed = len(actual_output) > 10
            score = 1.0 if passed else 0.0
            reasoning = "Heuristic: Response length check for negative example"

        else:
            # No expected output, check response quality
            passed = len(actual_output) > 10
            score = 0.5 if passed else 0.0
            reasoning = "Heuristic: Basic response length check"

        return passed, score, reasoning, 0

    async def _call_llm(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        call_type: str = "agent",
    ) -> str:
        """
        Make an LLM API call with token tracking.

        Resolves the tenant's LLM provider via resolve_effective_provider,
        falling back to env vars only for bootstrap (no providers configured).
        """
        from openai import AsyncOpenAI
        from anthropic import AsyncAnthropic
        from app.services.llm_gateway import resolve_effective_provider

        # --- Resolve provider config from tenant DB settings ---
        provider_type: str
        api_key: str
        api_base: Optional[str] = None
        resolved_model: str = model

        effective = None
        if self.tenant_id is not None:
            effective = await resolve_effective_provider(self.db, self.tenant_id)

        if effective:
            provider_type = effective.provider_type
            api_key = effective.api_key
            api_base = effective.api_base
            # Use the caller-specified model (agent config or judge model),
            # but fall back to the provider's default if not explicitly set.
            resolved_model = model or effective.model
        else:
            # Bootstrap fallback: no tenant providers configured — use env vars
            logger.warning("No tenant provider found; falling back to env var keys")
            provider_type = settings.DEFAULT_LLM_PROVIDER
            resolved_model = model
            if provider_type == "openrouter":
                api_key = settings.OPENROUTER_API_KEY
                api_base = getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            elif provider_type == "anthropic":
                api_key = settings.ANTHROPIC_API_KEY
            else:
                api_key = settings.OPENAI_API_KEY

        # --- Also infer provider_type from model name when needed ---
        if "claude" in resolved_model.lower() and provider_type not in ("anthropic", "openrouter"):
            provider_type = "anthropic"

        # --- Anthropic path ---
        if provider_type == "anthropic":
            # Strip any routing prefix (e.g. "anthropic/claude-3...")
            clean_model = resolved_model
            if clean_model.startswith("anthropic/"):
                clean_model = clean_model[len("anthropic/"):]

            client = AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=clean_model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                temperature=temperature,
            )

            if response.usage:
                self.cost_tracker.add_call(
                    model=clean_model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    call_type=call_type,
                )

            return response.content[0].text if response.content else ""

        # --- OpenAI / OpenRouter path (both use the OpenAI SDK) ---
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if provider_type == "openrouter":
            client_kwargs["base_url"] = api_base or "https://openrouter.ai/api/v1"
        elif api_base:
            client_kwargs["base_url"] = api_base

        client = AsyncOpenAI(**client_kwargs)
        response = await client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
        )

        # Track tokens (estimate for OpenRouter if usage missing)
        input_tokens = len(system_prompt + user_message) // 4
        output_tokens = len(response.choices[0].message.content or "") // 4

        if response.usage:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens

        self.cost_tracker.add_call(
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            call_type=call_type,
        )

        return response.choices[0].message.content or ""
