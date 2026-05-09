"""Rebuild pgvector HNSW indexes with parallel maintenance workers.

Usage:
    python backend/scripts/optimize_hnsw_indexes.py [--maintenance-work-mem 2GB]

Requires DATABASE_URL env var. Safe to run during live traffic.
If a reindex fails, drop the invalid _ccnew index and retry.
For dev environments, use --maintenance-work-mem 512MB.
"""
import argparse
import logging
import os
import re
import sys

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

INDEXES = [
    "idx_documents_embedding_hnsw",
    "idx_episodic_memories_embedding",
    "ix_agent_learnings_embedding_hnsw",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild pgvector HNSW indexes")
    parser.add_argument("--maintenance-work-mem", default="2GB")
    args = parser.parse_args()

    if not re.match(r'^\d+[KMGT]B$', args.maintenance_work_mem, re.IGNORECASE):
        logger.error(f"Invalid maintenance_work_mem format: {args.maintenance_work_mem}")
        sys.exit(1)

    database_url = os.environ["DATABASE_URL"].replace("+asyncpg", "")

    conn = psycopg2.connect(database_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET max_parallel_maintenance_workers = 7")
            cur.execute(f"SET maintenance_work_mem = '{args.maintenance_work_mem}'")

            for idx in INDEXES:
                logger.info(f"Rebuilding {idx}...")
                cur.execute(f"REINDEX INDEX CONCURRENTLY {idx}")
                logger.info(f"  Done: {idx}")

            # Verify
            cur.execute("""
                SELECT indexrelid::regclass, indisvalid
                FROM pg_index
                WHERE indexrelid::regclass::text = ANY(%s)
            """, (INDEXES,))
            for name, valid in cur.fetchall():
                status = "OK" if valid else "INVALID"
                logger.info(f"  {name}: {status}")
                if not valid:
                    logger.error(f"Index {name} is invalid! Drop the _ccnew index and retry.")
                    sys.exit(1)
    finally:
        conn.close()

    logger.info("All indexes rebuilt successfully.")


if __name__ == "__main__":
    main()
