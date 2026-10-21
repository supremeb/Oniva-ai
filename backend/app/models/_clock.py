"""Timezone-aware UTC clock for ORM column defaults.

A naive ``datetime.utcnow()`` bound to a ``timestamptz`` column is interpreted in the database
session's time zone, which skews stored values by the server's UTC offset. Use ``utc_now`` for
every ``default=`` / ``onupdate=`` on a ``DateTime(timezone=True)`` column.
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
