from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.agents.base import BaseAgent, AgentDependencies
from app.agents.tool_results import (
    ResearchVolumeFileResult, ResearchReportResult,
    DocumentSearchItem, DocumentDetailResult,
)
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_chunk import KnowledgeChunk

logger = logging.getLogger(__name__)


class ResearchResult(BaseModel):
    """Result from research agent"""
    answer: str
    sources: List[str] = []
    confidence: float = Field(ge=0, le=1)
    relevant_documents: List[Dict[str, Any]] = []
    artifact_uri: Optional[str] = None  # URI to saved research report


class ResearchAgent(BaseAgent):
    """
    Agent for answering questions using RAG (Retrieval-Augmented Generation).
    Searches documents with pgvector and provides cited answers.

    Storage Pattern:
    - Reads reference documents from mounted volumes (research_corpus, sec_filings)
    - Saves research reports as artifacts for future reference
    - Logs search queries and results to session history
    """

    def __init__(self):
        super().__init__(
            name="Research Agent",
            description="Answers questions using document search (RAG)",
            output_type=ResearchResult,
            system_prompt="""You are a research specialist with access to a document database.

When answering questions:
1. Use the search_documents tool to find relevant information
2. Cite your sources (document titles/URLs)
3. Synthesize information from multiple documents when relevant
4. Be clear about confidence level based on source quality and relevance
5. If information is not in the documents, say so clearly

Always provide citations and indicate when you're uncertain.""",
            model="openai/gpt-4o",  # Good for RAG
        )

        @self.agent.tool
        async def read_from_volume(
            ctx: RunContext[AgentDependencies],
            volume_name: str,
            file_path: str
        ) -> ResearchVolumeFileResult:
            """
            Read a reference document from a mounted volume.

            Args:
                volume_name: Name of the volume (e.g., 'research_corpus', 'sec_filings')
                file_path: Path to file within the volume

            Returns:
                File content or error
            """
            from app.services.volumes import get_volume_manager
            from app.config import get_settings

            settings = get_settings()
            if not getattr(settings, 'USE_VOLUMES', False):
                return {"error": "Volumes feature not enabled"}

            try:
                volume_mgr = get_volume_manager()
                content = await volume_mgr.read_file(str(ctx.deps.tenant_id), volume_name, file_path)
                return {
                    "success": True,
                    "volume": volume_name,
                    "file_path": file_path,
                    "content": content.decode('utf-8') if isinstance(content, bytes) else content,
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        @self.agent.tool
        async def save_research_report(
            ctx: RunContext[AgentDependencies],
            report_title: str,
            report_content: str,
            sources: List[str]
        ) -> ResearchReportResult:
            """
            Save a research report as an artifact.

            Args:
                report_title: Title of the research report
                report_content: Full markdown content of the report
                sources: List of source citations

            Returns:
                Storage URI or error
            """
            from app.services.artifacts import get_artifact_storage
            from app.config import get_settings
            from datetime import datetime

            settings = get_settings()
            if not getattr(settings, 'USE_ARTIFACTS', False):
                return {"warning": "Artifacts feature not enabled, report not saved"}

            session_id = ctx.deps.session_id
            if not session_id:
                return {"error": "No session_id available to save artifact"}

            try:
                artifact_storage = get_artifact_storage()
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in report_title[:50])
                file_path = f"research/{safe_title}_{timestamp}.md"

                # Add sources section to report
                full_report = f"# {report_title}\n\n{report_content}\n\n## Sources\n"
                for i, source in enumerate(sources, 1):
                    full_report += f"{i}. {source}\n"

                uri = await artifact_storage.write(
                    tenant_id=str(ctx.deps.tenant_id),
                    session_id=session_id,
                    file_path=file_path,
                    content=full_report.encode('utf-8'),
                    metadata={
                        "user_id": str(ctx.deps.user_id),
                        "agent_id": "research_agent",
                        "title": report_title,
                        "source_count": len(sources),
                    },
                    content_type="text/markdown",
                )

                return {
                    "success": True,
                    "artifact_uri": uri,
                    "file_path": file_path,
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        @self.agent.tool
        async def search_documents(
            ctx: RunContext[AgentDependencies],
            query: str,
            limit: int = 5
        ) -> List[DocumentSearchItem]:
            """
            Search the knowledge base using hybrid search (vector + keyword via RRF).

            Args:
                query: Search query
                limit: Maximum number of results

            Returns:
                List of relevant document chunks with relevance scores
            """
            from uuid import UUID
            from app.services.rag_service import RAGService

            db: AsyncSession = ctx.deps.db

            try:
                rag_service = RAGService(db)
                results = await rag_service.hybrid_search_v2(
                    query=query,
                    tenant_id=UUID(str(ctx.deps.tenant_id)),
                    limit=limit,
                )

                return [
                    {
                        "id": r.get("document_id", r.get("id", "")),
                        "title": r.get("document_name") or "Untitled",
                        "content": r["content"][:500] + "..." if len(r.get("content", "")) > 500 else r.get("content", ""),
                        "source": r.get("document_name") or r.get("document_id", ""),
                        "relevance_score": r.get("score", 0.0),
                    }
                    for r in results
                ]

            except Exception as e:
                logger.error(f"Knowledge base search failed: {e}")
                return []

        @self.agent.tool
        async def get_document_by_id(
            ctx: RunContext[AgentDependencies],
            document_id: str
        ) -> DocumentDetailResult:
            """
            Get full document content by ID.
            Returns document metadata and concatenated chunk content.

            Args:
                document_id: Document UUID (from search_documents results)

            Returns:
                Full document details with content from all chunks
            """
            db: AsyncSession = ctx.deps.db
            tenant_id = ctx.deps.tenant_id

            # Query knowledge_documents with tenant isolation
            stmt = select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.tenant_id == tenant_id,
            )
            result = await db.execute(stmt)
            doc = result.scalar_one_or_none()

            if not doc:
                return {"error": "Document not found"}

            # Fetch all chunks for this document, ordered by chunk_index
            chunk_stmt = (
                select(KnowledgeChunk.content, KnowledgeChunk.chunk_index)
                .where(KnowledgeChunk.document_id == doc.id)
                .order_by(KnowledgeChunk.chunk_index)
            )
            chunk_result = await db.execute(chunk_stmt)
            chunks = chunk_result.all()
            full_content = "\n\n".join(c.content for c in chunks) if chunks else "(no content)"

            return {
                "id": str(doc.id),
                "title": doc.filename,
                "content": full_content,
                "source": doc.filename,
                "metadata": {"file_type": doc.file_type, "version": doc.version, "chunk_count": doc.chunk_count},
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            }


# Create singleton instance
research_agent = ResearchAgent()
