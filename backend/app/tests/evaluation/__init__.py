"""
Agent Evaluation Framework

Part of Phase 1: Production Reliability.

Provides systematic evaluation of agent accuracy:
- Deterministic evaluation (schema validation, math checks)
- LLM-as-judge evaluation for qualitative accuracy
- Test case fixtures for all agent types
- Accuracy reporting and CI/CD gates

Usage:
    from app.tests.evaluation import (
        DeterministicEvaluator,
        LLMJudgeEvaluator,
        AccuracyTestRunner,
        load_all_fixtures,
    )

    # Run evaluation
    runner = AccuracyTestRunner()
    report = await runner.run_full_evaluation(agents)
"""

from app.tests.evaluation.evaluators import (
    DeterministicEvaluator,
    LLMJudgeEvaluator,
)
from app.tests.evaluation.conftest import (
    EvalTestCase,
    load_all_fixtures,
    load_yaml_fixtures,
    filter_test_cases,
    get_test_summary,
)
from app.tests.evaluation.test_accuracy import (
    AccuracyTestRunner,
    AgentAccuracyReport,
    FullAccuracyReport,
    generate_accuracy_report_markdown,
    generate_accuracy_report_json,
)

__all__ = [
    # Evaluators
    "DeterministicEvaluator",
    "LLMJudgeEvaluator",
    # Test Runner
    "AccuracyTestRunner",
    "AgentAccuracyReport",
    "FullAccuracyReport",
    # Fixtures
    "EvalTestCase",
    "load_all_fixtures",
    "load_yaml_fixtures",
    "filter_test_cases",
    "get_test_summary",
    # Report Generation
    "generate_accuracy_report_markdown",
    "generate_accuracy_report_json",
]
