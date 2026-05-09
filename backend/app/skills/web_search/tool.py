"""
Web Search Skill
Search the web using configured search provider with automatic fallback.
Priority: Tavily → Parallel → Exa → SerpAPI → Mock
"""

import logging
import os
from typing import Any, Dict, List, Optional

from app.agents.skills.context import SkillContext

logger = logging.getLogger(__name__)


def _is_auth_error(error_str: str) -> bool:
    """Check if an error string indicates an auth/payment failure."""
    markers = ("401", "402", "403", "Payment Required", "Unauthorized", "Forbidden")
    return any(m in error_str for m in markers)


async def web_search(
    ctx: SkillContext,
    query: str,
    num_results: int = 5,
) -> Dict[str, Any]:
    """
    Search the web for information.

    Args:
        ctx: Skill execution context
        query: Search query string
        num_results: Number of results to return (default: 5)

    Returns:
        Dict with query, results list, and source provider
    """
    # Domain restrictions are enforced server-side from context.specific_sites,
    # set by the user via the Web Search popover UI.
    include_domains: Optional[List[str]] = getattr(ctx, 'specific_sites', None)

    # Build ordered provider list
    providers: list[tuple[str, Any, str]] = []

    tavily_key = ctx.api_keys.get("tavily") or os.getenv("TAVILY_API_KEY")
    parallel_key = ctx.api_keys.get("parallelai") or os.getenv("PARALLEL_API_KEY")
    exa_key = ctx.api_keys.get("exa") or os.getenv("EXA_API_KEY")
    serp_key = ctx.api_keys.get("serpapi") or os.getenv("SERP_API_KEY")

    if tavily_key:
        providers.append(("tavily", _search_tavily, tavily_key))
    if parallel_key:
        providers.append(("parallel", _search_parallel, parallel_key))
    if exa_key:
        providers.append(("exa", _search_exa, exa_key))
    if serp_key:
        providers.append(("serpapi", _search_serpapi, serp_key))

    # Try each provider; fall through on auth/payment errors
    for name, search_fn, key in providers:
        result = await search_fn(query, num_results, key, include_domains=include_domains)
        if result.get("results") or not _is_auth_error(result.get("error", "")):
            return result
        logger.warning("web_search provider %s failed: %s", name, result.get("error"))

    # No providers configured or all failed — return structured error (not mock data)
    if not providers:
        logger.warning("web_search: no search providers configured")
        return {
            "error": "No web search provider configured. Configure Tavily, Exa, or SerpAPI in Settings > API Keys.",
            "results": [],
            "source": "none",
        }
    return {
        "error": f"All {len(providers)} search providers failed. Check API key validity in Settings > API Keys.",
        "results": [],
        "source": "none",
    }


async def _search_tavily(
    query: str,
    num_results: int,
    api_key: str,
    include_domains: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search using Tavily API."""
    try:
        import httpx

        # Tavily supports include_domains natively
        search_query = query
        if include_domains:
            # Fallback: prefix query with site: operators
            site_prefix = " OR ".join(f"site:{d}" for d in include_domains)
            search_query = f"{site_prefix} {query}"

        async with httpx.AsyncClient() as client:
            payload: Dict[str, Any] = {
                "api_key": api_key,
                "query": search_query,
                "max_results": num_results,
                "include_answer": False,
            }
            if include_domains:
                payload["include_domains"] = include_domains

            response = await client.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:500],
                }
                for r in data.get("results", [])[:num_results]
            ]

            return {
                "query": query,
                "results": results,
                "source": "tavily",
            }
    except Exception as e:
        return {
            "query": query,
            "results": [],
            "source": "tavily",
            "error": str(e),
        }


async def _search_parallel(
    query: str,
    num_results: int,
    api_key: str,
    include_domains: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search using Parallel AI API."""
    try:
        import httpx

        payload: Dict[str, Any] = {
            "objective": query,
            "max_results": num_results,
            "excerpts": {"max_chars_per_result": 500},
        }
        if include_domains:
            payload["source_policy"] = {"include_domains": include_domains}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.parallel.ai/v1beta/search",
                headers={
                    "x-api-key": api_key,
                    "parallel-beta": "search-extract-2025-10-10",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (
                        r.get("excerpts", [""])[0] if r.get("excerpts") else ""
                    )[:500],
                }
                for r in data.get("results", [])[:num_results]
            ]

            return {
                "query": query,
                "results": results,
                "source": "parallel",
            }
    except Exception as e:
        return {
            "query": query,
            "results": [],
            "source": "parallel",
            "error": str(e),
        }


async def _search_exa(
    query: str,
    num_results: int,
    api_key: str,
    include_domains: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search using Exa AI API."""
    try:
        import httpx

        payload: Dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "type": "auto",
            "contents": {
                "text": {"maxCharacters": 1000},
                "highlights": True,
            },
        }
        # Exa natively supports includeDomains
        if include_domains:
            payload["includeDomains"] = include_domains

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (
                        r.get("text", "")
                        or (r.get("highlights", [""])[0] if r.get("highlights") else "")
                    )[:500],
                }
                for r in data.get("results", [])[:num_results]
            ]

            return {
                "query": query,
                "results": results,
                "source": "exa",
            }
    except Exception as e:
        return {
            "query": query,
            "results": [],
            "source": "exa",
            "error": str(e),
        }


async def _search_serpapi(
    query: str,
    num_results: int,
    api_key: str,
    include_domains: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Search using SerpAPI."""
    try:
        import httpx

        # SerpAPI doesn't support domain filtering natively — use site: prefix
        search_query = query
        if include_domains:
            site_prefix = " OR ".join(f"site:{d}" for d in include_domains)
            search_query = f"{site_prefix} {query}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://serpapi.com/search",
                params={
                    "api_key": api_key,
                    "q": search_query,
                    "num": num_results,
                    "engine": "google",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in data.get("organic_results", [])[:num_results]
            ]

            return {
                "query": query,
                "results": results,
                "source": "serpapi",
            }
    except Exception as e:
        return {
            "query": query,
            "results": [],
            "source": "serpapi",
            "error": str(e),
        }


def _mock_search_results(query: str, num_results: int) -> Dict[str, Any]:
    """Return empty results with a clear error when no search API key is configured."""
    return {
        "query": query,
        "results": [],
        "source": "mock",
        "error": (
            "No web search provider configured. "
            "Add a Tavily, Parallel, Exa, or SerpAPI key in Settings > API Keys "
            "to enable real web search."
        ),
    }
