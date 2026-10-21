"""POST /schedules: an unknown workflow is a 404 and no raw DB error text reaches the client."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1 import schedules


class _Db:
    def __init__(self, found):
        self._found = found

    async def execute(self, *_a, **_k):
        return SimpleNamespace(scalar_one_or_none=lambda: self._found)


def _req():
    return schedules.ScheduleCreate(workflow_id=uuid.uuid4(), name="n", schedule_type="interval", interval_seconds=60)


async def test_unknown_workflow_is_404(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(schedules.WorkflowSchedulerService, "create_schedule", create)
    with pytest.raises(HTTPException) as e:
        await schedules.create_schedule(_req(), db=_Db(None), user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    assert e.value.status_code == 404
    create.assert_not_called()


async def test_unexpected_failure_does_not_leak_details(monkeypatch):
    monkeypatch.setattr(schedules.WorkflowSchedulerService, "create_schedule",
                        AsyncMock(side_effect=RuntimeError("ForeignKeyViolationError secret_table")))
    with pytest.raises(HTTPException) as e:
        await schedules.create_schedule(_req(), db=_Db(uuid.uuid4()), user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    assert e.value.status_code == 500 and "ForeignKey" not in e.value.detail
