"""Plan 003 U5: the run handler registry."""
import importlib
import logging
import sys
import types

import pytest

from app.services import run_handlers as rh


@pytest.fixture(autouse=True)
def _clean():
    rh.reset()
    yield
    rh.reset()


async def _noop(*a, **k):
    return None


def test_hooks_registered_by_two_calls_for_one_kind_are_both_used():
    rh.register("fleet", start_missing=_noop)
    rh.register("fleet", on_finished=_noop, find_stuck=_noop)
    assert rh.hook("fleet", "start_missing") is _noop
    assert rh.hook("fleet", "on_finished") is _noop
    assert rh.hook("fleet", "find_stuck") is _noop
    assert rh.hook("fleet", "fail") is None
    assert rh.kinds() == ["fleet"]


def test_registering_the_same_hook_twice_raises():
    rh.register("fleet", on_finished=_noop)
    with pytest.raises(rh.RunHandlerError, match="on_finished"):
        rh.register("fleet", on_finished=_noop)


def test_register_without_hooks_raises():
    with pytest.raises(rh.RunHandlerError):
        rh.register("fleet")


def test_an_unregistered_kind_has_no_hooks():
    assert rh.hook("ghost", "on_finished") is None
    assert rh.kinds() == []


def test_bootstrap_imports_every_listed_module_and_registers_each_kind_once(monkeypatch):
    def make(name, kind, **hooks):
        mod = types.ModuleType(name)
        rh_ = rh

        def _register():
            rh_.register(kind, **hooks)

        _register()
        monkeypatch.setitem(sys.modules, name, mod)

    make("fake_fleet_engine", "fleet", start_missing=_noop, find_stuck=_noop)
    make("fake_fleet_run_sync", "fleet", on_finished=_noop)
    make("fake_workflow_run_sync", "workflow", on_finished=_noop)
    kinds = rh.bootstrap(("fake_fleet_engine", "fake_fleet_run_sync", "fake_workflow_run_sync"))
    assert kinds == ["fleet", "workflow"]
    assert rh.missing_on_finished() == []
    # Importing again runs no registration a second time.
    assert rh.bootstrap(("fake_fleet_engine", "fake_fleet_run_sync", "fake_workflow_run_sync")) == kinds


def test_bootstrap_reports_a_kind_without_on_finished(monkeypatch, caplog):
    mod = types.ModuleType("fake_half")
    monkeypatch.setitem(sys.modules, "fake_half", mod)
    rh.register("batch", start_missing=_noop)
    with caplog.at_level(logging.ERROR):
        rh.bootstrap(("fake_half",))
    assert rh.missing_on_finished() == ["batch"]
    assert "batch" in caplog.text


def test_bootstrap_module_list_imports_cleanly_today():
    # Modules register at import and are cached; reload so the reset registry is filled again.
    for name in rh.BOOTSTRAP_MODULES:
        importlib.reload(importlib.import_module(name))
    assert rh.bootstrap() is not None
    # fleet registers start_missing/find_stuck/fail (U7) and on_finished (U9).
    assert "fleet" in rh.kinds()
    # workflow registers start_missing/find_stuck/fail (U15); its on_finished lands with U16 (then drop the filter).
    assert [k for k in rh.missing_on_finished() if k != "workflow"] == [], "every registered run kind needs an on_finished hook"
