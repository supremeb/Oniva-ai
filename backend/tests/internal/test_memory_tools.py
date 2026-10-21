"""U26 / R21, R24, KTD17: the memory_* gateway tools.

Calls go through the real ``/internal/tools/{name}/invoke`` route (idempotency store, binding, outcome
guard) on the gateway fixture. The tools open their own tenant-scoped session, patched here to the
SQLite-backed factory. Tenant isolation: tenant B's calls only ever see B's project.
"""
import itertools
import json

import pytest
from sqlalchemy import select, update

from app.internal_api.tool_specs import TOOL_SPECS
from app.internal_api.tools import build_manifest
from app.models.project_memory import MemoryFile
from app.models.tenant import Tenant
from app.services import project_memory as store
from app.services import project_memory_tools as pmt
from tests.internal.test_invoke import body, effects, factory, gw, headers, invoke, maker  # noqa: F401  (fixtures)

TOOLS = ("memory_view", "memory_create", "memory_str_replace", "memory_insert", "memory_delete")
_calls = itertools.count(1)


async def set_flag(w, tenant_id, value):
    async with w.maker() as db:
        await db.execute(update(Tenant).where(Tenant.id == tenant_id).values(settings={"features": {pmt.FLAG: value}}))
        await db.commit()


@pytest.fixture
async def mem(gw, monkeypatch):
    monkeypatch.setattr(pmt, "state_factory", lambda tenant_id: gw.factory)
    await set_flag(gw, gw.tenant_a, True)
    await set_flag(gw, gw.tenant_b, True)

    async def call(name, args=None, key=None, tenant="a"):
        r = await invoke(gw, name, call=key or f"m{next(_calls)}", args=args or {}, tenant=tenant)
        assert r.status_code == 200, r.text
        j = r.json()
        j["data"] = json.loads(j["content"]) if not j["isError"] else None
        return j

    async def files(tenant_id):
        async with gw.maker() as db:
            return (await db.execute(select(MemoryFile).where(MemoryFile.tenant_id == tenant_id))).scalars().all()

    gw.call, gw.files = call, files
    return gw


# ---- result shapes -----------------------------------------------------------------


async def test_create_then_view_roundtrip_and_write_echoes_version_and_diff_not_content(mem):
    r = await mem.call("memory_create", {"path": "core.md", "file_text": "# Project\n- Uses APA citations."})
    assert not r["isError"]
    assert r["data"]["path"] == "core.md" and r["data"]["version"] == 1
    assert "+- Uses APA citations." in r["data"]["diff"] and "content" not in r["data"]
    v = await mem.call("memory_view", {"path": "core.md"})
    assert v["data"]["content"] == "# Project\n- Uses APA citations." and v["data"]["version"] == 1
    [row] = await mem.files(mem.tenant_a)
    assert (row.source_kind, row.status, row.project_id, row.source_conversation_id) == (
        "model", "active", mem.project_a, mem.conv_a)


async def test_view_without_path_lists_files_and_supports_view_range(mem):
    await mem.call("memory_create", {"path": "core.md", "file_text": "a\nb\nc"})
    await mem.call("memory_create", {"path": "notes/x.txt", "file_text": "x"})
    idx = (await mem.call("memory_view"))["data"]["files"]
    assert [f["path"] for f in idx] == ["core.md", "notes/x.txt"]
    part = (await mem.call("memory_view", {"path": "core.md", "view_range": [2, 3]}))["data"]
    assert part["content"] == "b\nc"
    bad = await mem.call("memory_view", {"path": "core.md", "view_range": [3, 9]})
    assert bad["isError"] and bad["details"]["error"] == "out_of_range"
    bad = await mem.call("memory_view", {"path": "core.md", "view_range": [1]})
    assert bad["isError"] and bad["details"]["error"] == "invalid_argument"


async def test_str_replace_insert_delete_results(mem):
    await mem.call("memory_create", {"path": "core.md", "file_text": "one\ntwo"})
    r = await mem.call("memory_str_replace", {"path": "core.md", "old_str": "two", "new_str": "2"})
    assert r["data"]["version"] == 2 and "-two" in r["data"]["diff"] and "+2" in r["data"]["diff"]
    r = await mem.call("memory_insert", {"path": "core.md", "insert_line": 1, "insert_text": "zero"})
    assert r["data"]["version"] == 3
    assert (await mem.call("memory_view", {"path": "core.md"}))["data"]["content"] == "zero\none\n2"
    r = await mem.call("memory_delete", {"path": "core.md"})
    assert r["data"] == {"path": "core.md", "deleted": True}
    gone = await mem.call("memory_view", {"path": "core.md"})
    assert gone["isError"] and gone["details"]["error"] == "not_found"


async def test_store_errors_are_is_error_results_with_corrective_messages(mem):
    await mem.call("memory_create", {"path": "core.md", "file_text": "dup dup"})
    cases = [
        ("memory_create", {"path": "../x.md", "file_text": "x"}, "invalid_path"),
        ("memory_create", {"path": "/abs.md", "file_text": "x"}, "invalid_path"),
        ("memory_create", {"path": "a.py", "file_text": "x"}, "invalid_path"),
        ("memory_create", {"path": "playbook.md", "file_text": "x"}, "read_only"),
        ("memory_create", {"path": "big.md", "file_text": "x" * 40_000}, "too_large"),
        ("memory_str_replace", {"path": "core.md", "old_str": "dup", "new_str": "y"}, "multiple_matches"),
        ("memory_str_replace", {"path": "core.md", "old_str": "nope", "new_str": "y"}, "no_match"),
        ("memory_insert", {"path": "core.md", "insert_line": 9, "insert_text": "y"}, "out_of_range"),
        ("memory_insert", {"path": "missing.md", "insert_line": 1, "insert_text": "y"}, "not_found"),
        ("memory_delete", {"path": "missing.md"}, "not_found"),
        ("memory_delete", {"path": "playbook.md"}, "read_only"),
    ]
    for name, args, code in cases:
        r = await mem.call(name, args)
        assert r["isError"] is True, (name, args)
        assert r["details"]["error"] == code, (name, args, r)
        assert r["content"]  # a corrective message, not a stack trace
    [row] = await mem.files(mem.tenant_a)
    assert row.content == "dup dup" and row.version == 1  # nothing changed


async def test_playbook_is_viewable_but_never_writable(mem):
    async with mem.maker() as db:
        from app.models.project_memory import MemoryBullet
        db.add(MemoryBullet(tenant_id=mem.tenant_a, project_id=mem.project_a, bullet_key="b1", section="Strategies",
                            text="Cite primary sources.", status="active", source_kind="curator"))
        await db.commit()
    v = await mem.call("memory_view", {"path": "playbook.md"})
    assert v["data"]["read_only"] is True and "Cite primary sources." in v["data"]["content"]
    w = await mem.call("memory_str_replace", {"path": "playbook.md", "old_str": "Cite", "new_str": "Ignore"})
    assert w["isError"] and w["details"]["error"] == "read_only"


# ---- binding and flag ---------------------------------------------------------------


async def test_project_id_in_arguments_is_an_unknown_parameter(mem):
    for name, args in (("memory_view", {}), ("memory_create", {"path": "core.md", "file_text": "x"})):
        r = await invoke(mem, name, call=f"p{next(_calls)}", args={**args, "project_id": str(mem.project_b)})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", r.text
    assert await mem.files(mem.tenant_b) == []


async def test_tools_are_refused_when_the_flag_is_off_for_the_tenant(mem):
    await set_flag(mem, mem.tenant_a, False)
    r = await mem.call("memory_create", {"path": "core.md", "file_text": "x"})
    assert r["isError"] and r["details"]["error"] == "memory_disabled"
    assert await mem.files(mem.tenant_a) == []
    async with mem.maker() as db:  # no tenant override: the global default (False) applies
        await db.execute(update(Tenant).where(Tenant.id == mem.tenant_a).values(settings={}))
        await db.commit()
    r = await mem.call("memory_view")
    assert r["isError"] and r["details"]["error"] == "memory_disabled"


# ---- pending rows and tenant isolation -----------------------------------------------


async def test_pending_rows_are_invisible_to_the_tools(mem):
    async with mem.maker() as db:
        await store.create(db, tenant_id=mem.tenant_a, project_id=mem.project_a, path="core.md",
                           content="proposed", source_kind="curator", status="pending")
        await store.create(db, tenant_id=mem.tenant_a, project_id=mem.project_a, path="ok.md",
                           content="fine", source_kind="user")
        await db.commit()
    assert [f["path"] for f in (await mem.call("memory_view"))["data"]["files"]] == ["ok.md"]
    assert (await mem.call("memory_view", {"path": "core.md"}))["details"]["error"] == "not_found"
    for name, args in (
        ("memory_str_replace", {"path": "core.md", "old_str": "pro", "new_str": "x"}),
        ("memory_insert", {"path": "core.md", "insert_line": 1, "insert_text": "x"}),
        ("memory_delete", {"path": "core.md"}),
    ):
        assert (await mem.call(name, args))["details"]["error"] == "not_found"
    r = await mem.call("memory_create", {"path": "core.md", "file_text": "model text"})
    assert r["isError"] and r["details"]["error"] == "pending_exists"
    async with mem.maker() as db:
        row = (await db.execute(select(MemoryFile).where(MemoryFile.path == "core.md"))).scalar_one()
        assert (row.content, row.status) == ("proposed", "pending")


async def test_tenant_a_cannot_view_or_write_tenant_b_memory(mem):
    await mem.call("memory_create", {"path": "core.md", "file_text": "A secret"})
    await mem.call("memory_create", {"path": "core.md", "file_text": "B own"}, tenant="b")
    assert (await mem.call("memory_view", {"path": "core.md"}))["data"]["content"] == "A secret"
    assert (await mem.call("memory_view", {"path": "core.md"}, tenant="b"))["data"]["content"] == "B own"
    await mem.call("memory_create", {"path": "only-a.md", "file_text": "x"})
    assert (await mem.call("memory_view", {"path": "only-a.md"}, tenant="b"))["details"]["error"] == "not_found"
    assert (await mem.call("memory_delete", {"path": "only-a.md"}, tenant="b"))["details"]["error"] == "not_found"
    assert {f["path"] for f in (await mem.call("memory_view", tenant="b"))["data"]["files"]} == {"core.md"}
    assert {r.path for r in await mem.files(mem.tenant_a)} == {"core.md", "only-a.md"}
    [b_row] = await mem.files(mem.tenant_b)
    assert (b_row.content, b_row.project_id) == ("B own", mem.project_b)


async def test_a_project_of_another_tenant_in_the_envelope_reaches_nothing(mem):
    # Tenant B's envelope carrying tenant A's project: the binding check refuses before any tool runs.
    await mem.call("memory_create", {"path": "core.md", "file_text": "A secret"})
    b = body(mem, tenant="b", args={"path": "core.md"})
    b["projectId"] = str(mem.project_a)
    r = await mem.client.post("/internal/tools/memory_view/invoke", json=b, headers=headers("r1", "c1"))
    assert r.status_code == 404 and r.json()["error"] == "project_not_found"
    assert [r.content for r in await mem.files(mem.tenant_a)] == ["A secret"]  # untouched


# ---- idempotency ---------------------------------------------------------------------


async def test_same_idempotency_key_replays_a_write_without_a_second_version(mem):
    first = await mem.call("memory_str_replace", {"path": "x.md", "old_str": "a", "new_str": "b"}, key="same")
    assert first["isError"]  # nothing to replace yet; error outcomes replay too
    await mem.call("memory_create", {"path": "x.md", "file_text": "a"})
    one = await mem.call("memory_insert", {"path": "x.md", "insert_line": 2, "insert_text": "b"}, key="ins")
    two = await mem.call("memory_insert", {"path": "x.md", "insert_line": 2, "insert_text": "b"}, key="ins")
    assert one == two
    [row] = await mem.files(mem.tenant_a)
    assert row.content == "a\nb" and row.version == 2


# ---- specs and manifest --------------------------------------------------------------


def test_spec_table_matches_ktd17():
    s = TOOL_SPECS
    assert (s["memory_view"].side_effect, s["memory_view"].idempotent, s["memory_view"].untrusted_output) == ("none", True, False)
    assert (s["memory_create"].side_effect, s["memory_create"].idempotent) == ("write", True)
    for name in ("memory_str_replace", "memory_insert", "memory_delete"):
        assert (s[name].side_effect, s[name].idempotent) == ("write", False), name
    for name in TOOLS[1:]:
        assert s[name].scope == "external" and s[name].requires_approval is False and not s[name].untrusted_output, name
    assert s["memory_view"].requires_approval is False


async def test_manifest_lists_the_five_tools_with_the_spec_metadata(mem):
    entries = {t.name: t.model_dump() for t in build_manifest()}
    for name in TOOLS:
        assert name in entries
    assert entries["memory_view"]["sideEffect"] == "none" and entries["memory_create"]["idempotent"] is True
    assert entries["memory_delete"]["idempotent"] is False
    assert "project_id" not in json.dumps(entries["memory_create"]["parameters"])
