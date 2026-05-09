"""
Tests for SkillAuditService and tenant skill isolation.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from app.services.skill_audit import SkillAuditService


class TestSkillAuditServiceUnit:
    """Unit tests for SkillAuditService (no database)."""

    def test_compute_hash_none(self):
        """Test hash computation returns None for None input."""
        result = SkillAuditService._compute_hash(None)
        assert result is None

    def test_compute_hash_dict(self):
        """Test hash computation for dict."""
        data = {"key": "value", "number": 42}
        result = SkillAuditService._compute_hash(data)

        assert result is not None
        assert len(result) == 64  # SHA-256 hex digest length
        assert result.isalnum()

    def test_compute_hash_deterministic(self):
        """Test hash is deterministic for same input."""
        data = {"key": "value", "nested": {"a": 1, "b": 2}}

        hash1 = SkillAuditService._compute_hash(data)
        hash2 = SkillAuditService._compute_hash(data)

        assert hash1 == hash2

    def test_compute_hash_different_for_different_input(self):
        """Test hash differs for different input."""
        data1 = {"key": "value1"}
        data2 = {"key": "value2"}

        hash1 = SkillAuditService._compute_hash(data1)
        hash2 = SkillAuditService._compute_hash(data2)

        assert hash1 != hash2

    def test_compute_hash_key_order_independent(self):
        """Test hash is independent of key order."""
        data1 = {"a": 1, "b": 2, "c": 3}
        data2 = {"c": 3, "a": 1, "b": 2}

        hash1 = SkillAuditService._compute_hash(data1)
        hash2 = SkillAuditService._compute_hash(data2)

        assert hash1 == hash2

    def test_compute_hash_handles_datetime(self):
        """Test hash handles datetime objects."""
        data = {"timestamp": datetime.now(timezone.utc)}
        result = SkillAuditService._compute_hash(data)

        assert result is not None
        assert len(result) == 64


# Database integration tests would go here, using a real test database
# These are skipped if no database is available

@pytest.fixture
def mock_db_session():
    """Mock database session for unit testing."""
    class MockSession:
        def __init__(self):
            self.added = []
            self.committed = False
            self.refreshed = []

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.committed = True

        async def refresh(self, obj):
            self.refreshed.append(obj)

        async def execute(self, query):
            class MockResult:
                def scalars(self):
                    class Scalars:
                        def all(self):
                            return []
                        def first(self):
                            return None
                    return Scalars()
                def scalar(self):
                    return 0
                def scalar_one_or_none(self):
                    return None
            return MockResult()

    return MockSession()


class TestSkillAuditServiceWithMock:
    """Tests using mock database session."""

    @pytest.mark.asyncio
    async def test_log_execution_creates_record(self, mock_db_session):
        """Test log_execution creates a SkillExecution record."""
        service = SkillAuditService(mock_db_session)

        await service.log_execution(
            skill_name="test_skill",
            tenant_id=uuid4(),
            status="success",
            inputs={"query": "test"},
            outputs={"result": "data"},
            duration_ms=100,
        )

        assert mock_db_session.committed
        assert len(mock_db_session.added) == 1

        execution = mock_db_session.added[0]
        assert execution.skill_name == "test_skill"
        assert execution.status == "success"
        assert execution.duration_ms == 100
        assert execution.input_hash is not None
        assert execution.output_hash is not None

    @pytest.mark.asyncio
    async def test_log_execution_handles_string_uuids(self, mock_db_session):
        """Test log_execution handles string UUIDs correctly."""
        service = SkillAuditService(mock_db_session)

        tenant_id = str(uuid4())
        user_id = str(uuid4())

        await service.log_execution(
            skill_name="test_skill",
            tenant_id=tenant_id,
            user_id=user_id,
            status="success",
        )

        assert mock_db_session.committed
        execution = mock_db_session.added[0]
        # UUIDs should be converted
        assert execution.tenant_id is not None
        assert execution.user_id is not None

    @pytest.mark.asyncio
    async def test_log_execution_failed_status(self, mock_db_session):
        """Test log_execution with failed status includes error info."""
        service = SkillAuditService(mock_db_session)

        await service.log_execution(
            skill_name="test_skill",
            tenant_id=uuid4(),
            status="failed",
            error_message="Something went wrong",
            error_type="ValueError",
            duration_ms=50,
        )

        execution = mock_db_session.added[0]
        assert execution.status == "failed"
        assert execution.error_message == "Something went wrong"
        assert execution.error_type == "ValueError"
        assert execution.completed_at is not None

    @pytest.mark.asyncio
    async def test_get_executions_returns_empty_list(self, mock_db_session):
        """Test get_executions returns empty list when no executions."""
        service = SkillAuditService(mock_db_session)

        result = await service.get_executions(uuid4())

        assert result == []

    @pytest.mark.asyncio
    async def test_get_execution_by_id_returns_none(self, mock_db_session):
        """Test get_execution_by_id returns None when not found."""
        service = SkillAuditService(mock_db_session)

        result = await service.get_execution_by_id(uuid4(), uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_verify_integrity_not_found(self, mock_db_session):
        """Test verify_integrity when execution not found."""
        service = SkillAuditService(mock_db_session)

        result = await service.verify_integrity(uuid4(), uuid4())

        assert result["verified"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_get_execution_stats_empty(self, mock_db_session):
        """Test get_execution_stats with no executions."""
        service = SkillAuditService(mock_db_session)
        tenant_id = uuid4()

        result = await service.get_execution_stats(tenant_id)

        assert result["tenant_id"] == str(tenant_id)
        assert result["total_executions"] == 0
        assert result["successful"] == 0
        assert result["failed"] == 0
        assert result["success_rate"] == 0.0


class TestTenantIsolation:
    """Tests for tenant isolation in skill queries."""

    def test_tenant_skill_service_initialization(self):
        """Test TenantSkillService can be initialized."""
        from app.services.tenant_skills import TenantSkillService

        # String UUID
        service1 = TenantSkillService(None, str(uuid4()))
        assert service1.tenant_id is not None

        # UUID object
        tenant_id = uuid4()
        service2 = TenantSkillService(None, tenant_id)
        assert service2.tenant_id == tenant_id
