"""Fixture recorder/comparator for golden event-contract tests (U12).

Fixtures capture what the backend emits TODAY, quirks included (KTD5). They are
normalized so they are deterministic: ids -> stable placeholders, timestamps and
durations -> sentinels. Set ONIVA_RECORD_CONTRACTS=1 to (re)write fixtures.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RECORD_ENV = "ONIVA_RECORD_CONTRACTS"

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_TC_RE = re.compile(r"\btc-[0-9a-f]{8}\b")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

# Keys whose values are wall-clock timestamps.
TIMESTAMP_KEYS = {
    "timestamp", "started_at", "completed_at", "created_at", "updated_at",
    "modified_at", "last_updated",
}
# Keys whose values are measured durations (non-deterministic).
DURATION_KEYS = {
    "duration_ms", "durationMs", "duration_seconds", "elapsedMs", "elapsed_ms",
    "total_time", "elapsed",
}


class Normalizer:
    """Replaces uuids with stable ordinals (per instance) and volatile values with sentinels."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def _sub_ids(self, s: str) -> str:
        def repl(m: re.Match) -> str:
            raw = m.group(0).lower()
            if raw not in self._ids:
                self._ids[raw] = f"<id-{len(self._ids) + 1}>"
            return self._ids[raw]

        s = _UUID_RE.sub(repl, s)
        return _TC_RE.sub(repl, s)

    def __call__(self, obj: Any, key: str | None = None) -> Any:
        if key in TIMESTAMP_KEYS and obj is not None:
            return "<timestamp>"
        if key in DURATION_KEYS and obj is not None:
            return "<duration>"
        if isinstance(obj, dict):
            return {k: self(v, k) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self(v) for v in obj]
        if isinstance(obj, str):
            if _ISO_RE.match(obj):
                return "<timestamp>"
            return self._sub_ids(obj)
        return obj


def normalize(obj: Any) -> Any:
    return Normalizer()(obj)


def sse_events(raw_events: list[dict]) -> list[dict]:
    """sse-starlette style dicts {event, id, data(json str)} -> {event, id, data(obj)}."""
    out = []
    for e in raw_events:
        item = dict(e)
        if isinstance(item.get("data"), str):
            try:
                item["data"] = json.loads(item["data"])
            except ValueError:
                pass
        out.append(item)
    return out


def parse_sse_text(text: str) -> list[dict]:
    """Parse raw 'event: x\\ndata: {...}\\n\\n' text (StreamingResponse style)."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev: dict[str, Any] = {}
        for line in block.splitlines():
            name, _, value = line.partition(": ")
            if name == "data":
                ev["data"] = json.loads(value)
            else:
                ev[name] = value
        events.append(ev)
    return events


def _path(name: str, directory: Path | None = None) -> Path:
    return (directory or FIXTURE_DIR) / f"{name}.json"


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False, default=str) + "\n"


def check_or_record(name: str, live: Any, directory: Path | None = None) -> None:
    """Compare normalized `live` with fixtures/<name>.json (or `directory`/<name>.json), or re-record."""
    live = json.loads(json.dumps(normalize(live), default=str))  # force plain JSON types
    path = _path(name, directory)
    if os.environ.get(RECORD_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(live), encoding="utf-8")
        return
    assert path.exists(), (
        f"missing fixture {path.name}; run with {RECORD_ENV}=1 to record"
    )
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert live == expected, (
        f"event contract drift in {path.name}. Intentional? re-record with {RECORD_ENV}=1.\n"
        f"--- expected\n{dump(expected)}\n--- live\n{dump(live)}"
    )
