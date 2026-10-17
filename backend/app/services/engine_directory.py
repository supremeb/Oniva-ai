"""Where a session's engine worker lives (plan 003, U8 / KTD12). v1: one configured worker."""
from app.config import Settings


class EngineDirectory:
    def __init__(self, settings: Settings):
        self._url = settings.ENGINE_URL.rstrip("/")
        self._session_id = settings.ENGINE_SESSION_ID

    @property
    def default_session_id(self) -> str:
        return self._session_id

    def url_for(self, session_id: str) -> str:  # seam for a per-session map
        return self._url

    def session_for(self, tenant_id, run_kind: str) -> str:  # seam for per-tenant / per-kind sessions
        return self._session_id
