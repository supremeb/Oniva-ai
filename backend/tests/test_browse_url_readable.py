"""browse_url returns readable text, never raw HTML, capped with a note on how to page on."""
import httpx
import pytest

from app.skills.browse_url import tool as browse_tool
from app.skills.browse_url.tool import MAX_CONTENT_CHARS, browse_url

PAGE = (
    "<html><head><title>T</title><style>.a{color:red}</style><script>var x=1;</script></head>"
    "<body><nav>MENU</nav><main>{body}</main><footer>FOOT</footer></body></html>"
)


@pytest.fixture
def serve(monkeypatch):
    def _serve(html):
        async def fake(client, method, url, **kw):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

        monkeypatch.setattr(browse_tool, "safe_request", fake)

    return _serve


async def test_short_page_is_text_without_markup(serve):
    serve(PAGE.replace("{body}", "<p>Hello <b>world</b></p>"))
    r = await browse_url(None, "https://x.example/")
    assert r["content_type"] == "text" and r["title"] == "T"
    assert "Hello" in r["content"] and "world" in r["content"]
    assert "<" not in r["content"] and "MENU" not in r["content"] and "var x" not in r["content"]
    assert r["truncated"] is False and "note" not in r


async def test_raw_html_request_still_returns_text_not_html(serve):
    serve(PAGE.replace("{body}", "<p>" + "word " * 20_000 + "</p>"))
    r = await browse_url(None, "https://x.example/", extract_text=False)
    assert r["content_type"] == "text"
    assert "<p>" not in r["content"] and len(r["content"]) <= MAX_CONTENT_CHARS


async def test_long_page_is_capped_with_pagination_note(serve):
    serve(PAGE.replace("{body}", "<p>" + "abcdefghi " * 10_000 + "</p>"))
    first = await browse_url(None, "https://x.example/")
    assert first["truncated"] is True and len(first["content"]) <= MAX_CONTENT_CHARS
    assert first["total_chars"] > MAX_CONTENT_CHARS
    assert "offset=" in first["note"]
    nxt = await browse_url(None, "https://x.example/", offset=first["next_offset"])
    assert nxt["offset"] == first["next_offset"] and nxt["content"]
    assert nxt["content"] != first["content"]
