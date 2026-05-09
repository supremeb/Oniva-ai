"""
Browse URL Skill
Fetch and parse a web page using HTTP GET + BeautifulSoup.
Use for static HTML pages. For JavaScript-heavy sites, use browser_navigate instead.
"""

from typing import Any, Dict

import httpx
from bs4 import BeautifulSoup

from app.agents.skills.context import SkillContext
from app.security.url_validator import is_url_safe


async def browse_url(
    ctx: SkillContext,
    url: str,
    extract_text: bool = True,
) -> Dict[str, Any]:
    """
    Fetch webpage content.

    Args:
        ctx: Skill execution context
        url: URL to fetch
        extract_text: If True, extract text content; if False, return raw HTML

    Returns:
        Webpage content (text or HTML)
    """
    # SSRF protection: validate URL before fetching
    error = is_url_safe(url)
    if error:
        return {"url": url, "error": error, "status": "blocked"}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Oniva/1.0)"},
            )
            response.raise_for_status()

            html = response.text

            if extract_text:
                soup = BeautifulSoup(html, "html.parser")
                # Remove scripts and styles
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                # Limit text length
                text = text[:10000] if len(text) > 10000 else text
                return {
                    "url": url,
                    "status": response.status_code,
                    "title": soup.title.string if soup.title else None,
                    "content": text,
                    "content_type": "text",
                }
            else:
                return {
                    "url": url,
                    "status": response.status_code,
                    "content": html[:50000],  # Limit HTML size
                    "content_type": "html",
                }
        except Exception as e:
            return {
                "url": url,
                "error": str(e),
                "status": "failed",
            }
