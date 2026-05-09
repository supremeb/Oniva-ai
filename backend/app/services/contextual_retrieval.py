"""
Contextual Retrieval — Prepend document context to each chunk for improved search.

Based on Anthropic's Contextual Retrieval research:
https://www.anthropic.com/news/contextual-retrieval

Uses Claude Haiku with prompt caching to generate a short context prefix for
each chunk, reducing retrieval failures by ~49%.

Gated by USE_SEMANTIC_CHUNKING flag. Error handling contract:
- Rate limit: retry with backoff (respect Retry-After)
- API error / timeout: 1 retry with 2s delay, then fallback to uncontextualized chunk
- Malformed response: use chunk without context, log warning
- Entire doc failure: store all chunks uncontextualized, set status to "ready"
"""

import asyncio
import logging
from typing import Optional

import litellm

from app.config import get_settings
from app.services.chunking import ChunkResult

logger = logging.getLogger(__name__)
settings = get_settings()

# Maximum tokens for document context in prompt caching
MAX_DOC_TOKENS = 180_000

CONTEXT_PROMPT_TEMPLATE = """\
<document>
{document_text}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk_text}
</chunk>

Please give a short succinct context to situate this chunk within the overall document \
for the purposes of improving search retrieval of the chunk. Answer only with the context, \
nothing else."""


async def _generate_single_context(
    document_text: str,
    chunk_text: str,
    model: str,
    is_first_chunk: bool,
) -> Optional[str]:
    """Generate context for a single chunk using Claude with prompt caching."""
    try:
        # Build messages with cache_control for the document
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": CONTEXT_PROMPT_TEMPLATE.format(
                            document_text=document_text,
                            chunk_text=chunk_text,
                        ),
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            },
        ]

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            max_tokens=150,
            temperature=0,
            api_key=settings.ANTHROPIC_API_KEY,
        )

        content = response.choices[0].message.content
        if not content or not content.strip():
            logger.warning("Empty context response from LLM")
            return None

        return content.strip()

    except Exception as e:
        # Check for rate limit with Retry-After
        if hasattr(e, "status_code") and getattr(e, "status_code", None) == 429:
            retry_after = getattr(e, "headers", {}).get("Retry-After", "2")
            try:
                wait_time = float(retry_after)
            except (ValueError, TypeError):
                wait_time = 2.0
            logger.info(f"Rate limited, waiting {wait_time}s before retry")
            await asyncio.sleep(wait_time)

            # Retry once
            try:
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": CONTEXT_PROMPT_TEMPLATE.format(
                                    document_text=document_text,
                                    chunk_text=chunk_text,
                                ),
                                "cache_control": {"type": "ephemeral"},
                            },
                        ],
                    },
                ]
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    max_tokens=150,
                    temperature=0,
                    api_key=settings.ANTHROPIC_API_KEY,
                )
                content = response.choices[0].message.content
                return content.strip() if content else None
            except Exception as retry_e:
                logger.warning(f"Context generation retry failed: {retry_e}")
                return None

        logger.warning(f"Context generation failed for chunk: {type(e).__name__}: {e}")
        return None


async def generate_chunk_contexts(
    document_text: str,
    chunks: list[ChunkResult],
    model: str | None = None,
    concurrency: int = 5,
) -> list[str]:
    """Generate contextual prefixes for a list of chunks.

    Args:
        document_text: Full document text (used as context for prompt caching)
        chunks: Chunks to generate context for
        model: LLM model to use (defaults to settings.CONTEXTUAL_RETRIEVAL_MODEL)
        concurrency: Max concurrent LLM calls

    Returns:
        List of contextualized texts (context + chunk content).
        On failure, returns the original chunk content unchanged.
    """
    if model is None:
        model = settings.CONTEXTUAL_RETRIEVAL_MODEL

    if not chunks:
        return []

    # Guard for very large documents: truncate to ~MAX_DOC_TOKENS
    # Rough estimate: 4 chars per token
    max_chars = MAX_DOC_TOKENS * 4
    if len(document_text) > max_chars:
        logger.warning(
            f"Document too large ({len(document_text)} chars), truncating to {max_chars} chars"
        )
        document_text = document_text[:max_chars]

    semaphore = asyncio.Semaphore(concurrency)
    results: list[str] = [""] * len(chunks)

    async def process_chunk(index: int, chunk: ChunkResult) -> None:
        async with semaphore:
            context = await _generate_single_context(
                document_text=document_text,
                chunk_text=chunk.content,
                model=model,
                is_first_chunk=(index == 0),
            )

            if context:
                # Prepend context to chunk content
                results[index] = f"{context}\n\n{chunk.content}"
            else:
                # Fallback: use chunk without context
                results[index] = chunk.content

    tasks = [process_chunk(i, chunk) for i, chunk in enumerate(chunks)]
    await asyncio.gather(*tasks)

    return results
