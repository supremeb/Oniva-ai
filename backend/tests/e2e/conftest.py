import os

import pytest


def pytest_collection_modifyitems(config, items):
    enabled = os.environ.get("ONIVA_E2E") == "1"
    skip = pytest.mark.skip(reason="e2e: set ONIVA_E2E=1 to run")
    for item in items:
        if "tests/e2e" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.e2e)
            if not enabled:
                item.add_marker(skip)


from tests.e2e.harness import world  # noqa: E402,F401  (module-scoped stack fixture)
