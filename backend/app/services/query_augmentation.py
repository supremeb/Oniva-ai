"""
Query Augmentation Service for RAG
Implements HyDE, multi-query generation, and query rewriting.
"""

from typing import List, Optional
from pydantic import BaseModel
from pydantic_ai import Agent
import logging
import asyncio

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AugmentedQuery(BaseModel):
    """Result of query augmentation."""
    original: str
    hypothetical_answer: Optional[str] = None  # HyDE
    query_variations: List[str] = []            # Multi-query
    rewritten_query: Optional[str] = None       # Rewritten


class QueryAugmentationService:
    """
    Service for intelligent query preprocessing.

    Implements:
    - HyDE (Hypothetical Document Embeddings): Generate a hypothetical document
      that would answer the query, then embed that for retrieval.
    - Multi-query: Generate variations of the query to retrieve diverse results.
    - Query rewriting: Expand abbreviations and clarify ambiguous terms.
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.QUERY_AUGMENTATION_MODEL
        self._hyde_agent = None
        self._rewrite_agent = None

    async def augment(
        self,
        query: str,
        use_hyde: bool = True,
        use_multi_query: bool = True,
        use_rewrite: bool = False,
    ) -> AugmentedQuery:
        """
        Augment a query for better retrieval.

        Args:
            query: Original user query
            use_hyde: Generate hypothetical document embedding
            use_multi_query: Generate query variations
            use_rewrite: Rewrite query for clarity

        Returns:
            AugmentedQuery with all augmentations
        """
        result = AugmentedQuery(original=query)

        # Run HyDE and multi-query in parallel for latency
        tasks = []
        if use_hyde:
            tasks.append(("hyde", self._generate_hyde_safe(query)))
        if use_multi_query:
            tasks.append(("variations", self._generate_variations_safe(query)))
        if use_rewrite:
            tasks.append(("rewrite", self._rewrite_query_safe(query)))

        if tasks:
            task_names = [t[0] for t in tasks]
            task_coros = [t[1] for t in tasks]
            results = await asyncio.gather(*task_coros, return_exceptions=True)

            for name, res in zip(task_names, results):
                if isinstance(res, Exception):
                    logger.warning(f"{name} generation failed: {res}")
                    continue

                if name == "hyde" and res:
                    result.hypothetical_answer = res
                elif name == "variations" and res:
                    result.query_variations = res
                elif name == "rewrite" and res:
                    result.rewritten_query = res

        return result

    async def _generate_hyde_safe(self, query: str) -> Optional[str]:
        """Generate HyDE with timeout."""
        try:
            return await asyncio.wait_for(
                self._generate_hyde(query),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("HyDE generation timed out")
            return None
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}")
            return None

    async def _generate_variations_safe(self, query: str) -> List[str]:
        """Generate variations with timeout."""
        try:
            return await asyncio.wait_for(
                self._generate_variations(query),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Multi-query generation timed out")
            return []
        except Exception as e:
            logger.warning(f"Multi-query generation failed: {e}")
            return []

    async def _rewrite_query_safe(self, query: str) -> Optional[str]:
        """Rewrite query with timeout."""
        try:
            return await asyncio.wait_for(
                self._rewrite_query(query),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Query rewrite timed out")
            return None
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}")
            return None

    async def _generate_hyde(self, query: str) -> str:
        """
        Generate a hypothetical document that would answer the query.
        HyDE (Hypothetical Document Embeddings) improves retrieval
        by embedding an answer-like text instead of the question.
        """
        if not self._hyde_agent:
            self._hyde_agent = Agent(
                self.model,
                system_prompt=(
                    "You are a document generator. Given a question, write a "
                    "short paragraph that would be found in a document that "
                    "answers this question. Do not answer the question - write "
                    "as if you are the source document. Be factual and concise. "
                    "Write in a formal, informational tone as if excerpted from "
                    "an official document, report, or knowledge base."
                ),
            )

        result = await self._hyde_agent.run(query)
        return result.output

    async def _generate_variations(self, query: str, n: int = 3) -> List[str]:
        """
        Generate multiple variations of the query.
        Different phrasings help retrieve diverse relevant documents.
        """
        agent = Agent(
            self.model,
            system_prompt=(
                f"Generate {n} different ways to ask the following question. "
                "Each variation should capture the same intent but use different "
                "words and structure. Return only the variations, one per line. "
                "Do not number them or add any other text."
            ),
        )

        result = await agent.run(query)
        variations = [v.strip() for v in result.output.split('\n') if v.strip()]
        return variations[:n]

    async def _rewrite_query(self, query: str) -> str:
        """
        Rewrite query for better semantic matching.
        Expands abbreviations, clarifies ambiguity, adds context.
        """
        if not self._rewrite_agent:
            self._rewrite_agent = Agent(
                self.model,
                system_prompt=(
                    "Rewrite the following search query to be more specific and "
                    "semantically rich. Expand abbreviations, add relevant context "
                    "words, and clarify any ambiguous terms. Return only the "
                    "rewritten query without any explanation."
                ),
            )

        result = await self._rewrite_agent.run(query)
        return result.output


# Singleton instance
_query_augmentation_service: Optional[QueryAugmentationService] = None


def get_query_augmentation_service() -> QueryAugmentationService:
    """Get or create the singleton QueryAugmentationService."""
    global _query_augmentation_service
    if _query_augmentation_service is None:
        _query_augmentation_service = QueryAugmentationService()
    return _query_augmentation_service
