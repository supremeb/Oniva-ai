"""
Chunking Service — Semantic text chunking using Chonkie SemanticChunker.

When USE_SEMANTIC_CHUNKING=True:
  Uses potion-base-32M model for semantic boundary detection.
  Runs in a dedicated ThreadPoolExecutor to avoid GIL contention.

When USE_SEMANTIC_CHUNKING=False:
  Falls back to naive character-based chunking at ~512 tokens.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Dedicated thread pool for CPU-bound chunking (isolates from default pool)
_chunking_executor: Optional[ThreadPoolExecutor] = None

# Singleton chunker instance (loaded once at startup via init_chunker)
_semantic_chunker = None


@dataclass
class ChunkResult:
    """Result from chunking a text."""
    content: str
    token_count: int
    start_index: int
    end_index: int


def init_chunker() -> None:
    """Load the potion-base-32M model and initialize the SemanticChunker.

    Called in FastAPI lifespan handler. Only runs if USE_SEMANTIC_CHUNKING=True.
    """
    global _semantic_chunker, _chunking_executor

    if not settings.USE_SEMANTIC_CHUNKING:
        logger.info("Semantic chunking disabled (USE_SEMANTIC_CHUNKING=False)")
        return

    import time
    start = time.monotonic()

    try:
        from chonkie import SemanticChunker

        _semantic_chunker = SemanticChunker(
            embedding_model="minishlab/potion-base-32M",
            chunk_size=512,
            similarity_threshold=0.7,
            similarity_window=3,
        )
        _chunking_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="chunker",
        )

        elapsed = time.monotonic() - start
        logger.info(f"SemanticChunker initialized in {elapsed:.2f}s (potion-base-32M)")
    except Exception as e:
        logger.error(f"Failed to initialize SemanticChunker: {e}")
        _semantic_chunker = None


def _run_semantic_chunking(text: str) -> list[ChunkResult]:
    """Run semantic chunking synchronously (called in thread pool)."""
    if _semantic_chunker is None:
        raise RuntimeError("SemanticChunker not initialized. Call init_chunker() first.")

    chunks = _semantic_chunker.chunk(text)

    results = []
    for chunk in chunks:
        results.append(ChunkResult(
            content=chunk.text,
            token_count=chunk.token_count,
            start_index=chunk.start_index,
            end_index=chunk.end_index,
        ))
    return results


def _run_naive_chunking(text: str, chunk_size_chars: int = 2048, overlap_chars: int = 200) -> list[ChunkResult]:
    """Naive character-based chunking as fallback (512 tokens ~ 2048 chars)."""
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size_chars, text_len)

        # Try to break at sentence/paragraph boundary
        if end < text_len:
            newline_pos = text.rfind("\n\n", start + chunk_size_chars // 2, end)
            if newline_pos > start:
                end = newline_pos
            else:
                for sep in [". ", ".\n", "! ", "? "]:
                    sep_pos = text.rfind(sep, start + chunk_size_chars // 2, end)
                    if sep_pos > start:
                        end = sep_pos + len(sep)
                        break

        chunk_text = text[start:end].strip()
        if chunk_text:
            token_estimate = len(chunk_text) // 4
            chunks.append(ChunkResult(
                content=chunk_text,
                token_count=token_estimate,
                start_index=start,
                end_index=end,
            ))

        start = end - overlap_chars if end < text_len else text_len

    return chunks


async def chunk_text(text: str) -> list[ChunkResult]:
    """Chunk text using semantic or naive chunking based on config.

    When USE_SEMANTIC_CHUNKING=True: runs SemanticChunker in dedicated thread pool.
    When USE_SEMANTIC_CHUNKING=False: uses naive character-based chunking.
    """
    if not text.strip():
        return []

    if settings.USE_SEMANTIC_CHUNKING and _semantic_chunker is not None:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                _chunking_executor,
                _run_semantic_chunking,
                text,
            )
        except Exception as e:
            logger.warning(f"Semantic chunking failed, falling back to naive: {e}")
            return _run_naive_chunking(text)
    else:
        return _run_naive_chunking(text)
