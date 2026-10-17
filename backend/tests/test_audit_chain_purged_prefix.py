"""verify_chain starts at the first remaining sequence, so a retention-purged prefix is not a break."""
import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.compliance_audit_log import ComplianceAuditLog
from app.security.kms import LocalDevKMS
from app.services.audit_chain import AuditChain, compute_entry_hash, entry_bytes

TENANT = uuid.uuid4()


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        return _Result(self.rows)


def build_chain(kms, n):
    rows, prev = [], None
    for seq in range(1, n + 1):
        row = ComplianceAuditLog(
            id=uuid.uuid4(), tenant_id=TENANT, agent_id="a", action="agent_decision", decision="d",
            rationale="r", data_sources=[], tools_called=[], extra_metadata={}, seq=seq, previous_hash=prev,
            signing_key_id=kms.key_id, retention_until=datetime.now(timezone.utc) + timedelta(days=1),
            created_at=datetime.now(timezone.utc))
        row.entry_hash = compute_entry_hash(row)
        row.signature = base64.b64encode(kms.sign(entry_bytes(row))).decode()
        rows.append(row)
        prev = row.entry_hash
    return rows


@pytest.fixture
def kms(tmp_path):
    return LocalDevKMS(tmp_path)


async def verify(kms, rows):
    return await AuditChain(FakeDb(rows), kms=kms).verify_chain(TENANT)


async def test_full_chain_is_ok(kms):
    v = await verify(kms, build_chain(kms, 4))
    assert v["ok"] and v["checked"] == 4


async def test_purged_prefix_is_not_a_break(kms):
    rows = build_chain(kms, 5)[2:]  # seq 1-2 purged by retention
    v = await verify(kms, rows)
    assert v == {"ok": True, "checked": 3, "legacy_unchained": 0, "first_break": None}


async def test_gap_after_the_purged_prefix_is_still_a_break(kms):
    rows = build_chain(kms, 6)[2:]
    del rows[1]  # seq 4 removed from the middle
    v = await verify(kms, rows)
    assert v["ok"] is False and v["first_break"] == {"seq": 5, "reason": "missing_entry"}


async def test_tampering_after_the_purged_prefix_is_detected(kms):
    rows = build_chain(kms, 5)[2:]
    rows[1].rationale = "edited"
    v = await verify(kms, rows)
    assert v["ok"] is False and v["first_break"]["seq"] == 4
