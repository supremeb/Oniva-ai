"""Conversation-scoped offload store (plan 2026-10-21-001 U15, KTD9).

The run workspace is keyed by ``run_id``, so a file written in one run is unreadable in the next.
Offloaded tool results and archived history must outlive a run and be shared by a conversation
tree (orchestrator and its workers), so they live under one session key per *root* conversation:

    <SESSION_BASE_PATH>/<tenant>/conv-<root conversation uuid>/{tool_results,conversation_history}/...

The tenant id is a path component of the underlying ``SessionStorage``, so a path never crosses
tenants. Callers never supply a path: it is built here from validated ids, and reads accept only
the two fixed prefixes.
"""
import re
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.services.session_storage import get_session_storage

TOOL_RESULTS_PREFIX = "tool_results/"
HISTORY_PREFIX = "conversation_history/"
MAX_BYTES = 2 * 1024 * 1024  # one offloaded body

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Tool call ids are the engine's (`[A-Za-z0-9_.:-]{1,128}`, engine/test/support/fake-gateway.ts); provider ids
# such as `functions.get:0` are legal. Dots and colons are harmless in a file name (no separator).
_TOOL_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TOOL_RESULT_PATH = re.compile(r"tool_results/([A-Za-z0-9_.:-]{1,128})\.txt")
_HISTORY_PATH = re.compile(r"conversation_history/([A-Za-z0-9_-]{1,128})\.md")


class WorkspaceError(ValueError):
    pass


class InvalidId(WorkspaceError):
    pass


class InvalidPath(WorkspaceError):
    pass


class TooLarge(WorkspaceError):
    pass


@dataclass(frozen=True)
class PutResult:
    path: str
    created: bool
    size_bytes: int


def session_key(root_conversation_id: str) -> str:
    """``conv-<uuid>``. (``conv:<uuid>`` in the plan: SessionStorage rejects ``:`` in a component.)"""
    try:
        return f"conv-{uuid.UUID(str(root_conversation_id))}"
    except ValueError:
        raise InvalidId("conversation id is not a UUID")


def _tenant(tenant_id: str) -> str:
    try:
        return str(uuid.UUID(str(tenant_id)))
    except ValueError:
        raise InvalidId("tenant id is not a UUID")


def _check_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidId(f"invalid {label}")
    return value


def is_conversation_path(file_path: str) -> bool:
    """Whether ``file_path`` names the conversation store rather than the run workspace."""
    return file_path.startswith((TOOL_RESULTS_PREFIX, HISTORY_PREFIX))


def _resolve(file_path: str) -> str:
    if not (_TOOL_RESULT_PATH.fullmatch(file_path) or _HISTORY_PATH.fullmatch(file_path)):
        raise InvalidPath(f"not a conversation workspace path: {file_path}")
    return file_path


async def _put(tenant_id: str, root: str, rel: str, content: str) -> PutResult:
    tenant, key = _tenant(tenant_id), session_key(root)
    storage = get_session_storage()
    existing = storage._get_session_path(tenant, key) / rel
    if existing.exists():
        # Idempotent repeat: answer what is stored, even when this body differs or is over the cap.
        return PutResult(rel, False, existing.stat().st_size)
    size = len(content.encode("utf-8"))
    if size > MAX_BYTES:
        raise TooLarge(f"body is {size} bytes; limit is {MAX_BYTES}")
    await storage.write(tenant, key, rel, content)
    return PutResult(rel, True, size)


async def put_tool_result(tenant_id: str, root_conversation_id: str, tool_call_id: str, content: str) -> PutResult:
    """Write ``tool_results/<toolCallId>.txt`` once; a second put with the same id is a no-op."""
    if not _TOOL_CALL_ID.fullmatch(tool_call_id):
        raise InvalidId("invalid tool call id")
    return await _put(tenant_id, root_conversation_id, f"{TOOL_RESULTS_PREFIX}{tool_call_id}.txt", content)


async def put_history(tenant_id: str, root_conversation_id: str, compaction_id: str, content: str) -> PutResult:
    """Write ``conversation_history/<compactionId>.md`` once."""
    _check_id(compaction_id, "compaction id")
    return await _put(tenant_id, root_conversation_id, f"{HISTORY_PREFIX}{compaction_id}.md", content)


async def read(tenant_id: str, root_conversation_id: str, file_path: str) -> Optional[str]:
    """File text, or ``None`` when absent. Raises InvalidPath outside the two prefixes."""
    rel = _resolve(file_path)
    tenant, key = _tenant(tenant_id), session_key(root_conversation_id)
    storage = get_session_storage()
    if not (storage._get_session_path(tenant, key) / rel).is_file():
        return None
    return await storage.read(tenant, key, rel)


async def list_files(tenant_id: str, root_conversation_id: str) -> List[str]:
    return await get_session_storage().list_files(_tenant(tenant_id), session_key(root_conversation_id))


async def delete_conversation(tenant_id: str, root_conversation_id: str) -> bool:
    """Remove everything stored for a conversation. Call when the conversation is hard-deleted."""
    return await get_session_storage().delete_session(_tenant(tenant_id), session_key(root_conversation_id))
