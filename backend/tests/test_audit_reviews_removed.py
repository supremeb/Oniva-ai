"""U15 / R4, AE7: the /audit/reviews* routes and ReviewQueueService are gone."""
import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient


def test_openapi_has_no_audit_reviews_routes():
    from app.main import app

    paths = app.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/api/v1/audit/reviews")]


def test_review_queue_module_is_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.review_queue")


def test_no_app_source_references_the_deleted_service():
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(p) for p in root.rglob("*.py")
        if "review_queue import" in (t := p.read_text()) or "ReviewQueueService" in t
    ]
    assert offenders == []


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/audit/reviews"),
    ("post", "/api/v1/audit/reviews/abc/decision"),
])
def test_old_routes_do_not_resolve_to_a_review_handler(method, path):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = getattr(client, method)(path)
    # 404 (no route) or auth/validation rejection from the generic /{event_id} route; never a review handler.
    assert resp.status_code in (401, 403, 404, 405, 422)
