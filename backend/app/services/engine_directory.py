"""Where a session's engine worker lives (plan 003, U8 / KTD12).

One worker serves one session (KTD4). By default a single worker serves everything. With
``ENGINE_BACKGROUND_URL`` and ``ENGINE_BACKGROUND_SESSION_ID`` set there are two shards: the default
session serves interactive work (chat, Deep Work) and the background session serves automation runs
(``BACKGROUND_RUN_KINDS``), so a burst of workflow or Fleet commits cannot queue ahead of a chat turn.

A conversation is bound to its session once, when it is created (``engine_conversations.session_id``);
every later call uses that recorded session, so changing this configuration never moves a conversation.
"""
from app.config import Settings

BACKGROUND_RUN_KINDS = ("fleet", "workflow", "batch")


class EngineDirectory:
    def __init__(self, settings: Settings):
        self._url = settings.ENGINE_URL.rstrip("/")
        self._session_id = settings.ENGINE_SESSION_ID
        bg_url = (settings.ENGINE_BACKGROUND_URL or "").rstrip("/")
        bg_session = settings.ENGINE_BACKGROUND_SESSION_ID or ""
        # Half-configured means unconfigured: never route to a shard without both halves.
        self._bg_url, self._bg_session = (bg_url, bg_session) if bg_url and bg_session else ("", "")

    @property
    def default_session_id(self) -> str:
        """The interactive shard's session (chat, Deep Work)."""
        return self._session_id

    @property
    def sharded(self) -> bool:
        return bool(self._bg_session)

    @property
    def default_url(self) -> str:
        return self._url

    @property
    def background_url(self) -> str:
        """Where automation runs go: the background shard, else the one engine."""
        return self._bg_url or self._url

    def url_for(self, session_id: str) -> str:
        if self._bg_session and session_id == self._bg_session:
            return self._bg_url
        return self._url

    def session_for(self, tenant_id, run_kind: str) -> str:
        if self._bg_session and run_kind in BACKGROUND_RUN_KINDS:
            return self._bg_session
        return self._session_id
