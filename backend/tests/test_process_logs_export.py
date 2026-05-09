"""Tests for process log CSV export endpoint."""
import csv
import io
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


class TestProcessLogExport:
    """Test GET /process-logs/export endpoint."""

    def _make_audit_log(self, session_id=None, workflow_id=None, action="agent_decision", agent_id="analysis-agent"):
        """Create a mock ComplianceAuditLog row."""
        log = MagicMock()
        log.session_id = session_id or uuid4()
        log.workflow_id = workflow_id or uuid4()
        log.action = action
        log.agent_id = agent_id
        log.created_at = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        return log

    @pytest.mark.asyncio
    async def test_csv_structure(self):
        """CSV output has correct header and row format."""
        from app.api.v1.process_logs import export_process_logs

        session_id = uuid4()
        workflow_id = uuid4()
        mock_logs = [
            self._make_audit_log(session_id=session_id, workflow_id=workflow_id, action="data_access"),
            self._make_audit_log(session_id=session_id, workflow_id=workflow_id, action="agent_decision"),
        ]

        # Mock DB
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_logs
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        tenant_id = uuid4()

        response = await export_process_logs(
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 2, 1, tzinfo=timezone.utc),
            workflow_id=None,
            agent_id=None,
            limit=100_000,
            db=mock_db,
            user=mock_user,
            tenant_id=tenant_id,
        )

        # Collect streaming response
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        content = "".join(chunks)

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # Header check
        assert rows[0] == ["case_id", "activity", "timestamp", "resource", "workflow_id"]

        # Data rows
        assert len(rows) == 3  # header + 2 data rows
        assert rows[1][0] == str(session_id)  # case_id = session_id
        assert rows[1][1] == "data_access"  # activity
        assert rows[1][3] == "analysis-agent"  # resource
        assert rows[1][4] == str(workflow_id)  # workflow_id
        assert rows[2][1] == "agent_decision"

    @pytest.mark.asyncio
    async def test_csv_content_disposition(self):
        """Response has correct Content-Disposition header."""
        from app.api.v1.process_logs import export_process_logs

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        response = await export_process_logs(
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 2, 1, tzinfo=timezone.utc),
            workflow_id=None,
            agent_id=None,
            limit=100_000,
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        assert response.media_type == "text/csv"
        assert "process_logs_20260101_20260201.csv" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Empty result returns header-only CSV."""
        from app.api.v1.process_logs import export_process_logs

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        response = await export_process_logs(
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 2, 1, tzinfo=timezone.utc),
            workflow_id=None,
            agent_id=None,
            limit=100_000,
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        content = "".join(chunks)

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) == 1  # header only
        assert rows[0] == ["case_id", "activity", "timestamp", "resource", "workflow_id"]

    @pytest.mark.asyncio
    async def test_system_resource_fallback(self):
        """Rows without agent_id use 'system' as resource."""
        from app.api.v1.process_logs import export_process_logs

        log = self._make_audit_log()
        log.agent_id = None

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [log]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        response = await export_process_logs(
            date_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2026, 2, 1, tzinfo=timezone.utc),
            workflow_id=None,
            agent_id=None,
            limit=100_000,
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        content = "".join(chunks)

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert rows[1][3] == "system"
