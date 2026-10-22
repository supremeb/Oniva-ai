"""U15: read_file offset/limit and conversation-store prefix resolution."""
import uuid

import pytest

from app.agents.skills.context import SkillContext
from app.services import conversation_workspace as cw
from app.services import session_storage as ss
from app.skills.read_file.tool import read_file

TENANT = uuid.uuid4()
ROOT = uuid.uuid4()
RUN = uuid.uuid4()


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    storage = ss.SessionStorage(base_path=tmp_path, checkpointing_enabled=False)
    monkeypatch.setattr(ss, "_session_storage", storage)
    monkeypatch.setattr(cw, "get_session_storage", lambda: storage)
    return storage


def ctx(run=RUN, conv=ROOT, tenant=TENANT):
    return SkillContext.for_gateway(tenant_id=tenant, user_id=uuid.uuid4(), conversation_id=conv, run_id=run)


async def run_file(storage, name, text, run=RUN, tenant=TENANT):
    await storage.write(str(tenant), str(run), f"workspace/{name}", text)


LINES = "".join(f"line {i}\n" for i in range(10))


async def test_unchanged_for_callers_without_paging_params(_storage):
    big = "y" * 50_000
    await run_file(_storage, "data/a.txt", big)
    r = await read_file(ctx(), "data/a.txt")
    assert r == {"success": True, "content": big, "size_bytes": 50_000}


async def test_offset_and_limit_slice_lines(_storage):
    await run_file(_storage, "a.txt", LINES)
    r = await read_file(ctx(), "a.txt", offset=0, limit=5)
    assert r["success"] and r["content"] == "".join(f"line {i}\n" for i in range(5))
    assert r["total_lines"] == 10 and r["next_offset"] == 5
    r = await read_file(ctx(), "a.txt", offset=5, limit=5)
    assert r["content"].startswith("line 5") and r["next_offset"] is None


async def test_offset_past_end_is_empty(_storage):
    await run_file(_storage, "a.txt", LINES)
    r = await read_file(ctx(), "a.txt", offset=99, limit=5)
    assert r["success"] and r["content"] == "" and r["next_offset"] is None and r["total_lines"] == 10


async def test_negative_paging_is_an_error(_storage):
    await run_file(_storage, "a.txt", LINES)
    assert (await read_file(ctx(), "a.txt", offset=-1))["success"] is False
    assert (await read_file(ctx(), "a.txt", limit=0))["success"] is False


async def test_conversation_prefix_defaults_to_a_bounded_page():
    body = "".join(f"row {i}\n" for i in range(1000))
    await cw.put_tool_result(str(TENANT), str(ROOT), "call_1", body)
    r = await read_file(ctx(), "tool_results/call_1.txt")
    assert r["success"] and r["content"].count("\n") == 200
    assert r["total_lines"] == 1000 and r["next_offset"] == 200
    assert "offset=200" in r["note"]
    nxt = await read_file(ctx(), "tool_results/call_1.txt", offset=200, limit=1000)
    assert nxt["content"].startswith("row 200") and nxt["next_offset"] is None


async def test_conversation_prefix_default_is_capped_at_20k_chars():
    body = "".join("z" * 1000 + "\n" for _ in range(100))
    await cw.put_tool_result(str(TENANT), str(ROOT), "wide", body)
    r = await read_file(ctx(), "tool_results/wide.txt")
    assert len(r["content"]) <= 20_000 and r["next_offset"] is not None
    assert r["next_offset"] < 100


async def test_single_line_longer_than_cap_is_clipped_and_pageable():
    await cw.put_tool_result(str(TENANT), str(ROOT), "one", "q" * 50_000)
    r = await read_file(ctx(), "tool_results/one.txt")
    assert r["success"] and 0 < len(r["content"]) <= 20_000


async def test_worker_in_same_root_reads_what_the_orchestrator_offloaded():
    await cw.put_tool_result(str(TENANT), str(ROOT), "call_9", "shared")
    worker = ctx(run=uuid.uuid4())  # different run, same root conversation id
    r = await read_file(worker, "tool_results/call_9.txt")
    assert r["success"] and r["content"].strip() == "shared"


async def test_history_prefix_resolves():
    await cw.put_history(str(TENANT), str(ROOT), "cmp-1", "# old")
    r = await read_file(ctx(), "conversation_history/cmp-1.md")
    assert r["success"] and r["content"].startswith("# old")


async def test_cross_tenant_and_cross_conversation_not_found():
    await cw.put_tool_result(str(TENANT), str(ROOT), "c1", "secret")
    other_t = await read_file(ctx(tenant=uuid.uuid4()), "tool_results/c1.txt")
    other_c = await read_file(ctx(conv=uuid.uuid4()), "tool_results/c1.txt")
    assert other_t["success"] is False and "not found" in other_t["error"].lower()
    assert other_c["success"] is False and "not found" in other_c["error"].lower()


async def test_conversation_prefix_without_conversation_id_is_an_error():
    c = ctx()
    c.metadata["conversation_id"] = None
    r = await read_file(c, "tool_results/c1.txt")
    assert r["success"] is False


async def test_other_paths_still_read_the_run_workspace(_storage):
    await run_file(_storage, "notes/n.txt", "plain")
    r = await read_file(ctx(), "notes/n.txt")
    assert r["content"] == "plain"


@pytest.mark.parametrize("p", ["../x", "/etc/passwd", "tool_results/../../x", "tool_results/a/b.txt"])
async def test_traversal_and_absolute_rejected(p):
    r = await read_file(ctx(), p)
    assert r["success"] is False
