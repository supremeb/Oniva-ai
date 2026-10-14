"""U6: outbound HTTP safety (SSRF, retries, path params, token binding)."""

import socket
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.connectors.openapi.connector import OpenAPIConnector
from app.connectors.openapi.http_client import SafeHTTPClient, SecurityError
from app.security.url_validator import is_ip_blocked, is_url_safe

DNS = {
    "public.example": "93.184.216.34",
    "other.example": "93.184.216.35",
    "api.good.example": "93.184.216.40",
    "api.evil.example": "93.184.216.41",
    "rebind.example": "10.1.2.3",
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **kw):
        if host not in DNS:
            raise socket.gaierror(f"no such host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def transport(monkeypatch):
    """Route every httpx.AsyncClient through a recording MockTransport."""
    state = {"requests": [], "handler": lambda req: httpx.Response(200, json={})}

    def handler(request):
        state["requests"].append(request)
        return state["handler"](request)

    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    monkeypatch.setattr(SafeHTTPClient, "_backoff", AsyncMock())
    return state


def _host(req):
    return req.headers["host"].split(":")[0]


class TestIpPolicy:
    @pytest.mark.parametrize(
        "ip",
        ["100.64.1.1", "100.127.255.255", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
         "::ffff:7f00:1", "64:ff9b::a00:1", "2002:a00:1::", "::1", "fe80::1", "fd00::1",
         "0.0.0.0", "192.168.0.1", "198.18.0.1", "224.0.0.1", "not-an-ip"],
    )
    def test_blocked(self, ip):
        assert is_ip_blocked(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
    def test_public_allowed(self, ip):
        assert is_ip_blocked(ip) is False

    @pytest.mark.parametrize(
        "url", ["http://100.64.1.1/", "http://[::ffff:10.0.0.1]/", "http://[::ffff:a00:1]/",
                "http://rebind.example/"],
    )
    def test_is_url_safe_blocks(self, url):
        assert is_url_safe(url)


class TestRedirects:
    async def test_redirect_to_metadata_blocked(self, transport):
        def handler(req):
            if _host(req) == "public.example":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            return httpx.Response(200, text="SECRET")

        transport["handler"] = handler
        client = SafeHTTPClient()
        with pytest.raises(SecurityError):
            await client.request("GET", "https://public.example/x")
        assert all("169.254.169.254" not in str(r.url) for r in transport["requests"])
        assert all(_host(r) != "169.254.169.254" for r in transport["requests"])

    async def test_redirect_to_rebinding_host_blocked(self, transport):
        transport["handler"] = lambda req: (
            httpx.Response(302, headers={"location": "https://rebind.example/"})
            if _host(req) == "public.example" else httpx.Response(200, text="SECRET")
        )
        with pytest.raises(SecurityError):
            await SafeHTTPClient().request("GET", "https://public.example/")

    async def test_public_redirect_followed_and_auth_stripped_cross_host(self, transport):
        def handler(req):
            if _host(req) == "public.example":
                return httpx.Response(302, headers={"location": "https://other.example/y"})
            return httpx.Response(200, json={"ok": True})

        transport["handler"] = handler
        resp = await SafeHTTPClient().request(
            "GET", "https://public.example/x", headers={"Authorization": "Bearer tok"}
        )
        assert resp.status_code == 200
        assert transport["requests"][0].headers["authorization"] == "Bearer tok"
        assert "authorization" not in transport["requests"][1].headers

    async def test_connection_pinned_to_validated_ip(self, transport):
        await SafeHTTPClient().request("GET", "https://public.example/x")
        req = transport["requests"][0]
        assert req.url.host == "93.184.216.34"
        assert _host(req) == "public.example"


class TestRetries:
    async def test_post_503_not_retried(self, transport):
        transport["handler"] = lambda req: httpx.Response(503)
        resp = await SafeHTTPClient().request("POST", "https://public.example/pay", json_body={"a": 1})
        assert resp.status_code == 503
        assert len(transport["requests"]) == 1

    async def test_post_network_error_not_retried(self, transport):
        def handler(req):
            raise httpx.ConnectError("boom")

        transport["handler"] = handler
        with pytest.raises(httpx.ConnectError):
            await SafeHTTPClient().request("POST", "https://public.example/pay", json_body={})
        assert len(transport["requests"]) == 1

    async def test_get_503_retried(self, transport):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={})

        transport["handler"] = handler
        resp = await SafeHTTPClient().request("GET", "https://public.example/x")
        assert resp.status_code == 200
        assert len(calls) == 3


def _spec(server: str) -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": server}],
        "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}},
        "security": [{"b": []}],
        "paths": {
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


class TestConnector:
    async def _connector(self, server, **kw):
        c = OpenAPIConnector(_spec(server), **kw)
        assert await c.authenticate({"token": "SECRET-TOKEN"})
        return c

    async def test_path_param_encoded(self, transport):
        c = await self._connector("https://api.good.example")
        await c.call_tool(c.get_tools()[0]["name"], {"id": "../admin"})
        assert transport["requests"][0].url.raw_path == b"/users/..%2Fadmin"

    @pytest.mark.parametrize("bad", ["..", "."])
    async def test_dot_segment_path_param_rejected(self, transport, bad):
        c = await self._connector("https://api.good.example")
        with pytest.raises(ValueError):
            await c.call_tool(c.get_tools()[0]["name"], {"id": bad})
        assert not transport["requests"]

    async def test_spec_server_elsewhere_gets_no_token(self, transport):
        c = await self._connector(
            "https://api.evil.example", allowed_base_url="https://api.good.example"
        )
        await c.call_tool(c.get_tools()[0]["name"], {"id": "1"})
        assert [_host(r) for r in transport["requests"]] == ["api.good.example"]
        assert not any(r.headers.get("authorization") == "Bearer SECRET-TOKEN"
                       for r in transport["requests"] if _host(r) == "api.evil.example")

    async def test_token_sent_to_configured_host(self, transport):
        c = await self._connector("https://api.good.example")
        await c.call_tool(c.get_tools()[0]["name"], {"id": "1"})
        assert transport["requests"][0].headers["authorization"] == "Bearer SECRET-TOKEN"

    async def test_tool_meta_base_url_override_ignored(self, transport):
        c = await self._connector("https://api.good.example")
        c.get_tools()[0]["_meta"]["base_url"] = "https://api.evil.example"
        await c.call_tool(c.get_tools()[0]["name"], {"id": "1"})
        assert [_host(r) for r in transport["requests"]] == ["api.good.example"]


class TestBrowseUrl:
    async def test_redirect_to_metadata_blocked(self, transport):
        from app.skills.browse_url.tool import browse_url

        transport["handler"] = lambda req: (
            httpx.Response(302, headers={"location": "http://169.254.169.254/"})
            if _host(req) == "public.example" else httpx.Response(200, text="SECRET")
        )
        result = await browse_url(None, "https://public.example/")
        assert result["status"] == "blocked"
        assert len(transport["requests"]) == 1

    async def test_direct_cgnat_blocked(self, transport):
        from app.skills.browse_url.tool import browse_url

        result = await browse_url(None, "http://100.64.1.1/")
        assert result["status"] == "blocked"
        assert not transport["requests"]
