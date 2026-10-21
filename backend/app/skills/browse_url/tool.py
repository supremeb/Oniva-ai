"""
Browse URL Skill
Fetch and parse a web page using HTTP GET + BeautifulSoup. Returns readable text only
(never raw HTML), capped per call; page on with ``offset``.
Use for static HTML pages. For JavaScript-heavy sites, use browser_navigate instead.
"""

from typing import Any, Dict

import httpx
from bs4 import BeautifulSoup

from app.agents.skills.context import SkillContext
from app.security.url_validator import SecurityError, safe_request

# About 5k tokens per call; the model pages on with ``offset`` instead of receiving one huge blob.
MAX_CONTENT_CHARS = 20_000


async def browse_url(
    ctx: SkillContext,
    url: str,
    extract_text: bool = True,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Fetch webpage content as readable text.

    Args:
        ctx: Skill execution context
        url: URL to fetch
        extract_text: Kept for compatibility. Raw HTML is never returned: markup, scripts and
            styles are stripped either way, so the model does not pay tokens for tags.
        offset: Character offset into the extracted text, to read past a truncated page.

    Returns:
        Page text (at most ``MAX_CONTENT_CHARS``). When cut, ``truncated`` is true, ``next_offset``
        and a ``note`` say how to read the next slice.
    """
    # SSRF protection: every redirect hop is validated and the connection is
    # pinned to the validated IP (see safe_request).
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        try:
            response = await safe_request(
                client,
                "GET",
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Oniva/1.0)"},
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            # Remove scripts and styles
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            total = len(text)
            start = min(max(int(offset or 0), 0), total)
            chunk = text[start:start + MAX_CONTENT_CHARS]
            end = start + len(chunk)
            result: Dict[str, Any] = {
                "url": url,
                "status": response.status_code,
                "title": soup.title.string if soup.title else None,
                "content": chunk,
                "content_type": "text",
                "total_chars": total,
                "offset": start,
                "truncated": end < total,
            }
            if end < total:
                result["next_offset"] = end
                result["note"] = (
                    f"Showing characters {start}-{end} of {total}. "
                    f"Call browse_url again with offset={end} to read the next part."
                )
            return result
        except SecurityError as e:
            return {"url": url, "error": str(e), "status": "blocked"}
        except Exception as e:
            return {
                "url": url,
                "error": str(e),
                "status": "failed",
            }
