import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Tests marked `e2e` here boot the real stack (Postgres, backend, worker); they run only with ONIVA_E2E=1."""
    if os.environ.get("ONIVA_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="e2e: set ONIVA_E2E=1 to run")
    for item in items:
        if "e2e" in item.keywords and "tests/evals" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)
