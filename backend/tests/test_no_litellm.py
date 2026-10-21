"""Guard: litellm is gone from the backend (plan 003 U28) and must not come back.

LiteLLM was replaced by native provider SDKs (``app/services/llm_direct.py``) and the engine. Besides the
dependency, it used to call ``load_dotenv()`` on import, leaking ``backend/.env`` into tests.
"""

import importlib.util
import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
IMPORT = re.compile(r"^\s*(import|from)\s+litellm\b", re.MULTILINE)


def test_no_module_imports_litellm():
    hits = sorted(p.relative_to(APP).as_posix() for p in APP.rglob("*.py") if IMPORT.search(p.read_text(errors="ignore")))
    assert hits == [], f"litellm imported in: {hits}"


def test_litellm_is_not_a_dependency_nor_installed():
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not [d for d in deps if d.lower().startswith("litellm")]
    assert importlib.util.find_spec("litellm") is None


def test_create_litellm_model_alias_is_gone():
    from app.services import llm_gateway

    assert not hasattr(llm_gateway, "create_litellm_model")
    assert callable(llm_gateway.create_model)
