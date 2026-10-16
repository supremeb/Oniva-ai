"""An unhandled exception is logged with traceback, method, path and request id, and answers a generic 500."""
import logging

import httpx
from fastapi import FastAPI

from app.api.exception_handlers import unhandled_exception_handler


async def test_unhandled_exception_is_logged_and_returns_500(caplog):
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.post("/boom")
    async def boom():
        raise RuntimeError("kaboom-secret-detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        with caplog.at_level(logging.ERROR):
            r = await c.post("/boom", content=b"password=hunter2", headers={"x-request-id": "rid-1"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "request_id": "rid-1"}
    rec = next(x for x in caplog.records if "Unhandled exception" in x.getMessage())
    assert rec.exc_info and rec.exc_info[0] is RuntimeError
    assert "POST /boom request_id=rid-1" in rec.getMessage()
    assert "hunter2" not in caplog.text


def test_alembic_logging_config_does_not_disable_existing_loggers():
    from app.core.database import _alembic_config
    assert _alembic_config().attributes["configure_logger"] is False
    import pathlib
    env = (pathlib.Path(__file__).parents[1] / "migrations" / "env.py").read_text()
    assert "disable_existing_loggers=False" in env
