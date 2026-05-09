"""Outbound webhook delivery for background agent results.

Simple POST with retry. No abstraction hierarchy — one function.
"""

import asyncio
import json
import logging

import httpx

logger = logging.getLogger(__name__)


async def deliver_webhook(
    url: str,
    payload: dict,
    headers: dict | None = None,
    max_retries: int = 3,
    timeout_seconds: int = 30,
) -> bool:
    """POST agent results to a configured webhook URL with retry.

    Args:
        url: Webhook endpoint URL
        payload: JSON payload to deliver
        headers: Optional HTTP headers (e.g., Authorization)
        max_retries: Number of retry attempts (default 3)
        timeout_seconds: Request timeout per attempt

    Returns:
        True if delivery succeeded, False if all retries exhausted
    """
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, json=payload, headers=all_headers)
                resp.raise_for_status()
                logger.info(f"Webhook delivered to {url}: status={resp.status_code}")
                return True
        except Exception as e:
            if attempt == max_retries:
                logger.error(
                    f"Webhook delivery failed after {max_retries} retries to {url}: {e}"
                )
                return False
            wait = 2 ** attempt
            logger.warning(
                f"Webhook delivery attempt {attempt + 1}/{max_retries} failed to {url}: {e}. "
                f"Retrying in {wait}s."
            )
            await asyncio.sleep(wait)

    return False
