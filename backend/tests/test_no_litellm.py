"""Guard: litellm is being removed from backend/app (plan 003 U28).

The allowlist holds the legacy v2 Deep Work engine, which is deleted by a separate PR.
Shrink ALLOWED to empty when that lands, then drop the dependency.
"""

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {"services/deep_work/engine.py"}


def test_litellm_only_appears_in_allowlisted_files():
    hits = {
        p.relative_to(APP).as_posix()
        for p in APP.rglob("*.py")
        if "litellm" in p.read_text(encoding="utf-8", errors="ignore")
    }
    assert hits <= ALLOWED, f"unexpected litellm references: {sorted(hits - ALLOWED)}"


def test_create_litellm_model_alias_is_gone():
    from app.services import llm_gateway

    assert not hasattr(llm_gateway, "create_litellm_model")
    assert callable(llm_gateway.create_model)
