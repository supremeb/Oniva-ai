"""U25 - the project memory store service (path confinement, caps, CRUD, versioning, status) on SQLite."""
import uuid

import pytest
from sqlalchemy import select

from app.models.project import Project
from app.models.project_memory import MemoryBullet, MemoryFile
from app.models.tenant import Tenant
from app.models.user import User
from app.services import project_memory as pm
from app.services.project_memory import MemoryStoreError

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()
USER = uuid.uuid4()


@pytest.fixture
async def world(test_db):
    db = test_db
    db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    db.add(Tenant(id=OTHER_TENANT, name="o", slug="o", settings={}))
    db.add(User(id=USER, tenant_id=TENANT, email="u@x.io", name="u", role="member", is_active=True))
    await db.commit()
    for pid, tid in ((PROJECT, TENANT), (OTHER_PROJECT, OTHER_TENANT)):
        db.add(Project(id=pid, tenant_id=tid, owner_id=USER, name="p"))
    await db.commit()
    return db


def kw(**extra):
    return {"tenant_id": TENANT, "project_id": PROJECT, **extra}


# --- path confinement -----------------------------------------------------------

@pytest.mark.parametrize("path", [
    "../core.md", "a/../core.md", "/core.md", "dir\\core.md", "a" * 126 + ".md",
    "core.py", "core", "core.MD.exe", "sp ace.md", "core.md/", "", "a//b.md", ".md",
    "playbook.md",
])
async def test_confined_paths_are_rejected_on_write(world, path):
    with pytest.raises(MemoryStoreError) as ei:
        await pm.create(world, **kw(), path=path, content="x", source_kind="model")
    assert ei.value.code in {"invalid_path", "read_only"}


async def test_playbook_is_read_only_for_every_write_command(world):
    for call in (
        pm.create(world, **kw(), path="playbook.md", content="x", source_kind="model"),
        pm.str_replace(world, **kw(), path="playbook.md", old="a", new="b"),
        pm.insert(world, **kw(), path="playbook.md", line=1, text="x"),
        pm.delete(world, **kw(), path="playbook.md"),
    ):
        with pytest.raises(MemoryStoreError) as ei:
            await call
        assert ei.value.code == "read_only"


@pytest.mark.parametrize("path", ["core.md", "notes.txt", "topics/billing.md", "a-b_c.1/d.md"])
async def test_valid_paths_are_accepted(world, path):
    row = await pm.create(world, **kw(), path=path, content="hi", source_kind="user")
    assert row.path == path and row.version == 1


# --- CRUD semantics -------------------------------------------------------------

async def test_create_view_roundtrip_with_provenance_and_hash(world):
    conv = uuid.uuid4()
    row = await pm.create(world, **kw(), path="core.md", content="line1\nline2\n", source_kind="model",
                          source_conversation_id=conv)
    assert (row.status, row.source_kind, row.source_conversation_id) == ("active", "model", conv)
    assert len(row.content_hash) == 64
    got = await pm.view(world, **kw(), path="core.md")
    assert got["content"] == "line1\nline2\n" and got["version"] == 1


async def test_view_range_is_one_based_inclusive(world):
    await pm.create(world, **kw(), path="core.md", content="a\nb\nc\nd", source_kind="user")
    got = await pm.view(world, **kw(), path="core.md", view_range=(2, 3))
    assert got["content"] == "b\nc"
    with pytest.raises(MemoryStoreError) as ei:
        await pm.view(world, **kw(), path="core.md", view_range=(3, 9))
    assert ei.value.code == "out_of_range"


async def test_view_missing_file_is_not_found(world):
    with pytest.raises(MemoryStoreError) as ei:
        await pm.view(world, **kw(), path="nope.md")
    assert ei.value.code == "not_found"


async def test_create_over_existing_path_replaces_and_bumps_version(world):
    await pm.create(world, **kw(), path="core.md", content="one", source_kind="model")
    row = await pm.create(world, **kw(), path="core.md", content="two", source_kind="model")
    assert row.version == 2 and row.content == "two"
    assert len((await world.execute(select(MemoryFile))).scalars().all()) == 1


async def test_str_replace_requires_exactly_one_match(world):
    await pm.create(world, **kw(), path="core.md", content="cat dog cat", source_kind="user")
    for old, code in (("cat", "multiple_matches"), ("bird", "no_match"), ("", "invalid_argument")):
        with pytest.raises(MemoryStoreError) as ei:
            await pm.str_replace(world, **kw(), path="core.md", old=old, new="x")
        assert ei.value.code == code
    row = await pm.str_replace(world, **kw(), path="core.md", old="dog", new="fox")
    assert row.content == "cat fox cat" and row.version == 2


async def test_insert_takes_a_one_based_line(world):
    await pm.create(world, **kw(), path="core.md", content="a\nb", source_kind="user")
    row = await pm.insert(world, **kw(), path="core.md", line=1, text="top")
    assert row.content == "top\na\nb"
    row = await pm.insert(world, **kw(), path="core.md", line=4, text="end")  # one past the last line appends
    assert row.content == "top\na\nb\nend" and row.version == 3
    for bad in (0, -1, 6):
        with pytest.raises(MemoryStoreError) as ei:
            await pm.insert(world, **kw(), path="core.md", line=bad, text="x")
        assert ei.value.code == "out_of_range"


async def test_delete_removes_and_missing_is_not_found(world):
    await pm.create(world, **kw(), path="core.md", content="x", source_kind="user")
    await pm.delete(world, **kw(), path="core.md")
    assert (await world.execute(select(MemoryFile))).scalars().all() == []
    with pytest.raises(MemoryStoreError) as ei:
        await pm.delete(world, **kw(), path="core.md")
    assert ei.value.code == "not_found"


async def test_optimistic_version_check(world):
    await pm.create(world, **kw(), path="core.md", content="a", source_kind="user")
    await pm.str_replace(world, **kw(), path="core.md", old="a", new="b", expected_version=1)
    with pytest.raises(MemoryStoreError) as ei:
        await pm.str_replace(world, **kw(), path="core.md", old="b", new="c", expected_version=1)
    assert ei.value.code == "version_conflict"
    with pytest.raises(MemoryStoreError):
        await pm.insert(world, **kw(), path="core.md", line=1, text="x", expected_version=1)
    with pytest.raises(MemoryStoreError):
        await pm.delete(world, **kw(), path="core.md", expected_version=1)
    with pytest.raises(MemoryStoreError):
        await pm.create(world, **kw(), path="core.md", content="z", source_kind="user", expected_version=1)


# --- caps -----------------------------------------------------------------------

async def test_file_size_cap_applies_to_every_write(world):
    with pytest.raises(MemoryStoreError) as ei:
        await pm.create(world, **kw(), path="big.md", content="x" * (pm.MAX_FILE_BYTES + 1), source_kind="user")
    assert ei.value.code == "too_large"
    await pm.create(world, **kw(), path="big.md", content="x" * pm.MAX_FILE_BYTES, source_kind="user")
    with pytest.raises(MemoryStoreError) as ei:
        await pm.insert(world, **kw(), path="big.md", line=1, text="y")
    assert ei.value.code == "too_large"


async def test_size_cap_counts_utf8_bytes(world):
    with pytest.raises(MemoryStoreError):
        await pm.create(world, **kw(), path="u.md", content="é" * (pm.MAX_FILE_BYTES // 2 + 1), source_kind="user")


async def test_core_md_has_a_smaller_cap(world):
    with pytest.raises(MemoryStoreError) as ei:
        await pm.create(world, **kw(), path="core.md", content="x" * (pm.MAX_CORE_BYTES + 1), source_kind="user")
    assert ei.value.code == "too_large"
    await pm.create(world, **kw(), path="core.md", content="x" * pm.MAX_CORE_BYTES, source_kind="user")


async def test_file_count_cap_per_project(world, monkeypatch):
    monkeypatch.setattr(pm, "MAX_FILES_PER_PROJECT", 3)
    for i in range(3):
        await pm.create(world, **kw(), path=f"f{i}.md", content="x", source_kind="user")
    with pytest.raises(MemoryStoreError) as ei:
        await pm.create(world, **kw(), path="f9.md", content="x", source_kind="user")
    assert ei.value.code == "too_many_files"
    await pm.create(world, **kw(), path="f1.md", content="replace ok", source_kind="user")  # not a new file
    await pm.delete(world, **kw(), path="f0.md")
    await pm.create(world, **kw(), path="f9.md", content="x", source_kind="user")
    # another project is unaffected
    await pm.create(world, tenant_id=OTHER_TENANT, project_id=OTHER_PROJECT, path="f9.md", content="x",
                    source_kind="user")


def test_caps_match_the_plan():
    assert (pm.MAX_PATH_CHARS, pm.MAX_FILE_BYTES, pm.MAX_FILES_PER_PROJECT, pm.MAX_BULLETS_PER_PROJECT) == (
        128, 32 * 1024, 64, 400)
    assert pm.MAX_CORE_BYTES == 8 * 1024


# --- status, scope, index, core -------------------------------------------------

async def test_pending_rows_are_invisible_unless_asked(world):
    await pm.create(world, **kw(), path="core.md", content="pending note", source_kind="curator", status="pending")
    with pytest.raises(MemoryStoreError) as ei:
        await pm.view(world, **kw(), path="core.md")
    assert ei.value.code == "not_found"
    assert await pm.list_index(world, **kw()) == []
    assert await pm.render_core(world, **kw()) == ""
    got = await pm.view(world, **kw(), path="core.md", include_pending=True)
    assert got["status"] == "pending"
    assert len(await pm.list_index(world, **kw(), include_pending=True)) == 1
    # a model write cannot edit or clobber a pending row it cannot see
    with pytest.raises(MemoryStoreError):
        await pm.str_replace(world, **kw(), path="core.md", old="pending", new="x")


async def test_invalid_status_or_source_kind_rejected(world):
    with pytest.raises(MemoryStoreError):
        await pm.create(world, **kw(), path="a.md", content="x", source_kind="user", status="weird")
    with pytest.raises(MemoryStoreError):
        await pm.create(world, **kw(), path="a.md", content="x", source_kind="alien")


async def test_list_index_shape_and_order(world):
    await pm.create(world, **kw(), path="b.md", content="bb", source_kind="user")
    await pm.create(world, **kw(), path="a.md", content="a", source_kind="user")
    idx = await pm.list_index(world, **kw())
    assert [e["path"] for e in idx] == ["a.md", "b.md"]
    assert idx[0]["size"] == 1 and idx[0]["version"] == 1


async def test_view_without_path_returns_the_index(world):
    await pm.create(world, **kw(), path="a.md", content="a", source_kind="user")
    got = await pm.view(world, **kw())
    assert [e["path"] for e in got["files"]] == ["a.md"]


async def test_user_scoped_files_are_separate_from_project_files(world):
    await pm.create(world, **kw(), path="core.md", content="project", source_kind="user")
    await pm.create(world, **kw(user_id=USER), path="core.md", content="mine", source_kind="user")
    assert (await pm.view(world, **kw(), path="core.md"))["content"] == "project"
    assert (await pm.view(world, **kw(user_id=USER), path="core.md"))["content"] == "mine"
    await pm.create(world, **kw(), path="core.md", content="project2", source_kind="user")  # still one per scope
    assert len((await world.execute(select(MemoryFile))).scalars().all()) == 2


async def test_scoping_by_tenant_and_project(world):
    await pm.create(world, **kw(), path="core.md", content="x", source_kind="user")
    with pytest.raises(MemoryStoreError):
        await pm.view(world, tenant_id=TENANT, project_id=OTHER_PROJECT, path="core.md")
    with pytest.raises(MemoryStoreError):
        await pm.view(world, tenant_id=OTHER_TENANT, project_id=PROJECT, path="core.md")


async def test_render_core_cuts_at_the_token_budget(world):
    assert await pm.render_core(world, **kw()) == ""
    await pm.create(world, **kw(), path="core.md", content="prefers EUR", source_kind="user")
    assert await pm.render_core(world, **kw()) == "prefers EUR"
    await pm.create(world, **kw(), path="core.md", content="y" * pm.MAX_CORE_BYTES, source_kind="user")
    out = await pm.render_core(world, **kw())
    assert len(out.encode()) <= pm.CORE_INJECT_BYTES + 64 and out.endswith(pm.TRUNCATION_MARKER)


async def test_playbook_virtual_file_renders_active_bullets_only(world):
    world.add_all([
        MemoryBullet(tenant_id=TENANT, project_id=PROJECT, bullet_key="b1", section="s", text="use EUR",
                     helpful=3, harmful=0, status="active", source_kind="curator"),
        MemoryBullet(tenant_id=TENANT, project_id=PROJECT, bullet_key="b2", section="s", text="hidden",
                     status="pending", source_kind="curator"),
    ])
    await world.commit()
    got = await pm.view(world, **kw(), path="playbook.md")
    assert "use EUR" in got["content"] and "hidden" not in got["content"]
    assert got["read_only"] is True
    assert "playbook.md" in [e["path"] for e in await pm.list_index(world, **kw())]
