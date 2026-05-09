"""
Volume Manager for Oniva Simplified Memory Architecture.

Volumes are pre-loaded, read-only datasets that can be mounted to agent sessions.
Use cases:
- Market data CSVs (updated nightly)
- SEC filings corpus (10-K, 10-Q documents)
- Company profiles and metadata
- Regulatory rule databases
- Historical transaction data

This module supports both S3 and local filesystem backends.

Volume Directory Structure (tenant-isolated):
    /volumes/{tenant_id}/{volume_name}/
    |-- <data files>

Multi-tenant isolation: all paths scoped by tenant_id (AD-1).
Shared volumes live under the "system" tenant and can be mounted by any tenant.
"""

import os
import shutil
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
from dataclasses import dataclass, field

import aiofiles
import aiofiles.os

from app.config import get_settings


@dataclass
class VolumeInfo:
    """Metadata about a volume."""
    name: str
    path: str
    size_bytes: int
    file_count: int
    created_at: Optional[datetime] = None
    source: Optional[str] = None
    is_mounted: bool = False


@dataclass
class MountInfo:
    """Information about a volume mount."""
    session_id: str
    volume_name: str
    mount_path: str
    mounted_at: datetime = field(default_factory=datetime.utcnow)


class VolumeManager:
    """
    Manages read-only volumes mounted to agent sessions.

    Volumes are pre-loaded data that multiple sessions can share.
    No promotion, no caching - just direct file access.

    Multi-tenant isolation: all volumes are stored under
    /volumes/{tenant_id}/{volume_name}/ and every public method
    requires a tenant_id parameter (AD-1).  Shared volumes live
    under the "system" tenant and can be mounted by any tenant.

    Usage:
        volume_mgr = VolumeManager()

        # Create a volume from local path
        await volume_mgr.create_volume("tenant_1", "market_data_2026", "/data/market_data")

        # Or create from S3
        await volume_mgr.create_volume("tenant_1", "sec_filings", "s3://oniva-data/sec_filings")

        # Mount to a session
        mount_path = await volume_mgr.mount_volume("tenant_1", "market_data_2026", "session_123")

        # Agent can now read files from mount_path
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        s3_client: Optional[object] = None,
    ):
        """
        Initialize VolumeManager.

        Args:
            base_path: Base directory for volumes. Defaults to settings.VOLUME_BASE_PATH
            s3_client: Optional boto3 S3 client for S3 sources
        """
        settings = get_settings()
        self.base_path = base_path or Path(
            getattr(settings, "VOLUME_BASE_PATH", "./volumes")
        )
        self.base_path = Path(self.base_path)
        self._s3_client = s3_client
        self._mounts: dict[str, list[MountInfo]] = {}  # session_id -> list of mounts

    @property
    def s3_client(self):
        """Lazy-load S3 client only when needed."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client("s3")
            except ImportError:
                raise ImportError(
                    "boto3 is required for S3 volume sources. "
                    "Install with: pip install boto3"
                )
        return self._s3_client

    @staticmethod
    def _sanitize(value: str, label: str) -> str:
        """Sanitize a path component to prevent path traversal.

        Args:
            value: Raw path component (e.g. tenant_id or volume_name).
            label: Human-readable label used in error messages.

        Returns:
            The validated value (unchanged if it passes).

        Raises:
            ValueError: If the value contains characters other than
                        alphanumeric, hyphen, or underscore, or is empty.
        """
        safe = "".join(c for c in value if c.isalnum() or c in "-_")
        if not safe or safe != value:
            raise ValueError(f"Invalid {label}: {value}")
        return safe

    def _get_volume_path(self, tenant_id: str, volume_name: str) -> Path:
        """Get the filesystem path for a volume, scoped by tenant.

        Args:
            tenant_id: Tenant identifier (sanitized).
            volume_name: Volume identifier (sanitized).

        Returns:
            Path of the form ``<base_path>/<tenant_id>/<volume_name>``.
        """
        safe_tenant = self._sanitize(tenant_id, "tenant_id")
        safe_name = self._sanitize(volume_name, "volume_name")
        return self.base_path / safe_tenant / safe_name

    def _validate_path(self, volume_path: Path, file_path: str) -> Path:
        """
        Validate that file_path stays within volume_path.

        Prevents path traversal attacks including:
        - Directory traversal (../)
        - Null byte injection
        - Symlink escape

        Args:
            volume_path: The volume's base directory (must exist)
            file_path: User-provided relative path

        Returns:
            Resolved absolute path that is verified to be within volume_path

        Raises:
            ValueError: If path traversal is detected
        """
        # Check for null bytes
        if '\x00' in file_path:
            raise ValueError("Invalid path: null byte detected")

        # Resolve the volume path first (handles any symlinks in base path)
        resolved_volume = volume_path.resolve()

        # Construct and resolve the full path
        # Using resolve(strict=False) to handle non-existent paths during validation
        try:
            full_path = (volume_path / file_path).resolve()
        except (OSError, ValueError) as e:
            raise ValueError(f"Invalid path: {e}")

        # Verify the resolved path starts with the resolved volume path
        try:
            full_path.relative_to(resolved_volume)
        except ValueError:
            raise ValueError("Path traversal detected: path escapes volume boundary")

        return full_path

    async def create_volume(
        self,
        tenant_id: str,
        volume_name: str,
        source: str,
        overwrite: bool = False,
    ) -> VolumeInfo:
        """
        Create a new volume from S3 or local filesystem.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Unique volume identifier (alphanumeric, hyphens, underscores)
            source: Data source - S3 URI (s3://bucket/key) or local path
            overwrite: If True, overwrite existing volume

        Returns:
            VolumeInfo with volume metadata

        Raises:
            ValueError: If volume exists and overwrite=False, or invalid source
        """
        volume_path = self._get_volume_path(tenant_id, volume_name)

        # Check if volume exists
        if volume_path.exists():
            if not overwrite:
                raise ValueError(
                    f"Volume '{volume_name}' already exists. "
                    "Use overwrite=True to replace."
                )
            # Remove existing volume
            await asyncio.to_thread(shutil.rmtree, volume_path)

        # Create volume directory
        await aiofiles.os.makedirs(volume_path, exist_ok=True)

        # Copy data from source
        if source.startswith("s3://"):
            await self._download_from_s3(source, volume_path)
        else:
            await self._copy_from_local(source, volume_path)

        # Get volume info
        return await self.get_volume(tenant_id, volume_name)

    async def _download_from_s3(self, s3_uri: str, dest_path: Path) -> None:
        """
        Download files from S3 to local volume directory.

        Args:
            s3_uri: S3 URI in format s3://bucket/prefix
            dest_path: Local destination path
        """
        # Parse S3 URI
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {s3_uri}")

        parts = s3_uri[5:].split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

        # List and download objects
        paginator = self.s3_client.get_paginator("list_objects_v2")

        async def download_object(obj_key: str) -> None:
            """Download a single object."""
            # Calculate relative path
            relative_path = obj_key[len(prefix):].lstrip("/") if prefix else obj_key
            local_path = dest_path / relative_path

            # Create parent directories
            await aiofiles.os.makedirs(local_path.parent, exist_ok=True)

            # Download file
            await asyncio.to_thread(
                self.s3_client.download_file,
                bucket,
                obj_key,
                str(local_path),
            )

        # Iterate through pages and download files
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                obj_key = obj["Key"]
                # Skip "directory" markers
                if not obj_key.endswith("/"):
                    await download_object(obj_key)

    async def _copy_from_local(self, source_path: str, dest_path: Path) -> None:
        """
        Copy files from local filesystem to volume directory.

        Args:
            source_path: Source directory path
            dest_path: Destination volume path
        """
        source = Path(source_path)

        if not source.exists():
            raise ValueError(f"Source path does not exist: {source_path}")

        if source.is_file():
            # Copy single file
            await asyncio.to_thread(
                shutil.copy2,
                source,
                dest_path / source.name,
            )
        else:
            # Copy directory contents
            await asyncio.to_thread(
                shutil.copytree,
                source,
                dest_path,
                dirs_exist_ok=True,
            )

    async def mount_volume(
        self,
        tenant_id: str,
        volume_name: str,
        session_id: str,
        session_base_path: Optional[Path] = None,
    ) -> Path:
        """
        Mount a volume to a session (creates symlink).

        The symlink target must reside within the tenant's own volume
        directory or the shared "system" tenant directory.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume to mount
            session_id: Session to mount to
            session_base_path: Base path for sessions. Defaults to settings.SESSION_BASE_PATH

        Returns:
            Path to mounted volume within session

        Raises:
            ValueError: If volume does not exist or path escapes tenant boundary
        """
        safe_tenant = self._sanitize(tenant_id, "tenant_id")
        volume_path = self._get_volume_path(tenant_id, volume_name)

        if not volume_path.exists():
            raise ValueError(f"Volume '{volume_name}' not found")

        # Validate symlink target is within allowed directories
        allowed_parents = [self.base_path / safe_tenant, self.base_path / "system"]
        if not any(volume_path.resolve().is_relative_to(p.resolve()) for p in allowed_parents):
            raise ValueError(f"Volume path outside tenant boundary: {volume_name}")

        # Get session base path — include tenant_id for multi-tenant isolation
        settings = get_settings()
        session_base = session_base_path or Path(settings.SESSION_BASE_PATH)
        session_mount = session_base / safe_tenant / session_id / "volumes" / volume_name

        # Create parent directories
        await aiofiles.os.makedirs(session_mount.parent, exist_ok=True)

        # Remove existing symlink if present
        if session_mount.exists() or session_mount.is_symlink():
            await aiofiles.os.remove(session_mount)

        # Create symlink (use absolute path for reliability)
        await asyncio.to_thread(
            os.symlink,
            volume_path.resolve(),
            session_mount,
        )

        # Track mount
        if session_id not in self._mounts:
            self._mounts[session_id] = []

        self._mounts[session_id].append(
            MountInfo(
                session_id=session_id,
                volume_name=volume_name,
                mount_path=str(session_mount),
            )
        )

        return session_mount

    async def unmount_volume(
        self,
        tenant_id: str,
        volume_name: str,
        session_id: str,
        session_base_path: Optional[Path] = None,
    ) -> bool:
        """
        Unmount a volume from a session (removes symlink).

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume to unmount
            session_id: Session to unmount from
            session_base_path: Base path for sessions

        Returns:
            True if unmounted, False if not mounted
        """
        # Sanitize inputs (raises on bad values)
        safe_tenant = self._sanitize(tenant_id, "tenant_id")
        self._sanitize(volume_name, "volume_name")

        settings = get_settings()
        session_base = session_base_path or Path(settings.SESSION_BASE_PATH)
        session_mount = session_base / safe_tenant / session_id / "volumes" / volume_name

        if session_mount.is_symlink():
            await aiofiles.os.remove(session_mount)

            # Update mount tracking
            if session_id in self._mounts:
                self._mounts[session_id] = [
                    m for m in self._mounts[session_id]
                    if m.volume_name != volume_name
                ]
            return True

        return False

    async def get_volume(self, tenant_id: str, volume_name: str) -> VolumeInfo:
        """
        Get information about a volume.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume identifier

        Returns:
            VolumeInfo with volume metadata

        Raises:
            ValueError: If volume does not exist
        """
        volume_path = self._get_volume_path(tenant_id, volume_name)

        if not volume_path.exists():
            raise ValueError(f"Volume '{volume_name}' not found")

        # Calculate size and file count
        size_bytes = 0
        file_count = 0

        for file_path in volume_path.rglob("*"):
            if file_path.is_file():
                size_bytes += file_path.stat().st_size
                file_count += 1

        # Get creation time
        stat = volume_path.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime)

        return VolumeInfo(
            name=volume_name,
            path=str(volume_path),
            size_bytes=size_bytes,
            file_count=file_count,
            created_at=created_at,
        )

    async def list_volumes(self, tenant_id: str) -> list[VolumeInfo]:
        """
        List all available volumes for a tenant.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
                        Only volumes under this tenant's directory are returned.

        Returns:
            List of VolumeInfo objects
        """
        safe_tenant = self._sanitize(tenant_id, "tenant_id")
        tenant_path = self.base_path / safe_tenant
        volumes = []

        if not tenant_path.exists():
            return volumes

        for volume_dir in tenant_path.iterdir():
            if volume_dir.is_dir() and not volume_dir.name.startswith("."):
                try:
                    info = await self.get_volume(tenant_id, volume_dir.name)
                    volumes.append(info)
                except ValueError:
                    # Skip invalid volumes
                    pass

        return sorted(volumes, key=lambda v: v.name)

    async def delete_volume(
        self,
        tenant_id: str,
        volume_name: str,
        force: bool = False,
    ) -> bool:
        """
        Delete a volume.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume to delete
            force: If True, delete even if mounted to sessions

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If volume is mounted and force=False
        """
        volume_path = self._get_volume_path(tenant_id, volume_name)

        if not volume_path.exists():
            return False

        # Check if mounted
        mounted_sessions = [
            session_id
            for session_id, mounts in self._mounts.items()
            for mount in mounts
            if mount.volume_name == volume_name
        ]

        if mounted_sessions and not force:
            raise ValueError(
                f"Volume '{volume_name}' is mounted to sessions: {mounted_sessions}. "
                "Use force=True to delete anyway."
            )

        # Delete volume directory
        await asyncio.to_thread(shutil.rmtree, volume_path)

        return True

    async def list_session_mounts(self, tenant_id: str, session_id: str) -> list[MountInfo]:
        """
        List all volumes mounted to a session.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            session_id: Session identifier

        Returns:
            List of MountInfo objects
        """
        # Sanitize inputs (raises on bad values)
        self._sanitize(tenant_id, "tenant_id")

        return self._mounts.get(session_id, [])

    async def read_file(
        self,
        tenant_id: str,
        volume_name: str,
        file_path: str,
    ) -> bytes:
        """
        Read a file from a volume.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume containing the file
            file_path: Relative path within volume

        Returns:
            File contents as bytes

        Raises:
            ValueError: If volume or file not found, or path traversal detected
        """
        volume_path = self._get_volume_path(tenant_id, volume_name)

        if not volume_path.exists():
            raise ValueError(f"Volume '{volume_name}' not found")

        # Validate path to prevent traversal attacks
        full_path = self._validate_path(volume_path, file_path)

        if not full_path.exists():
            raise ValueError(f"File '{file_path}' not found in volume '{volume_name}'")

        if not full_path.is_file():
            raise ValueError(f"'{file_path}' is not a file")

        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    async def list_files(
        self,
        tenant_id: str,
        volume_name: str,
        path: str = "",
        recursive: bool = False,
    ) -> list[dict]:
        """
        List files in a volume directory.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            volume_name: Volume to list files from
            path: Relative path within volume (default: root)
            recursive: If True, list all files recursively

        Returns:
            List of file info dicts with name, path, size, is_dir

        Raises:
            ValueError: If volume not found or path traversal detected
        """
        volume_path = self._get_volume_path(tenant_id, volume_name)

        if not volume_path.exists():
            raise ValueError(f"Volume '{volume_name}' not found")

        # Validate path to prevent traversal attacks (even for empty path)
        if path:
            target_path = self._validate_path(volume_path, path)
        else:
            target_path = volume_path.resolve()

        if not target_path.is_dir():
            raise ValueError(f"'{path}' is not a directory")

        files = []
        resolved_volume = volume_path.resolve()

        if recursive:
            for file_path in target_path.rglob("*"):
                # Skip symlinks that point outside the volume
                try:
                    resolved = file_path.resolve()
                    relative_resolved = resolved.relative_to(resolved_volume)
                except ValueError:
                    continue  # Skip files outside volume boundary

                files.append({
                    "name": file_path.name,
                    "path": str(relative_resolved),
                    "size": file_path.stat().st_size if file_path.is_file() else 0,
                    "is_dir": file_path.is_dir(),
                })
        else:
            for file_path in target_path.iterdir():
                # Skip symlinks that point outside the volume
                try:
                    resolved = file_path.resolve()
                    relative_resolved = resolved.relative_to(resolved_volume)
                except ValueError:
                    continue  # Skip files outside volume boundary

                files.append({
                    "name": file_path.name,
                    "path": str(relative_resolved),
                    "size": file_path.stat().st_size if file_path.is_file() else 0,
                    "is_dir": file_path.is_dir(),
                })

        return sorted(files, key=lambda f: (not f["is_dir"], f["name"]))


# Dependency injection helper
_volume_manager: Optional[VolumeManager] = None


def get_volume_manager() -> VolumeManager:
    """Get or create the singleton VolumeManager instance."""
    global _volume_manager
    if _volume_manager is None:
        _volume_manager = VolumeManager()
    return _volume_manager


def reset_volume_manager() -> None:
    """Reset the singleton instance (for testing)."""
    global _volume_manager
    _volume_manager = None
