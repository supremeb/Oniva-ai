"""Guard: PG UUID columns on the SQLite test DB must keep text affinity.

A bare ``UUID`` column type gets NUMERIC affinity on SQLite, so a uuid4 whose 32 hex chars are all digits
(or digits plus one ``e``) is stored as an INTEGER/REAL and reloads as a number, which then fails inside
``uuid.UUID(...)`` ("'float' object has no attribute 'replace'"). Rare per id, but it shows up as a flaky
fixture error once a run mints enough ids. conftest renders UUID as CHAR(32) on SQLite to prevent it.
"""
import uuid

from sqlalchemy import select

from app.models.tenant import Tenant

# Hex strings SQLite would coerce to numbers under NUMERIC affinity.
ALL_DIGITS = uuid.UUID("12345678901234567890123456789012")
DIGITS_AND_E = uuid.UUID("1234567890123456789012345678e901")


async def test_numeric_looking_uuids_round_trip(test_db):
    for tid in (ALL_DIGITS, DIGITS_AND_E):
        test_db.add(Tenant(id=tid, name="t", slug=f"s-{tid.hex}", settings={}))
    await test_db.commit()
    test_db.expunge_all()
    got = {t.id for t in (await test_db.execute(select(Tenant))).scalars()}
    assert got == {ALL_DIGITS, DIGITS_AND_E}
