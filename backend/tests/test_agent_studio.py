"""Unit tests for Agent Studio endpoints.

Tests for chat sessions, test cases, evaluation, and deploy gates.
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from app.models.agent_chat_session import AgentChatSession
from app.models.agent_test_case import AgentTestCase
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.deploy_history import DeployHistory
from app.services.agent_evaluator import AgentEvaluator, EvaluationResult


# ============================================================================
# Model Tests
# ============================================================================

class TestAgentChatSession:
    """Tests for AgentChatSession model."""

    def test_add_message(self):
        """Test adding a message to the session."""
        session = AgentChatSession(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Session",
            messages=[],
        )

        session.add_message(
            message_id="msg-1",
            role="user",
            content="Hello, agent!",
        )

        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "Hello, agent!"
        assert session.total_messages == 1

    def test_add_assistant_message_with_metrics(self):
        """Test adding assistant message with tokens and latency."""
        session = AgentChatSession(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Session",
            messages=[],
        )

        session.add_message(
            message_id="msg-1",
            role="assistant",
            content="Hello! How can I help?",
            tokens_used=50,
            latency_ms=150,
        )

        assert session.total_tokens_used == 50
        assert session.total_latency_ms == 150
        assert session.messages[0]["tokens_used"] == 50
        assert session.messages[0]["latency_ms"] == 150

    def test_set_message_feedback(self):
        """Test setting feedback on a message."""
        session = AgentChatSession(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Session",
            messages=[],
            positive_feedback_count=0,
            negative_feedback_count=0,
        )

        session.add_message("msg-1", "user", "Test input")
        session.add_message("msg-2", "assistant", "Test response")

        session.set_message_feedback("msg-2", "positive", "tc-123")

        assert session.messages[1]["feedback"] == "positive"
        assert session.messages[1]["test_case_id"] == "tc-123"
        assert session.positive_feedback_count == 1
        assert session.negative_feedback_count == 0

    def test_get_message_by_id(self):
        """Test retrieving a message by ID."""
        session = AgentChatSession(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Session",
            messages=[],
        )

        session.add_message("msg-1", "user", "First message")
        session.add_message("msg-2", "assistant", "Second message")

        msg = session.get_message_by_id("msg-2")
        assert msg is not None
        assert msg["content"] == "Second message"

        # Non-existent message
        assert session.get_message_by_id("msg-999") is None

    def test_get_previous_message(self):
        """Test getting the message before a given message."""
        session = AgentChatSession(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Session",
            messages=[],
        )

        session.add_message("msg-1", "user", "User input")
        session.add_message("msg-2", "assistant", "Agent response")

        prev = session.get_previous_message("msg-2")
        assert prev is not None
        assert prev["id"] == "msg-1"
        assert prev["content"] == "User input"


class TestAgentTestCase:
    """Tests for AgentTestCase model."""

    def test_record_result_pass(self):
        """Test recording a passing result."""
        test_case = AgentTestCase(
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Case 1",
            input="What is 2+2?",
            expected_output="4",
            evaluation_type="exact",
            source="manual",
            pass_count=0,
            fail_count=0,
        )

        test_case.record_result(passed=True, actual_output="4", score=1.0)

        assert test_case.pass_count == 1
        assert test_case.fail_count == 0
        assert test_case.last_result["passed"] is True
        assert test_case.last_result["actual_output"] == "4"

    def test_record_result_fail(self):
        """Test recording a failing result."""
        test_case = AgentTestCase(
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Case 1",
            input="What is 2+2?",
            expected_output="4",
            evaluation_type="exact",
            source="manual",
            pass_count=0,
            fail_count=0,
        )

        test_case.record_result(passed=False, actual_output="5", score=0.0)

        assert test_case.pass_count == 0
        assert test_case.fail_count == 1
        assert test_case.last_result["passed"] is False

    def test_accuracy_property(self):
        """Test accuracy calculation."""
        test_case = AgentTestCase(
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            name="Test Case 1",
            input="Test",
            evaluation_type="contains",
            source="manual",
        )

        # No runs yet
        assert test_case.accuracy is None

        # Record some results
        test_case.record_result(True, "ok", 1.0)
        test_case.record_result(True, "ok", 1.0)
        test_case.record_result(False, "fail", 0.0)

        assert test_case.accuracy == pytest.approx(0.6667, rel=0.01)

    def test_generate_name_from_input(self):
        """Test auto-generating test case names."""
        name = AgentTestCase.generate_name_from_input("What is the stock price of AAPL today?")
        # The method truncates to 50 chars, so this won't be truncated
        assert name == "What is the stock price of AAPL today?"

        # Test with a longer input that gets truncated
        long_input = "This is a very long input that definitely exceeds the fifty character limit for names"
        truncated_name = AgentTestCase.generate_name_from_input(long_input)
        # The model truncates at exactly 50 chars without space
        assert truncated_name == "This is a very long input that definitely exceeds..."

        short_name = AgentTestCase.generate_name_from_input("Short")
        assert short_name == "Short"


class TestAgentAccuracyHistory:
    """Tests for AgentAccuracyHistory model."""

    def test_create_from_results(self):
        """Test creating accuracy entry from test results."""
        tenant_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        version_id = uuid.uuid4()
        run_id = uuid.uuid4()

        results = [
            {"passed": True, "category": "math"},
            {"passed": True, "category": "math"},
            {"passed": False, "category": "language"},
            {"passed": True, "category": "language"},
        ]

        entry = AgentAccuracyHistory.create_from_results(
            tenant_id=tenant_id,
            agent_id=agent_id,
            test_results=results,
            agent_version_id=version_id,
            previous_accuracy=0.5,
            duration_ms=1500,
            evaluation_run_id=run_id,
        )

        assert entry.accuracy == 0.75  # 3/4 passed
        assert entry.total_tests == 4
        assert entry.passed_tests == 3
        assert entry.failed_tests == 1
        assert entry.accuracy_delta == 0.25  # 0.75 - 0.5
        assert entry.duration_ms == 1500

        # Check category breakdown
        assert "math" in entry.category_breakdown
        assert entry.category_breakdown["math"]["passed"] == 2
        assert entry.category_breakdown["math"]["accuracy"] == 1.0

        assert "language" in entry.category_breakdown
        assert entry.category_breakdown["language"]["passed"] == 1
        assert entry.category_breakdown["language"]["accuracy"] == 0.5


class TestDeployHistory:
    """Tests for DeployHistory model."""

    def test_create_deploy_record(self):
        """Test creating a deploy record."""
        tenant_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        version_id = uuid.uuid4()
        user_id = uuid.uuid4()

        record = DeployHistory.create_deploy_record(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            deployed_by=user_id,
            gate_check_passed=True,
            accuracy_at_deploy=0.85,
            previous_version_id=None,
            previous_accuracy=None,
            notes="Initial deployment",
        )

        assert record.action == "deploy"
        assert record.status == "success"
        assert record.gate_check_passed is True
        assert record.accuracy_at_deploy == 0.85
        assert record.notes == "Initial deployment"

    def test_create_block_record(self):
        """Test creating a blocked deploy record."""
        tenant_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        version_id = uuid.uuid4()
        user_id = uuid.uuid4()

        record = DeployHistory.create_block_record(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            deployed_by=user_id,
            block_reason="Accuracy below threshold",
            accuracy_at_deploy=0.65,
            previous_accuracy=0.80,
        )

        assert record.action == "block"  # The model uses 'block' not 'deploy'
        assert record.status == "blocked"
        assert record.gate_check_passed is False
        assert record.block_reason == "Accuracy below threshold"

    def test_create_rollback_record(self):
        """Test creating a rollback record."""
        tenant_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        target_version_id = uuid.uuid4()
        rolled_back_from_id = uuid.uuid4()
        user_id = uuid.uuid4()

        record = DeployHistory.create_rollback_record(
            tenant_id=tenant_id,
            agent_id=agent_id,
            target_version_id=target_version_id,
            rolled_back_from_id=rolled_back_from_id,
            deployed_by=user_id,
            reason="Performance regression detected",
        )

        assert record.action == "rollback"
        assert record.status == "rolled_back"  # The model uses 'rolled_back' not 'success'
        assert record.agent_version_id == target_version_id
        assert record.previous_version_id == rolled_back_from_id


# ============================================================================
# Service Tests
# ============================================================================

class TestAgentEvaluator:
    """Tests for AgentEvaluator service."""

    @pytest.mark.asyncio
    async def test_evaluation_result_to_dict(self):
        """Test EvaluationResult serialization."""
        result = EvaluationResult(
            test_case_id=uuid.uuid4(),
            passed=True,
            actual_output="The answer is 42",
            expected_output="42",
            score=0.85,
            evaluation_type="llm_judge",
            duration_ms=250,
            tokens_used=100,
            category="math",
        )

        d = result.to_dict()

        assert d["passed"] is True
        assert d["actual_output"] == "The answer is 42"
        assert d["expected_output"] == "42"
        assert d["score"] == 0.85
        assert d["evaluation_type"] == "llm_judge"
        assert d["duration_ms"] == 250
        assert d["category"] == "math"

    @pytest.mark.asyncio
    async def test_evaluate_test_case_exact_match(self):
        """Test exact match evaluation."""
        mock_db = MagicMock()
        evaluator = AgentEvaluator(mock_db)

        # Create mock test case and agent
        test_case = MagicMock()
        test_case.id = uuid.uuid4()
        test_case.input = "What is 2+2?"
        test_case.expected_output = "4"
        test_case.evaluation_type = "exact"
        test_case.context = None
        test_case.category = "math"
        test_case.record_result = MagicMock()

        agent = MagicMock()
        agent.config = {"model": "test"}

        # Mock _run_agent to return exact match
        with patch.object(evaluator, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "4"

            result = await evaluator.evaluate_test_case(test_case, agent)

            assert result.passed is True
            assert result.score == 1.0
            assert result.actual_output == "4"
            test_case.record_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_test_case_contains(self):
        """Test contains evaluation."""
        mock_db = MagicMock()
        evaluator = AgentEvaluator(mock_db)

        test_case = MagicMock()
        test_case.id = uuid.uuid4()
        test_case.input = "Tell me about stocks"
        test_case.expected_output = "AAPL"
        test_case.evaluation_type = "contains"
        test_case.context = None
        test_case.category = None
        test_case.record_result = MagicMock()

        agent = MagicMock()
        agent.config = {"model": "test"}

        with patch.object(evaluator, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "The top stock is AAPL with strong performance."

            result = await evaluator.evaluate_test_case(test_case, agent)

            assert result.passed is True
            assert result.score == 1.0


# ============================================================================
# Schema Tests
# ============================================================================

class TestSchemas:
    """Tests for Pydantic schemas."""

    def test_chat_message_schema(self):
        """Test ChatMessage schema validation."""
        from app.schemas.agent_studio import ChatMessage

        msg = ChatMessage(
            id=uuid.uuid4(),
            role="user",
            content="Hello",
            timestamp=datetime.utcnow(),
        )

        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.feedback is None

    def test_create_test_case_request(self):
        """Test CreateTestCaseRequest schema."""
        from app.schemas.agent_studio import CreateTestCaseRequest

        request = CreateTestCaseRequest(
            name="Test Math",
            input="What is 2+2?",
            expected_output="4",
            evaluation_type="exact",
            is_positive=True,
        )

        assert request.name == "Test Math"
        assert request.evaluation_type == "exact"
        assert request.is_baseline is False  # default

    def test_deploy_gate_check(self):
        """Test DeployGateCheck schema."""
        from app.schemas.agent_studio import DeployGateCheck

        check = DeployGateCheck(
            name="Accuracy Threshold",
            passed=True,
            value="85%",
            threshold="80%",
            message="Meets accuracy threshold",
        )

        assert check.passed is True
        assert check.name == "Accuracy Threshold"

    def test_deploy_gate_status(self):
        """Test DeployGateStatus schema."""
        from app.schemas.agent_studio import DeployGateStatus, DeployGateCheck

        checks = [
            DeployGateCheck(
                name="Evaluation Complete",
                passed=True,
                value="Yes",
                threshold=None,
                message="Evaluation completed",
            ),
            DeployGateCheck(
                name="Accuracy Threshold",
                passed=True,
                value="85%",
                threshold="80%",
                message="Meets threshold",
            ),
        ]

        status = DeployGateStatus(
            can_deploy=True,
            checks=checks,
            current_accuracy=0.80,
            new_accuracy=0.85,
            accuracy_delta=0.05,
            current_version="1.0.0",
            new_version="1.1.0",
        )

        assert status.can_deploy is True
        assert len(status.checks) == 2
        assert status.accuracy_delta == 0.05

    def test_evaluation_result_schema(self):
        """Test EvaluationResult schema."""
        from app.schemas.agent_studio import EvaluationResult

        result = EvaluationResult(
            evaluation_run_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            version_id=None,
            accuracy=0.85,
            total_tests=20,
            passed_tests=17,
            failed_tests=3,
            duration_ms=5000,
            category_breakdown={},
            test_results=[],
            evaluated_at=datetime.utcnow(),
        )

        assert result.accuracy == 0.85
        assert result.total_tests == 20


# ============================================================================
# Integration Tests (require database with aiosqlite)
# These tests are skipped when aiosqlite is not installed
# ============================================================================

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False


@pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed")
@pytest.mark.asyncio
async def test_agent_chat_session_crud(test_db):
    """Test AgentChatSession CRUD operations."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    # Create session
    session = AgentChatSession(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        name="Integration Test Session",
        messages=[],
    )

    test_db.add(session)
    await test_db.commit()
    await test_db.refresh(session)

    assert session.id is not None
    assert session.status == "active"

    # Add messages
    session.add_message("msg-1", "user", "Test message")
    session.add_message("msg-2", "assistant", "Test response", tokens_used=10, latency_ms=100)
    await test_db.commit()

    assert session.total_messages == 2
    assert session.total_tokens_used == 10


@pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed")
@pytest.mark.asyncio
async def test_agent_test_case_crud(test_db):
    """Test AgentTestCase CRUD operations."""
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    # Create test case
    test_case = AgentTestCase(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name="Math Test",
        input="What is 5 + 5?",
        expected_output="10",
        evaluation_type="exact",
        source="manual",
        category="arithmetic",
        is_baseline=True,
    )

    test_db.add(test_case)
    await test_db.commit()
    await test_db.refresh(test_case)

    assert test_case.id is not None
    assert test_case.is_active is True

    # Record results
    test_case.record_result(True, "10", 1.0)
    test_case.record_result(False, "11", 0.0)
    await test_db.commit()

    assert test_case.pass_count == 1
    assert test_case.fail_count == 1
    assert test_case.accuracy == 0.5


@pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed")
@pytest.mark.asyncio
async def test_agent_accuracy_history_crud(test_db):
    """Test AgentAccuracyHistory CRUD operations."""
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run_id = uuid.uuid4()

    results = [
        {"passed": True, "category": "math"},
        {"passed": True, "category": "math"},
        {"passed": False, "category": "logic"},
    ]

    entry = AgentAccuracyHistory.create_from_results(
        tenant_id=tenant_id,
        agent_id=agent_id,
        test_results=results,
        duration_ms=1000,
        evaluation_run_id=run_id,
    )

    test_db.add(entry)
    await test_db.commit()
    await test_db.refresh(entry)

    assert entry.id is not None
    assert entry.accuracy == pytest.approx(0.6667, rel=0.01)
    assert entry.total_tests == 3


@pytest.mark.skipif(not HAS_AIOSQLITE, reason="aiosqlite not installed")
@pytest.mark.asyncio
async def test_deploy_history_crud(test_db):
    """Test DeployHistory CRUD operations."""
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    version_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Create deploy record
    record = DeployHistory.create_deploy_record(
        tenant_id=tenant_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        deployed_by=user_id,
        gate_check_passed=True,
        accuracy_at_deploy=0.90,
    )

    test_db.add(record)
    await test_db.commit()
    await test_db.refresh(record)

    assert record.id is not None
    assert record.action == "deploy"
    assert record.status == "success"
