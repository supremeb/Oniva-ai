"""The /internal router: engine -> backend. Token-guarded; absent unless configured."""
import logging

from fastapi import APIRouter, Depends, FastAPI

from app.config import Settings
from app.internal_api.auth import InternalAuth, InternalUnauthorized, internal_unauthorized_handler
from app.internal_api.logging import install_redaction
from app.internal_api.models import router as models_router
from app.internal_api.tools import router as tools_router

logger = logging.getLogger(__name__)


def build_internal_router(settings: Settings) -> APIRouter:
    auth = InternalAuth(settings.ENGINE_GATEWAY_TOKEN, settings.ENGINE_GATEWAY_TOKEN_PREVIOUS)
    router = APIRouter(prefix="/internal", dependencies=[Depends(auth)], include_in_schema=False)

    @router.get("/ping")
    async def ping():
        return {"ok": True}

    router.include_router(tools_router)
    router.include_router(models_router)
    return router


def mount_internal_router(app: FastAPI, settings: Settings) -> bool:
    """Mount /internal only when ENGINE_GATEWAY_TOKEN is set. Returns whether it was mounted."""
    if not settings.ENGINE_GATEWAY_TOKEN:
        return False
    install_redaction()
    app.add_exception_handler(InternalUnauthorized, internal_unauthorized_handler)
    app.include_router(build_internal_router(settings))
    logger.info("Mounted /internal (engine gateway)")
    return True
