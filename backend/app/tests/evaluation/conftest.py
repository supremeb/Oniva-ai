"""
Evaluation Framework Test Fixtures

Part of Phase 1: Production Reliability.

Provides pytest fixtures for loading test cases and running evaluations.
"""

import os
import yaml
import pytest
from pathlib import Path
from typing import List, Dict, Any, AsyncGenerator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import get_settings
from app.tests.evaluation.evaluators.deterministic import DeterministicEvaluator
from app.tests.evaluation.evaluators.llm_judge import LLMJudgeEvaluator

settings = get_settings()

# Fixture directory path
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class EvalTestCase:
    """Represents a single evaluation test case."""
    id: str
    description: str
    input: str
    expected: Dict[str, Any]
    evaluation_method: str
    agent_type: str
    category: str


def load_yaml_fixtures(filename: str) -> List[EvalTestCase]:
    """
    Load test cases from a YAML fixture file.

    Args:
        filename: Name of the YAML file (e.g., "compliance.yaml")

    Returns:
        List of EvalTestCase objects
    """
    filepath = FIXTURES_DIR / filename

    if not filepath.exists():
        return []

    with open(filepath, 'r') as f:
        data = yaml.safe_load(f)

    if not data:
        return []

    test_cases = []
    for item in data:
        test_cases.append(EvalTestCase(
            id=item.get("id", "unknown"),
            description=item.get("description", ""),
            input=item.get("input", ""),
            expected=item.get("expected", {}),
            evaluation_method=item.get("evaluation_method", "deterministic"),
            agent_type=item.get("agent_type", "unknown"),
            category=item.get("category", "general"),
        ))

    return test_cases


def load_all_fixtures() -> Dict[str, List[EvalTestCase]]:
    """
    Load all test case fixtures.

    Returns:
        Dictionary mapping agent type to list of test cases
    """
    fixtures = {
        "compliance": load_yaml_fixtures("compliance.yaml"),
        "data_retrieval": load_yaml_fixtures("data_retrieval.yaml"),
        "analysis": load_yaml_fixtures("analysis.yaml"),
        "research": load_yaml_fixtures("research.yaml"),
    }
    return fixtures


# ============================================
# Pytest Fixtures
# ============================================

@pytest.fixture
def compliance_test_cases() -> List[EvalTestCase]:
    """Load compliance agent test cases."""
    return load_yaml_fixtures("compliance.yaml")


@pytest.fixture
def data_retrieval_test_cases() -> List[EvalTestCase]:
    """Load data retrieval agent test cases."""
    return load_yaml_fixtures("data_retrieval.yaml")


@pytest.fixture
def analysis_test_cases() -> List[EvalTestCase]:
    """Load analysis agent test cases."""
    return load_yaml_fixtures("analysis.yaml")


@pytest.fixture
def research_test_cases() -> List[EvalTestCase]:
    """Load research agent test cases."""
    return load_yaml_fixtures("research.yaml")


@pytest.fixture
def all_test_cases() -> Dict[str, List[EvalTestCase]]:
    """Load all test cases grouped by agent type."""
    return load_all_fixtures()


@pytest.fixture
def deterministic_evaluator() -> DeterministicEvaluator:
    """Create a deterministic evaluator instance."""
    return DeterministicEvaluator()


@pytest.fixture
def llm_judge_evaluator() -> LLMJudgeEvaluator:
    """
    Create an LLM judge evaluator instance.

    Note: Requires OPENAI_API_KEY to be set for full functionality.
    Falls back to returning empty results if not available.
    """
    return LLMJudgeEvaluator()


@pytest.fixture
def test_db_url() -> str:
    """
    Get test database URL.

    Uses TEST_DATABASE_URL if set, otherwise modifies the main URL.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        return test_url

    # Modify main URL to use test database
    main_url = settings.DATABASE_URL
    if "fiagents" in main_url:
        return main_url.replace("fiagents", "fiagents_test")
    return main_url + "_test"


@pytest.fixture
async def async_db_session(test_db_url: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Create an async database session for testing.

    Note: This creates a new connection for each test.
    Consider using a transaction-based approach for better isolation.
    """
    engine = create_async_engine(
        test_db_url,
        echo=False,
        pool_pre_ping=True,
    )

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


# ============================================
# Evaluation Helpers
# ============================================

def filter_test_cases(
    test_cases: List[EvalTestCase],
    agent_type: str = None,
    category: str = None,
    evaluation_method: str = None,
) -> List[EvalTestCase]:
    """
    Filter test cases by various criteria.

    Args:
        test_cases: List of test cases to filter
        agent_type: Filter by agent type
        category: Filter by category
        evaluation_method: Filter by evaluation method

    Returns:
        Filtered list of test cases
    """
    filtered = test_cases

    if agent_type:
        filtered = [tc for tc in filtered if tc.agent_type == agent_type]

    if category:
        filtered = [tc for tc in filtered if tc.category == category]

    if evaluation_method:
        filtered = [tc for tc in filtered if tc.evaluation_method == evaluation_method]

    return filtered


def get_test_summary(test_cases: List[EvalTestCase]) -> Dict[str, Any]:
    """
    Get a summary of test cases.

    Returns:
        Summary statistics about the test cases
    """
    by_agent = {}
    by_category = {}
    by_method = {}

    for tc in test_cases:
        by_agent[tc.agent_type] = by_agent.get(tc.agent_type, 0) + 1
        by_category[tc.category] = by_category.get(tc.category, 0) + 1
        by_method[tc.evaluation_method] = by_method.get(tc.evaluation_method, 0) + 1

    return {
        "total": len(test_cases),
        "by_agent": by_agent,
        "by_category": by_category,
        "by_evaluation_method": by_method,
    }


# ============================================
# Mock Agent Fixtures (for testing without real agents)
# ============================================

@pytest.fixture
def mock_compliance_agent():
    """
    Mock compliance agent for testing evaluation logic.

    Returns predictable outputs for testing the evaluation framework itself.
    """
    class MockComplianceAgent:
        async def run(self, input_text: str) -> Dict[str, Any]:
            # Return mock responses based on keywords
            input_lower = input_text.lower()

            if "iran" in input_lower or "north korea" in input_lower:
                return {
                    "result": "FAIL",
                    "risk_level": "CRITICAL",
                    "rationale": "Transaction involves sanctioned country",
                }
            elif "john smith" in input_lower or "microsoft" in input_lower:
                return {
                    "result": "PASS",
                    "risk_level": "LOW",
                    "rationale": "No match found in OFAC SDN list",
                }
            else:
                return {
                    "result": "REVIEW",
                    "risk_level": "MEDIUM",
                    "rationale": "Partial match found, manual review required",
                }

    return MockComplianceAgent()


@pytest.fixture
def mock_data_retrieval_agent():
    """Mock data retrieval agent for testing."""
    class MockDataRetrievalAgent:
        async def run(self, input_text: str) -> Dict[str, Any]:
            input_lower = input_text.lower()

            if "aapl" in input_lower or "apple" in input_lower:
                return {
                    "symbol": "AAPL",
                    "has_price": True,
                    "price": 175.50,
                    "volume": 50000000,
                }
            elif "invalid" in input_lower:
                return {
                    "error": True,
                    "error_type": "Symbol not found",
                }
            else:
                return {
                    "symbol": "UNKNOWN",
                    "has_price": True,
                    "price": 100.00,
                }

    return MockDataRetrievalAgent()


@pytest.fixture
def mock_analysis_agent():
    """Mock analysis agent for testing."""
    class MockAnalysisAgent:
        async def run(self, input_text: str) -> Dict[str, Any]:
            input_lower = input_text.lower()

            if "p/e" in input_lower or "pe ratio" in input_lower:
                # Extract numbers from input for calculation
                return {
                    "ratio_name": "P/E",
                    "value": 30,
                    "interpretation": "Above market average",
                }
            elif "rsi" in input_lower:
                return {
                    "indicator": "RSI",
                    "period": 14,
                    "value": 65,
                }
            else:
                return {
                    "analysis_type": "general",
                    "recommendation": "hold",
                }

    return MockAnalysisAgent()


@pytest.fixture
def mock_research_agent():
    """Mock research agent for testing."""
    class MockResearchAgent:
        async def run(self, input_text: str) -> Dict[str, Any]:
            input_lower = input_text.lower()

            if "10-k" in input_lower:
                return {
                    "document_type": "10-K",
                    "company": "Apple" if "apple" in input_lower else "Unknown",
                    "has_filing_date": True,
                    "has_content_summary": True,
                }
            elif "xyz123" in input_lower:
                return {
                    "error": True,
                    "error_type": "Company not found",
                }
            else:
                return {
                    "has_answer": True,
                    "source_cited": True,
                }

    return MockResearchAgent()


# ============================================
# Accuracy Threshold Configuration
# ============================================

@pytest.fixture
def accuracy_thresholds() -> Dict[str, float]:
    """
    Get accuracy thresholds for different agent types.

    These are the minimum acceptable accuracy levels for CI/CD gates.
    """
    return {
        "compliance": 0.95,      # 95% - High bar for compliance
        "data_retrieval": 0.90,  # 90% - Data accuracy critical
        "analysis": 0.85,        # 85% - Some subjectivity allowed
        "research": 0.80,        # 80% - Most subjective
        "overall": 0.90,         # 90% - Overall system accuracy
    }


# ============================================
# Skip Markers
# ============================================

requires_openai = pytest.mark.skipif(
    not settings.OPENAI_API_KEY,
    reason="OPENAI_API_KEY not set"
)

requires_db = pytest.mark.skipif(
    not settings.DATABASE_URL or "test" not in settings.DATABASE_URL.lower(),
    reason="Test database not configured"
)

slow_test = pytest.mark.slow
