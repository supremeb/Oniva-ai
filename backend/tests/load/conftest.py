import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Load tests (everything here except the synthetic gate tests) need the e2e stack: skipped unless ONIVA_E2E=1."""
    enabled = os.environ.get("ONIVA_E2E") == "1"
    skip = pytest.mark.skip(reason="load: set ONIVA_E2E=1 and select with -m load")
    for item in items:
        path = str(item.fspath).replace(os.sep, "/")
        if "tests/load" in path and not path.endswith("test_gate.py"):
            item.add_marker(pytest.mark.load)
            if not enabled:
                item.add_marker(skip)


from tests.e2e.harness import world  # noqa: E402,F401  (module-scoped stack fixture)
