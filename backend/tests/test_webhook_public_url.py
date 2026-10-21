"""Webhook trigger URLs come from PUBLIC_API_URL, falling back to the request's base URL."""
from types import SimpleNamespace

from app.api.v1 import webhooks as wh_api


def _req(base="http://localhost:8000/"):
    return SimpleNamespace(base_url=base)


def test_uses_configured_public_url(monkeypatch):
    monkeypatch.setattr(wh_api, "get_settings", lambda: SimpleNamespace(PUBLIC_API_URL="https://api.example.com/"))
    assert wh_api.webhook_url(_req(), "tok") == "https://api.example.com/api/v1/webhooks/tok"


def test_falls_back_to_request_base_url(monkeypatch):
    monkeypatch.setattr(wh_api, "get_settings", lambda: SimpleNamespace(PUBLIC_API_URL=""))
    assert wh_api.webhook_url(_req(), "tok") == "http://localhost:8000/api/v1/webhooks/tok"
