"""The gateway's default tool rate limiter is built from the configured Redis URL."""

from app.internal_api import execution


def test_default_limiter_uses_configured_redis(monkeypatch):
    monkeypatch.setattr(execution, "_default_limiter", None)
    limiter = execution.get_rate_limiter()
    try:
        assert limiter._client is not None
    finally:
        monkeypatch.setattr(execution, "_default_limiter", None)
