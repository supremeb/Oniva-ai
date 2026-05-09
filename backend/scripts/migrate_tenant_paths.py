"""
Migrate session/volume/artifact directories to tenant-scoped paths.

Run BEFORE deploying the new multi-tenant-aware code.

Usage:
    python -m scripts.migrate_tenant_paths --dry-run   # Preview moves
    python -m scripts.migrate_tenant_paths              # Execute migration

Creates a manifest at ./migration_manifest.json for rollback.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from sqlalchemy import text
from app.database import async_engine


async def lookup_tenant_ids() -> dict[str, str]:
    """Return {session_id: tenant_id} from workflow_sessions table."""
    mapping: dict[str, str] = {}
    async with async_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT id, tenant_id FROM workflow_sessions")
        )
        for row in rows:
            mapping[str(row[0])] = str(row[1])
    return mapping


def migrate_directory(
    base_path: Path,
    tenant_lookup: dict[str, str],
    fallback_tenant: str,
    dry_run: bool,
) -> list[dict]:
    """Move top-level subdirs into tenant-scoped subdirs.

    Returns a list of move records for the manifest.
    """
    moves: list[dict] = []
    if not base_path.exists():
        return moves

    for child in sorted(base_path.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        tenant_id = tenant_lookup.get(name, fallback_tenant)
        dest = base_path / tenant_id / name

        if dest.exists():
            print(f"  SKIP (dest exists): {child} -> {dest}")
            continue

        moves.append({
            "src": str(child),
            "dest": str(dest),
            "tenant_id": tenant_id,
        })

        if dry_run:
            print(f"  [DRY-RUN] {child} -> {dest}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(dest))
            print(f"  MOVED {child} -> {dest}")

    return moves


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    parser.add_argument(
        "--fallback-tenant", default="system",
        help="Tenant ID for directories not found in DB (default: system)",
    )
    args = parser.parse_args()

    session_base = Path(os.environ.get("SESSION_BASE_PATH", "./sessions"))
    volume_base = Path(os.environ.get("VOLUME_BASE_PATH", "./volumes"))
    artifact_base = Path(os.environ.get("ARTIFACT_BASE_PATH", "./artifacts"))

    print("Looking up tenant IDs from database...")
    tenant_lookup = asyncio.run(lookup_tenant_ids())
    print(f"  Found {len(tenant_lookup)} sessions in DB")

    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "moves": [],
    }

    for label, base in [
        ("sessions", session_base),
        ("volumes", volume_base),
        ("artifacts", artifact_base),
    ]:
        print(f"\nMigrating {label} ({base})...")
        moves = migrate_directory(base, tenant_lookup, args.fallback_tenant, args.dry_run)
        manifest["moves"].extend(moves)
        print(f"  {len(moves)} directories {'would be' if args.dry_run else ''} moved")

    manifest_path = Path("migration_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest written to {manifest_path}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
    else:
        print(f"\nMigration complete. {len(manifest['moves'])} directories moved.")


if __name__ == "__main__":
    main()
