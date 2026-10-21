"""U15: the conversation-scoped offload store (KTD9)."""
import uuid

import pytest

from app.services import conversation_workspace as cw
from app.services import session_storage as ss


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    storage = ss.SessionStorage(base_path=tmp_path, checkpointing_enabled=False)
    monkeypatch.setattr(ss, "_session_storage", storage, raising=False)
    monkeypatch.setattr(cw, "get_session_storage", lambda: storage)
    return storage


TENANT = str(uuid.uuid4())
ROOT = str(uuid.uuid4())


async def test_put_is_idempotent_and_never_overwrites():
    first = await cw.put_tool_result(TENANT, ROOT, "call_1", "hello")
    second = await cw.put_tool_result(TENANT, ROOT, "call_1", "DIFFERENT")
    assert first.path == second.path == "tool_results/call_1.txt"
    assert first.created is True and second.created is False
    assert await cw.read(TENANT, ROOT, "tool_results/call_1.txt") == "hello"


async def test_two_calls_get_two_files():
    a = await cw.put_tool_result(TENANT, ROOT, "call_a", "A")
    b = await cw.put_tool_result(TENANT, ROOT, "call_b", "B")
    assert a.path != b.path
    assert sorted(await cw.list_files(TENANT, ROOT)) == ["tool_results/call_a.txt", "tool_results/call_b.txt"]


async def test_history_uses_its_own_prefix():
    r = await cw.put_history(TENANT, ROOT, "cmp-1", "# summary")
    assert r.path == "conversation_history/cmp-1.md"
    assert await cw.read(TENANT, ROOT, r.path) == "# summary"


async def test_oversize_body_is_rejected():
    with pytest.raises(cw.TooLarge):
        await cw.put_tool_result(TENANT, ROOT, "big", "x" * (cw.MAX_BYTES + 1))


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a b","x" * 200, "a\x00b"])
async def test_bad_ids_are_rejected(bad):
    with pytest.raises(cw.InvalidId):
        await cw.put_tool_result(TENANT, ROOT, bad, "x")


async def test_bad_root_is_rejected():
    with pytest.raises(cw.InvalidId):
        await cw.put_tool_result(TENANT, "../etc", "c", "x")


@pytest.mark.parametrize("path", [
    "../secret", "/etc/passwd", "tool_results/../x.txt", "tool_results/a/b.txt",
    "tool_results/a.md", "conversation_history/a.txt", "other/a.txt", "tool_results/",
])
async def test_read_rejects_paths_outside_the_two_prefixes(path):
    with pytest.raises(cw.InvalidPath):
        await cw.read(TENANT, ROOT, path)


async def test_is_workspace_path():
    assert cw.is_conversation_path("tool_results/x.txt")
    assert cw.is_conversation_path("conversation_history/x.md")
    assert not cw.is_conversation_path("data/x.txt")
    assert not cw.is_conversation_path("tool_results_extra/x.txt")


async def test_other_tenant_and_other_conversation_cannot_read():
    await cw.put_tool_result(TENANT, ROOT, "c1", "secret")
    other_tenant = str(uuid.uuid4())
    other_root = str(uuid.uuid4())
    assert await cw.read(other_tenant, ROOT, "tool_results/c1.txt") is None
    assert await cw.read(TENANT, other_root, "tool_results/c1.txt") is None


async def test_delete_conversation_removes_its_files_only():
    other = str(uuid.uuid4())
    await cw.put_tool_result(TENANT, ROOT, "c1", "x")
    await cw.put_tool_result(TENANT, other, "c1", "y")
    assert await cw.delete_conversation(TENANT, ROOT) is True
    assert await cw.read(TENANT, ROOT, "tool_results/c1.txt") is None
    assert await cw.read(TENANT, other, "tool_results/c1.txt") == "y"
    assert await cw.delete_conversation(TENANT, ROOT) is False
