"""Backfill SHA-256 hashes for existing API keys.

Run once after deploying migration 066_add_api_key_hash_index.
Decrypts each key and stores its SHA-256 hash for O(1) lookup.

Usage:
    cd backend
    python -m scripts.backfill_api_key_hashes
"""
import asyncio
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def backfill():
    from app.config import settings
    from app.models.api_key import APIKey
    from app.core.encryption import get_encryption_service

    engine = create_async_engine(settings.DATABASE_URL)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    encryption = get_encryption_service()

    async with async_session() as db:
        query = select(APIKey).where(APIKey.key_hash.is_(None))
        result = await db.execute(query)
        keys = result.scalars().all()

        logger.info(f"Found {len(keys)} keys without hashes")

        backfilled = 0
        errors = 0

        for key in keys:
            try:
                plaintext = encryption.decrypt(key.encrypted_key)
                key.key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
                backfilled += 1
            except Exception as e:
                logger.warning(f"Could not decrypt key {key.id}: {e}")
                errors += 1

        await db.commit()
        logger.info(f"Backfilled {backfilled} keys, {errors} errors")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
