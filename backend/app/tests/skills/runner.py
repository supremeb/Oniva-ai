"""
Skills Test Runner
Provides a framework for unit testing agent skills (tools)
"""

from typing import Any, Dict, List, Optional
import inspect
from unittest.mock import MagicMock
from pydantic import BaseModel, ConfigDict
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from app.agents.base import AgentDependencies
from app.agents.skills.registry import SkillRegistry

class SkillTest(BaseModel):
    """
    Definition of a test case for a skill
    """
    skill_name: str
    description: str
    inputs: Dict[str, Any]
    expected_output: Optional[Any] = None
    expected_error: Optional[str] = None
    mock_deps: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


def create_mock_run_context(deps: Any, **overrides: Any) -> RunContext:
    """
    Factory that creates a real RunContext with sensible test defaults.

    Unlike subclassing RunContext (which breaks when upstream adds fields),
    this calls the dataclass constructor directly and stays compatible
    across pydantic-ai upgrades.
    """
    defaults: Dict[str, Any] = dict(
        deps=deps,
        model=MagicMock(),
        usage=RunUsage(),
        messages=[],
        tracer=MagicMock(),
        retries={},
        retry=0,
    )
    defaults.update(overrides)
    return RunContext(**defaults)


# Backward-compat alias so any external imports keep working
MockRunContext = create_mock_run_context


class TestResult(BaseModel):
    """
    Result of a skill test execution
    """
    test_case: SkillTest
    success: bool
    actual_output: Optional[Any] = None
    actual_error: Optional[str] = None
    message: str


async def run_skill_test(test_case: SkillTest) -> TestResult:
    """
    Run a single skill test case
    """
    print(f"Running test: {test_case.description} for skill {test_case.skill_name}...")
    
    # 1. Load skill
    registry = SkillRegistry()
    skill = registry.get_skill(test_case.skill_name)
    
    if not skill:
        return TestResult(
            test_case=test_case,
            success=False,
            message=f"Skill '{test_case.skill_name}' not found"
        )
    
    # 2. Prepare mock context
    mock_deps_data = test_case.mock_deps or {}
    deps = AgentDependencies(
        db=mock_deps_data.get('db'),
        user_id=mock_deps_data.get('user_id', 'test-user-id'),
        api_keys=mock_deps_data.get('api_keys', {}),
        connectors=mock_deps_data.get('connectors', {})
    )
    
    ctx = MockRunContext(deps=deps)
    
    # 3. Execute skill
    try:
        # Check if function is async
        if inspect.iscoroutinefunction(skill.handler):
            output = await skill.handler(ctx, **test_case.inputs)
        else:
            output = skill.handler(ctx, **test_case.inputs)
            
        # 4. Assertions
        if test_case.expected_error:
            return TestResult(
                test_case=test_case,
                success=False,
                actual_output=output,
                message=f"Expected error '{test_case.expected_error}' but got success"
            )
            
        if test_case.expected_output is not None:
            # Simple equality check for now
            # For complex objects we might need more sophisticated matching
            if output == test_case.expected_output:
                return TestResult(
                    test_case=test_case,
                    success=True,
                    actual_output=output,
                    message="Output matched expected result"
                )
            else:
                return TestResult(
                    test_case=test_case,
                    success=False,
                    actual_output=output,
                    message=f"Output mismatch. Expected {test_case.expected_output}, got {output}"
                )
                
        # If no expected output specified, just success is enough
        return TestResult(
            test_case=test_case,
            success=True,
            actual_output=output,
            message="Execution successful (no expectation checked)"
        )
        
    except Exception as e:
        error_msg = str(e)
        if test_case.expected_error:
            if test_case.expected_error in error_msg:
                return TestResult(
                    test_case=test_case,
                    success=True,
                    actual_error=error_msg,
                    message=f"Caught expected error: {error_msg}"
                )
            else:
                return TestResult(
                    test_case=test_case,
                    success=False,
                    actual_error=error_msg,
                    message=f"Error mismatch. Expected '{test_case.expected_error}', got '{error_msg}'"
                )
        
        return TestResult(
            test_case=test_case,
            success=False,
            actual_error=error_msg,
            message=f"Unexpected error: {error_msg}"
        )


async def run_suite(tests: List[SkillTest]) -> List[TestResult]:
    """
    Run a suite of tests
    """
    results = []
    print(f"Starting test suite with {len(tests)} tests...")
    
    for test in tests:
        result = await run_skill_test(test)
        results.append(result)
        
        status = "✅ PASS" if result.success else "❌ FAIL"
        print(f"{status}: {test.description} ({result.message})")
        
    return results
