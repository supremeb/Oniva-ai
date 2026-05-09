"""
Summarization Service - LLM-based document summarization
Implements anchored iterative summarization for long documents.
"""

from typing import List, Optional
from pydantic import BaseModel
from pydantic_ai import Agent
import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class DocumentSection(BaseModel):
    """A section of a document with optional header."""
    header: Optional[str] = None
    content: str


class SummarizationService:
    """
    LLM-based document summarization with anchored iterative approach.

    For short documents (<1000 words): Single-pass summarization
    For long documents: Extract sections, summarize each, then consolidate
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.SUMMARIZATION_MODEL
        self.chunk_size = 4000  # tokens per chunk
        self.max_summary_tokens = 500

    async def summarize(
        self,
        text: str,
        max_length: int = 500,
        style: str = "concise",
        preserve_structure: bool = True,
    ) -> str:
        """
        Summarize text using anchored iterative summarization.

        Args:
            text: Text to summarize
            max_length: Maximum summary length in words
            style: "concise", "detailed", or "bullet"
            preserve_structure: Keep section headers if present

        Returns:
            Summarized text
        """
        word_count = len(text.split())

        # Short text - summarize directly
        if word_count < 1000:
            return await self._single_pass_summarize(text, max_length, style)

        # Long text - anchored iterative summarization
        return await self._iterative_summarize(text, max_length, style, preserve_structure)

    async def _single_pass_summarize(
        self,
        text: str,
        max_length: int,
        style: str,
    ) -> str:
        """Single-pass summarization for shorter texts."""
        style_instructions = {
            "concise": "Be extremely concise. Focus on key facts only.",
            "detailed": "Include important details and nuances.",
            "bullet": "Use bullet points for main ideas.",
        }

        agent = Agent(
            self.model,
            system_prompt=(
                f"Summarize the following text in {max_length} words or less. "
                f"{style_instructions.get(style, '')} "
                "Preserve factual accuracy. Do not add information not in the original."
            ),
        )

        result = await agent.run(text)
        return result.output

    async def _iterative_summarize(
        self,
        text: str,
        max_length: int,
        style: str,
        preserve_structure: bool,
    ) -> str:
        """
        Anchored iterative summarization for long documents.

        Process:
        1. Extract document structure (headers, sections)
        2. Summarize each section independently
        3. Combine section summaries with structure anchors
        4. Final consolidation pass
        """
        # Extract sections
        sections = self._extract_sections(text)

        # Calculate per-section length based on total sections
        per_section_length = max(100, max_length // max(len(sections), 1))

        # Summarize each section
        section_summaries = []
        for section in sections:
            try:
                summary = await self._single_pass_summarize(
                    section.content,
                    max_length=per_section_length,
                    style=style,
                )
                if preserve_structure and section.header:
                    summary = f"**{section.header}**: {summary}"
                section_summaries.append(summary)
            except Exception as e:
                logger.warning(f"Section summarization failed: {e}")
                # Fall back to truncation for this section
                truncated = section.content[:per_section_length * 5]
                if preserve_structure and section.header:
                    truncated = f"**{section.header}**: {truncated}"
                section_summaries.append(truncated)

        # Combine summaries
        combined = "\n\n".join(section_summaries)

        # If still too long, do final consolidation
        if len(combined.split()) > max_length * 1.5:
            combined = await self._single_pass_summarize(combined, max_length, style)

        return combined

    def _extract_sections(self, text: str) -> List[DocumentSection]:
        """Extract sections from text based on headers or paragraphs."""
        sections = []

        # Try to find markdown headers
        header_pattern = r'^(#{1,3})\s+(.+)$'
        lines = text.split('\n')

        current_section = DocumentSection(header=None, content="")
        content_lines = []

        for line in lines:
            header_match = re.match(header_pattern, line, re.MULTILINE)
            if header_match:
                # Save previous section
                if content_lines:
                    current_section.content = '\n'.join(content_lines).strip()
                    if current_section.content:
                        sections.append(current_section)
                # Start new section
                current_section = DocumentSection(
                    header=header_match.group(2),
                    content=""
                )
                content_lines = []
            else:
                content_lines.append(line)

        # Save last section
        if content_lines:
            current_section.content = '\n'.join(content_lines).strip()
            if current_section.content:
                sections.append(current_section)

        # If no headers found or only one section, split by paragraphs
        if len(sections) <= 1:
            paragraphs = text.split('\n\n')
            sections = []
            for p in paragraphs:
                stripped = p.strip()
                if stripped:
                    sections.append(DocumentSection(header=None, content=stripped))

        # If still too few sections, split by word count
        if len(sections) <= 1 and len(text.split()) > 2000:
            words = text.split()
            chunk_size = 500  # words per chunk
            sections = []
            for i in range(0, len(words), chunk_size):
                chunk = ' '.join(words[i:i + chunk_size])
                sections.append(DocumentSection(header=None, content=chunk))

        return sections if sections else [DocumentSection(header=None, content=text)]


# Singleton instance
_summarization_service: Optional[SummarizationService] = None


def get_summarization_service() -> SummarizationService:
    """Get or create the singleton SummarizationService."""
    global _summarization_service
    if _summarization_service is None:
        _summarization_service = SummarizationService()
    return _summarization_service
