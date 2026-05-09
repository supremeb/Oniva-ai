"""Tests for httpOnly cookie authentication and CSRF protection.

Validates that:
- CSRF tokens are required on state-changing requests
- Bearer header and API key auth bypass CSRF
- Login endpoint is exempt from CSRF
- CSRF token generation is cryptographically unique
"""
import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI

from app.middleware.csrf import CSRFMiddleware, CSRF_COOKIE_NAME, CSRF_HEADER_NAME, generate_csrf_token


@pytest.fixture
def csrf_app():
    """Create a minimal FastAPI app with CSRF middleware for testing."""
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/api/test")
    async def get_test():
        return {"ok": True}

    @app.post("/api/test")
    async def post_test():
        return {"ok": True}

    @app.put("/api/test")
    async def put_test():
        return {"ok": True}

    @app.delete("/api/test")
    async def delete_test():
        return {"ok": True}

    @app.post("/api/v1/auth/login")
    async def login():
        return {"ok": True}

    return app


@pytest.fixture
def csrf_client(csrf_app):
    return TestClient(csrf_app, raise_server_exceptions=False)


class TestCSRFMiddleware:
    def test_get_requests_allowed_without_csrf(self, csrf_client):
        """GET requests should not require CSRF tokens."""
        response = csrf_client.get("/api/test")
        assert response.status_code == 200

    def test_post_without_csrf_rejected(self, csrf_client):
        """POST requests without CSRF token should be rejected."""
        response = csrf_client.post("/api/test")
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_post_with_valid_csrf_accepted(self, csrf_client):
        """POST with matching CSRF cookie + header should succeed."""
        token = generate_csrf_token()
        response = csrf_client.post(
            "/api/test",
            cookies={CSRF_COOKIE_NAME: token},
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_post_with_mismatched_csrf_rejected(self, csrf_client):
        """POST with non-matching CSRF cookie + header should be rejected."""
        response = csrf_client.post(
            "/api/test",
            cookies={CSRF_COOKIE_NAME: "token_a"},
            headers={CSRF_HEADER_NAME: "token_b"},
        )
        assert response.status_code == 403

    def test_put_requires_csrf(self, csrf_client):
        """PUT requests require CSRF tokens."""
        response = csrf_client.put("/api/test")
        assert response.status_code == 403

    def test_delete_requires_csrf(self, csrf_client):
        """DELETE requests require CSRF tokens."""
        response = csrf_client.delete("/api/test")
        assert response.status_code == 403

    def test_login_exempt_from_csrf(self, csrf_client):
        """Auth login endpoint is exempt from CSRF."""
        response = csrf_client.post("/api/v1/auth/login")
        assert response.status_code == 200

    def test_api_key_exempt_from_csrf(self, csrf_client):
        """Requests with X-API-Key header bypass CSRF."""
        response = csrf_client.post(
            "/api/test",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200

    def test_bearer_token_exempt_from_csrf(self, csrf_client):
        """Requests with Bearer token bypass CSRF (direct API clients)."""
        response = csrf_client.post(
            "/api/test",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

    def test_csrf_token_generation_is_unique(self):
        """Each generated CSRF token should be unique."""
        tokens = {generate_csrf_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_csrf_valid_for_put_with_token(self, csrf_client):
        """PUT with valid CSRF token should succeed."""
        token = generate_csrf_token()
        response = csrf_client.put(
            "/api/test",
            cookies={CSRF_COOKIE_NAME: token},
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_csrf_valid_for_delete_with_token(self, csrf_client):
        """DELETE with valid CSRF token should succeed."""
        token = generate_csrf_token()
        response = csrf_client.delete(
            "/api/test",
            cookies={CSRF_COOKIE_NAME: token},
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_csrf_missing_header_only(self, csrf_client):
        """POST with cookie but missing header should be rejected."""
        response = csrf_client.post(
            "/api/test",
            cookies={CSRF_COOKIE_NAME: "some-token"},
        )
        assert response.status_code == 403

    def test_csrf_missing_cookie_only(self, csrf_client):
        """POST with header but missing cookie should be rejected."""
        response = csrf_client.post(
            "/api/test",
            headers={CSRF_HEADER_NAME: "some-token"},
        )
        assert response.status_code == 403
