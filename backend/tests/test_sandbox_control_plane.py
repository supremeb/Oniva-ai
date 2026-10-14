"""
Tests for Sandbox Control Plane — zero-secrets architecture.

Tests cover:
- Token creation and validation round-trip
- Expired/revoked token rejection
- Proxy endpoint with valid request
- Proxy endpoint with invalid token (401)
- Proxy endpoint with cost cap exceeded (429)
- Sandbox env vars contain token, not real keys
"""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.sandbox_tokens import (
    SandboxTokenMetadata,
    _REDIS_KEY_PREFIX,
    _TOKEN_PREFIX,
)


def _make_mock_redis_with_pipeline():
    """Create a mock Redis service with working pipeline context manager."""
    mock_redis = AsyncMock()
    mock_pipe = MagicMock()
    mock_pipe.hset = MagicMock()
    mock_pipe.expire = MagicMock()

    @asynccontextmanager
    async def _pipeline(transaction=True):
        yield mock_pipe

    mock_redis.pipeline = _pipeline
    mock_redis.execute_pipeline = AsyncMock(return_value=[1, True])
    return mock_redis, mock_pipe


# ── Token module tests ──────────────────────────────────────────


class TestSandboxTokens:
    """Test sandbox token lifecycle."""

    @pytest.mark.asyncio
    async def test_create_and_validate_token(self):
        """Token round-trip via Redis, returns typed SandboxTokenMetadata."""
        mock_redis, mock_pipe = _make_mock_redis_with_pipeline()
        mock_redis.hgetall = AsyncMock(return_value={
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "session_id": "sess-1",
            "worker_id": "w0",
            "created_at": "1709500000.0",
            "tokens_used": "150",
            "requests_count": "3",
        })

        with patch("app.services.sandbox_tokens.get_redis_service", return_value=mock_redis):
            from app.services.sandbox_tokens import create_sandbox_token, validate_sandbox_token

            token = await create_sandbox_token("tenant-1", "user-1", "sess-1", "w0")

            # Token has correct prefix
            assert token.startswith(_TOKEN_PREFIX)
            assert len(token) > len(_TOKEN_PREFIX) + 10  # urlsafe(32) adds ~43 chars

            # Pipeline was called with correct data
            mock_pipe.hset.assert_called_once()
            call_args = mock_pipe.hset.call_args
            redis_key = call_args[0][0]
            assert redis_key == f"{_REDIS_KEY_PREFIX}{token}"
            mapping = call_args[1]["mapping"]
            assert mapping["tenant_id"] == "tenant-1"
            assert mapping["user_id"] == "user-1"

            # Validate returns typed metadata
            meta = await validate_sandbox_token(token)
            assert meta is not None
            assert isinstance(meta, SandboxTokenMetadata)
            assert meta.tenant_id == "tenant-1"
            assert meta.user_id == "user-1"
            assert meta.session_id == "sess-1"
            assert meta.worker_id == "w0"
            assert meta.tokens_used == 150
            assert meta.requests_count == 3

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self):
        """Expired token (empty hgetall) returns None."""
        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})

        with patch("app.services.sandbox_tokens.get_redis_service", return_value=mock_redis):
            from app.services.sandbox_tokens import validate_sandbox_token

            result = await validate_sandbox_token("sbx_tok_expired_token_abc")
            assert result is None


# ── Proxy endpoint tests ─────────────────────────────────────────


class TestSandboxProxy:
    """Test sandbox proxy endpoint."""

    @pytest.mark.asyncio
    async def test_proxy_valid_request(self):
        """Valid token + LLMGateway mock → OpenAI-format response."""
        from app.schemas.sandbox_proxy import SandboxProxyChatRequest, SandboxProxyChatMessage

        meta = SandboxTokenMetadata(
            tenant_id=str(uuid4()),
            user_id=str(uuid4()),
            session_id="sess-1",
            worker_id="w0",
            created_at=1709500000.0,
            tokens_used=0,
            requests_count=0,
        )

        mock_llm_response = MagicMock()
        mock_llm_response.content = "Hello from proxy"
        mock_llm_response.model = "gpt-4o-mini"
        mock_llm_response.input_tokens = 10
        mock_llm_response.output_tokens = 5
        mock_llm_response.cost_usd = 0.00005

        mock_gateway = AsyncMock()
        mock_gateway.complete = AsyncMock(return_value=mock_llm_response)

        with patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=meta), \
             patch("app.api.v1.sandbox_proxy.get_settings") as gs, \
             patch("app.api.v1.sandbox_proxy.LLMGateway", return_value=mock_gateway), \
             patch("app.api.v1.sandbox_proxy.increment_sandbox_token_usage", new_callable=AsyncMock):

            gs.return_value.SANDBOX_ALLOWED_MODELS = "gpt-4o-mini"
            gs.return_value.DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"
            from app.api.v1.sandbox_proxy import proxy_chat_completions

            body = SandboxProxyChatRequest(
                model="gpt-4o-mini",
                messages=[SandboxProxyChatMessage(role="user", content="Hi")],
            )

            mock_request = MagicMock()
            mock_request.headers = {"Authorization": f"Bearer sbx_tok_valid_token_123"}

            mock_db = AsyncMock()

            response = await proxy_chat_completions(body, mock_request, mock_db)

            assert response.choices[0].message.content == "Hello from proxy"
            assert response.usage.total_tokens == 15
            assert response.object == "chat.completion"

    @pytest.mark.asyncio
    async def test_proxy_invalid_token_401(self):
        """Token not in Redis → 401."""
        from fastapi import HTTPException

        with patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=None):
            from app.api.v1.sandbox_proxy import proxy_chat_completions
            from app.schemas.sandbox_proxy import SandboxProxyChatRequest, SandboxProxyChatMessage

            body = SandboxProxyChatRequest(
                model="gpt-4o-mini",
                messages=[SandboxProxyChatMessage(role="user", content="Hi")],
            )

            mock_request = MagicMock()
            mock_request.headers = {"Authorization": "Bearer sbx_tok_invalid_xyz"}
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await proxy_chat_completions(body, mock_request, mock_db)

            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_proxy_cost_cap_exceeded(self):
        """Accumulated real cost over _MAX_COST_PER_EXECUTION → 429."""
        from fastapi import HTTPException

        meta = SandboxTokenMetadata(
            tenant_id=str(uuid4()),
            user_id=str(uuid4()),
            session_id="sess-1",
            worker_id="w0",
            created_at=1709500000.0,
            tokens_used=1_000_000,
            requests_count=100,
            cost_usd=3.00,
        )

        with patch("app.api.v1.sandbox_proxy.validate_sandbox_token", return_value=meta), \
             patch("app.api.v1.sandbox_proxy.get_settings") as gs:
            gs.return_value.SANDBOX_ALLOWED_MODELS = "gpt-4o-mini"
            gs.return_value.DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"
            from app.api.v1.sandbox_proxy import proxy_chat_completions
            from app.schemas.sandbox_proxy import SandboxProxyChatRequest, SandboxProxyChatMessage

            body = SandboxProxyChatRequest(
                model="gpt-4o-mini",
                messages=[SandboxProxyChatMessage(role="user", content="Hi")],
            )

            mock_request = MagicMock()
            mock_request.headers = {"Authorization": "Bearer sbx_tok_valid_token_456"}
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await proxy_chat_completions(body, mock_request, mock_db)

            assert exc_info.value.status_code == 429


# ── Integration test ─────────────────────────────────────────────


class TestSandboxEnvVars:
    """Test env var generation for sandbox."""

    @pytest.mark.asyncio
    async def test_sandbox_env_vars_contain_token_not_keys(self):
        """_build_control_plane_env_vars returns token + proxy URL, not real keys."""
        mock_redis, _ = _make_mock_redis_with_pipeline()

        with patch("app.services.sandbox_tokens.get_redis_service", return_value=mock_redis):
            from app.services.deep_work.engine import _build_control_plane_env_vars

            token, env_vars = await _build_control_plane_env_vars(
                tenant_id="tenant-1",
                user_id="user-1",
                session_id="sess-1",
                worker_id="w0",
                proxy_url="https://api.oniva.ai",
            )

            # Token is opaque, not a real API key
            assert token.startswith("sbx_tok_")
            assert "sk-" not in token

            # Env vars contain proxy URL and token
            assert env_vars["OPENAI_API_KEY"] == token
            assert env_vars["OPENAI_BASE_URL"] == "https://api.oniva.ai/api/v1/sandbox-proxy/v1"
            assert env_vars["SESSION_ID"] == "sess-1"

            # No real API keys present
            assert "ANTHROPIC_API_KEY" not in env_vars
            assert "OPENROUTER_API_KEY" not in env_vars
