import asyncio
import os

import pytest


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """The eval CLI/runner call asyncio.run(), which unsets the main-thread loop; pytest-asyncio 0.24 needs one.

    Put back the very loop that was installed before the test instead of a fresh one: a loop nobody owns or
    closes leaks into later tests (e.g. the Postgres fencing tests) that cache or compare the current loop.
    """
    policy = asyncio.get_event_loop_policy()
    try:
        before = policy.get_event_loop()
    except RuntimeError:
        before = None
    yield
    if before is not None and not before.is_closed():
        asyncio.set_event_loop(before)
        return
    try:
        policy.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def pytest_collection_modifyitems(config, items):
    """Tests marked `e2e` here boot the real stack (Postgres, backend, worker); they run only with ONIVA_E2E=1."""
    if os.environ.get("ONIVA_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="e2e: set ONIVA_E2E=1 to run")
    for item in items:
        if "e2e" in item.keywords and "tests/evals" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)
