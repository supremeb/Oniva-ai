"""Re-index existing knowledge documents with semantic chunking + contextual retrieval.

Usage:
    python backend/scripts/reindex_knowledge_chunks.py [--kb-id UUID] [--dry-run] [--concurrency 3]

Requires DATABASE_URL env var. Processes documents one at a time, committing
after each to avoid long transactions. Safe to interrupt and resume.

Only re-processes documents in "ready" or "error" status.
"""
import argparse
import asyncio
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


async def reindex(kb_id: str | None = None, dry_run: bool = False, concurrency: int = 3) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from app.config import get_settings
    from app.models.knowledge_document import KnowledgeDocument
    from app.services.document_processor import DocumentProcessorService
    from app.services.chunking import init_chunker

    settings = get_settings()

    # Initialize semantic chunker if enabled
    init_chunker()

    engine = create_async_engine(settings.DATABASE_URL, pool_size=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        # Build query for documents to re-process
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.status.in_(["ready", "error"])
        ).order_by(KnowledgeDocument.created_at)

        if kb_id:
            from uuid import UUID
            stmt = stmt.where(KnowledgeDocument.knowledge_base_id == UUID(kb_id))

        result = await db.execute(stmt)
        docs = result.scalars().all()

        logger.info(f"Found {len(docs)} documents to re-index")
        if dry_run:
            for doc in docs:
                logger.info(f"  [DRY RUN] Would re-index: {doc.filename} (id={doc.id}, status={doc.status})")
            return

        # Process documents with limited concurrency
        sem = asyncio.Semaphore(concurrency)
        success = 0
        failed = 0

        async def process_one(doc: KnowledgeDocument) -> bool:
            async with sem:
                async with session_factory() as doc_db:
                    try:
                        processor = DocumentProcessorService(doc_db)
                        await processor.process_document(doc.id, doc.tenant_id)
                        await doc_db.commit()
                        logger.info(f"  Re-indexed: {doc.filename} (id={doc.id})")
                        return True
                    except Exception as e:
                        await doc_db.rollback()
                        logger.error(f"  Failed: {doc.filename} (id={doc.id}): {e}")
                        return False

        results = await asyncio.gather(*[process_one(doc) for doc in docs])
        success = sum(1 for r in results if r)
        failed = sum(1 for r in results if not r)

        logger.info(f"Re-indexing complete: {success} succeeded, {failed} failed out of {len(docs)}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-index knowledge documents with new chunking pipeline")
    parser.add_argument("--kb-id", type=str, default=None, help="Only re-index documents in this knowledge base")
    parser.add_argument("--dry-run", action="store_true", help="List documents without processing")
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent document processing (default: 3)")
    args = parser.parse_args()

    asyncio.run(reindex(kb_id=args.kb_id, dry_run=args.dry_run, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
