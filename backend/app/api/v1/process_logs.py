"""Process log CSV export for process mining analysis.

Exports ComplianceAuditLog data as CSV with standard process mining columns
(case_id, activity, timestamp, resource, workflow_id) for use with tools
like Excel, Celonis, Disco, or ProM.
"""
import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import require_role, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.compliance_audit_log import ComplianceAuditLog

router = APIRouter()

CSV_COLUMNS = ["case_id", "activity", "timestamp", "resource", "workflow_id"]


@router.get("/export")
async def export_process_logs(
    date_from: datetime = Query(..., description="Start date (ISO format)"),
    date_to: datetime = Query(..., description="End date (ISO format)"),
    workflow_id: uuid.UUID | None = Query(None, description="Filter by workflow"),
    agent_id: str | None = Query(None, description="Filter by agent"),
    limit: int = Query(100_000, le=500_000, description="Max rows"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(require_role("owner", "admin")),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> StreamingResponse:
    """Export process logs as CSV for process mining.

    Uses session_id as case_id (unique per workflow execution).
    Rows with NULL session_id are excluded.
    """
    query = (
        select(ComplianceAuditLog)
        .where(
            and_(
                ComplianceAuditLog.tenant_id == tenant_id,
                ComplianceAuditLog.created_at >= date_from,
                ComplianceAuditLog.created_at <= date_to,
                ComplianceAuditLog.session_id.isnot(None),
            )
        )
        .order_by(ComplianceAuditLog.session_id, ComplianceAuditLog.created_at)
        .limit(limit)
    )

    if workflow_id is not None:
        query = query.where(ComplianceAuditLog.workflow_id == workflow_id)
    if agent_id is not None:
        query = query.where(ComplianceAuditLog.agent_id == agent_id)

    result = await db.execute(query)
    rows = result.scalars().all()

    async def generate_csv():
        # Header
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_COLUMNS)
        yield output.getvalue()

        # Data rows
        for row in rows:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                str(row.session_id),
                row.action,
                row.created_at.isoformat() if row.created_at else "",
                row.agent_id or "system",
                str(row.workflow_id) if row.workflow_id else "",
            ])
            yield output.getvalue()

    filename = f"process_logs_{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
