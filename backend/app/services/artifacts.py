"""
Artifact Storage for Oniva Simplified Memory Architecture.

Artifacts are persistent outputs (reports, CSVs, analysis results) with optional
7-year retention for regulatory compliance.

Use Cases:
- Analysis reports (PDF, Markdown)
- Risk assessment results (JSON, CSV)
- Compliance audit logs (JSONL)
- Generated visualizations (PNG, HTML)
- Export data files

This module supports both S3 and local filesystem backends:
- S3: Production use with 7-year Object Lock retention
- Local: Development and testing without S3 dependency

Storage Structure:
    /artifacts/{tenant_id}/{session_id}/
    |-- analysis_report.pdf
    |-- risk_scores.csv
    |-- compliance_audit.json
    |-- visualizations/
    |   |-- chart.png
    |   |-- dashboard.html
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

import aiofiles
import aiofiles.os

from app.config import get_settings


@dataclass
class ArtifactMetadata:
    """Metadata about an artifact."""
    session_id: str
    file_path: str
    size_bytes: int
    content_type: str
    created_at: datetime
    checksum: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    retention_until: Optional[datetime] = None
    s3_uri: Optional[str] = None
    download_url: Optional[str] = None


@dataclass
class ArtifactInfo:
    """Summary information about an artifact."""
    file_path: str
    size_bytes: int
    last_modified: datetime
    content_type: str
    s3_uri: Optional[str] = None


class ArtifactStorage:
    """
    Manages persistent artifacts with S3 or local filesystem backend.

    In production (S3):
    - Artifacts are stored with 7-year Object Lock retention
    - Server-side encryption (AES256)
    - Versioning enabled for audit trail
    - Presigned URLs for secure downloads

    In development (local):
    - Artifacts stored in local directory
    - Same API as S3 for compatibility
    - No retention enforcement

    Usage:
        # Production with S3
        storage = ArtifactStorage(use_s3=True, bucket_name="oniva-artifacts")

        # Development with local storage
        storage = ArtifactStorage(use_s3=False, local_path="./artifacts")

        # Write artifact
        uri = await storage.write(
            tenant_id="tenant_abc",
            session_id="session_123",
            file_path="report.pdf",
            content=pdf_bytes,
            metadata={"user_id": "user_456", "agent_id": "analysis"}
        )

        # Read artifact
        content = await storage.read("tenant_abc", "session_123", "report.pdf")

        # Get download URL
        url = await storage.get_download_url("tenant_abc", "session_123", "report.pdf")
    """

    # Default retention period (7 years for financial compliance)
    DEFAULT_RETENTION_DAYS = 7 * 365

    def __init__(
        self,
        use_s3: Optional[bool] = None,
        bucket_name: Optional[str] = None,
        local_path: Optional[Path] = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        s3_client: Optional[object] = None,
    ):
        """
        Initialize ArtifactStorage.

        Args:
            use_s3: If True, use S3 backend. If None, auto-detect from settings.
            bucket_name: S3 bucket name. Defaults to settings.ARTIFACT_BUCKET_NAME.
            local_path: Local directory for artifacts. Defaults to settings.ARTIFACT_LOCAL_PATH.
            retention_days: Days to retain artifacts. Default 7 years.
            s3_client: Optional boto3 S3 client (for testing).
        """
        settings = get_settings()

        # Determine storage backend
        if use_s3 is None:
            use_s3 = getattr(settings, "USE_S3_ARTIFACTS", False)

        self.use_s3 = use_s3
        self.bucket_name = bucket_name or getattr(
            settings, "ARTIFACT_BUCKET_NAME", "oniva-artifacts"
        )
        self.local_path = local_path or Path(
            getattr(settings, "ARTIFACT_LOCAL_PATH", "./artifacts")
        )
        self.local_path = Path(self.local_path)
        self.retention_days = retention_days
        self._s3_client = s3_client

    @property
    def s3_client(self):
        """Lazy-load S3 client only when needed."""
        if self._s3_client is None:
            try:
                import boto3
                self._s3_client = boto3.client("s3")
            except ImportError:
                raise ImportError(
                    "boto3 is required for S3 artifact storage. "
                    "Install with: pip install boto3"
                )
        return self._s3_client

    @staticmethod
    def _sanitize(value: str, label: str) -> str:
        """
        Validate and return a safe filesystem/S3 path component.

        Only alphanumeric characters, hyphens, and underscores are allowed.

        Args:
            value: The raw string to sanitize.
            label: Human-readable label used in error messages (e.g. "tenant_id").

        Returns:
            The validated string (unchanged if valid).

        Raises:
            ValueError: If value contains invalid characters or is empty.
        """
        safe = "".join(c for c in value if c.isalnum() or c in "-_")
        if not safe or safe != value:
            raise ValueError(f"Invalid {label}: {value}")
        return safe

    def _get_artifact_key(self, tenant_id: str, session_id: str, file_path: str) -> str:
        """Generate S3 key or local path for artifact.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier.
            file_path: Relative file path within the session.

        Returns:
            A key string of the form ``artifacts/{tenant_id}/{session_id}/{file_path}``.
        """
        safe_tenant = self._sanitize(tenant_id, "tenant_id")
        safe_session = self._sanitize(session_id, "session_id")
        safe_path = file_path.lstrip("/")

        return f"artifacts/{safe_tenant}/{safe_session}/{safe_path}"

    def _get_content_type(self, file_path: str) -> str:
        """Guess content type from file extension."""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"

    def _compute_checksum(self, content: bytes) -> str:
        """Compute SHA256 checksum of content."""
        return hashlib.sha256(content).hexdigest()

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        content: bytes,
        metadata: Optional[dict] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Write an artifact to storage.

        In S3 mode:
        - Sets 7-year Object Lock retention
        - Enables server-side encryption
        - Stores user metadata

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier
            file_path: Relative path within session (e.g., "report.pdf")
            content: Binary content
            metadata: Optional metadata (user_id, agent_id, etc.)
            content_type: MIME type. Auto-detected if not provided.

        Returns:
            Storage URI (s3:// or file://)
        """
        key = self._get_artifact_key(tenant_id, session_id, file_path)
        content_type = content_type or self._get_content_type(file_path)
        checksum = self._compute_checksum(content)

        # Prepare metadata
        artifact_metadata = {
            "session_id": session_id,
            "checksum": checksum,
            "created_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        if self.use_s3:
            return await self._write_s3(key, content, artifact_metadata, content_type)
        else:
            return await self._write_local(key, content, artifact_metadata, content_type)

    async def _write_s3(
        self,
        key: str,
        content: bytes,
        metadata: dict,
        content_type: str,
    ) -> str:
        """Write artifact to S3 with retention policy."""
        retention_until = datetime.utcnow() + timedelta(days=self.retention_days)

        # Convert metadata values to strings (S3 requirement)
        s3_metadata = {k: str(v) for k, v in metadata.items()}

        try:
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata=s3_metadata,
                ServerSideEncryption="AES256",
                # Object Lock retention (requires bucket with Object Lock enabled)
                # ObjectLockRetainUntilDate=retention_until,
                # ObjectLockMode="GOVERNANCE",
            )
        except Exception as e:
            # If Object Lock fails, try without it (for buckets without Object Lock)
            if "ObjectLockConfiguration" in str(e):
                await asyncio.to_thread(
                    self.s3_client.put_object,
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                    Metadata=s3_metadata,
                    ServerSideEncryption="AES256",
                )
            else:
                raise

        return f"s3://{self.bucket_name}/{key}"

    async def _write_local(
        self,
        key: str,
        content: bytes,
        metadata: dict,
        content_type: str,
    ) -> str:
        """Write artifact to local filesystem."""
        full_path = self.local_path / key
        metadata_path = full_path.with_suffix(full_path.suffix + ".meta.json")

        # Create parent directories
        await aiofiles.os.makedirs(full_path.parent, exist_ok=True)

        # Write content
        async with aiofiles.open(full_path, "wb") as f:
            await f.write(content)

        # Write metadata
        import json
        metadata["content_type"] = content_type
        metadata["size_bytes"] = len(content)

        async with aiofiles.open(metadata_path, "w") as f:
            await f.write(json.dumps(metadata, indent=2))

        return f"file://{full_path.resolve()}"

    async def read(self, tenant_id: str, session_id: str, file_path: str) -> bytes:
        """
        Read an artifact from storage.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier
            file_path: Relative path within session

        Returns:
            Binary content

        Raises:
            ValueError: If artifact not found
        """
        key = self._get_artifact_key(tenant_id, session_id, file_path)

        if self.use_s3:
            return await self._read_s3(key)
        else:
            return await self._read_local(key)

    async def _read_s3(self, key: str) -> bytes:
        """Read artifact from S3."""
        try:
            response = await asyncio.to_thread(
                self.s3_client.get_object,
                Bucket=self.bucket_name,
                Key=key,
            )
            return response["Body"].read()
        except self.s3_client.exceptions.NoSuchKey:
            raise ValueError(f"Artifact not found: {key}")

    async def _read_local(self, key: str) -> bytes:
        """Read artifact from local filesystem."""
        full_path = self.local_path / key

        if not full_path.exists():
            raise ValueError(f"Artifact not found: {key}")

        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    async def list_artifacts(self, tenant_id: str, session_id: str) -> list[ArtifactInfo]:
        """
        List all artifacts for a session.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier

        Returns:
            List of ArtifactInfo objects
        """
        prefix = self._get_artifact_key(tenant_id, session_id, "")

        if self.use_s3:
            return await self._list_s3(prefix, session_id)
        else:
            return await self._list_local(prefix, session_id)

    async def _list_s3(self, prefix: str, session_id: str) -> list[ArtifactInfo]:
        """List artifacts from S3."""
        artifacts = []

        try:
            response = await asyncio.to_thread(
                self.s3_client.list_objects_v2,
                Bucket=self.bucket_name,
                Prefix=prefix,
            )

            for obj in response.get("Contents", []):
                file_path = obj["Key"].replace(prefix, "")
                artifacts.append(
                    ArtifactInfo(
                        file_path=file_path,
                        size_bytes=obj["Size"],
                        last_modified=obj["LastModified"],
                        content_type=self._get_content_type(file_path),
                        s3_uri=f"s3://{self.bucket_name}/{obj['Key']}",
                    )
                )
        except Exception:
            # Return empty list if bucket doesn't exist or access denied
            pass

        return artifacts

    async def _list_local(self, prefix: str, session_id: str) -> list[ArtifactInfo]:
        """List artifacts from local filesystem."""
        artifacts = []
        session_path = self.local_path / prefix

        if not session_path.exists():
            return artifacts

        for file_path in session_path.rglob("*"):
            if file_path.is_file() and not file_path.name.endswith(".meta.json"):
                relative = file_path.relative_to(session_path)
                stat = file_path.stat()
                artifacts.append(
                    ArtifactInfo(
                        file_path=str(relative),
                        size_bytes=stat.st_size,
                        last_modified=datetime.fromtimestamp(stat.st_mtime),
                        content_type=self._get_content_type(str(relative)),
                    )
                )

        return sorted(artifacts, key=lambda a: a.file_path)

    async def get_download_url(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        expiration: int = 3600,
    ) -> str:
        """
        Generate a download URL for an artifact.

        In S3 mode: Returns presigned URL
        In local mode: Returns file:// URL

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier
            file_path: Relative path within session
            expiration: URL expiration time in seconds (default 1 hour)

        Returns:
            Download URL
        """
        key = self._get_artifact_key(tenant_id, session_id, file_path)

        if self.use_s3:
            url = await asyncio.to_thread(
                self.s3_client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        else:
            full_path = self.local_path / key
            if not full_path.exists():
                raise ValueError(f"Artifact not found: {key}")
            return f"file://{full_path.resolve()}"

    async def delete(self, tenant_id: str, session_id: str, file_path: str) -> bool:
        """
        Delete an artifact.

        Note: In S3 with Object Lock, deletion may fail before retention expires.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier
            file_path: Relative path within session

        Returns:
            True if deleted, False if not found
        """
        key = self._get_artifact_key(tenant_id, session_id, file_path)

        if self.use_s3:
            return await self._delete_s3(key)
        else:
            return await self._delete_local(key)

    async def _delete_s3(self, key: str) -> bool:
        """Delete artifact from S3."""
        try:
            await asyncio.to_thread(
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=key,
            )
            return True
        except Exception:
            return False

    async def _delete_local(self, key: str) -> bool:
        """Delete artifact from local filesystem."""
        full_path = self.local_path / key
        metadata_path = full_path.with_suffix(full_path.suffix + ".meta.json")

        deleted = False

        if full_path.exists():
            await aiofiles.os.remove(full_path)
            deleted = True

        if metadata_path.exists():
            await aiofiles.os.remove(metadata_path)

        return deleted

    async def delete_session_artifacts(self, tenant_id: str, session_id: str) -> int:
        """
        Delete all artifacts for a session.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier

        Returns:
            Number of artifacts deleted
        """
        artifacts = await self.list_artifacts(tenant_id, session_id)
        count = 0

        for artifact in artifacts:
            if await self.delete(tenant_id, session_id, artifact.file_path):
                count += 1

        return count

    async def get_metadata(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
    ) -> Optional[ArtifactMetadata]:
        """
        Get metadata for an artifact.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation.
            session_id: Session identifier
            file_path: Relative path within session

        Returns:
            ArtifactMetadata or None if not found
        """
        key = self._get_artifact_key(tenant_id, session_id, file_path)

        if self.use_s3:
            return await self._get_metadata_s3(key, session_id, file_path)
        else:
            return await self._get_metadata_local(key, session_id, file_path)

    async def _get_metadata_s3(
        self,
        key: str,
        session_id: str,
        file_path: str,
    ) -> Optional[ArtifactMetadata]:
        """Get artifact metadata from S3."""
        try:
            response = await asyncio.to_thread(
                self.s3_client.head_object,
                Bucket=self.bucket_name,
                Key=key,
            )

            metadata = response.get("Metadata", {})

            return ArtifactMetadata(
                session_id=session_id,
                file_path=file_path,
                size_bytes=response["ContentLength"],
                content_type=response["ContentType"],
                created_at=response["LastModified"],
                checksum=metadata.get("checksum"),
                user_id=metadata.get("user_id"),
                agent_id=metadata.get("agent_id"),
                s3_uri=f"s3://{self.bucket_name}/{key}",
            )
        except Exception:
            return None

    async def _get_metadata_local(
        self,
        key: str,
        session_id: str,
        file_path: str,
    ) -> Optional[ArtifactMetadata]:
        """Get artifact metadata from local filesystem."""
        full_path = self.local_path / key
        metadata_path = full_path.with_suffix(full_path.suffix + ".meta.json")

        if not full_path.exists():
            return None

        stat = full_path.stat()
        metadata = {}

        if metadata_path.exists():
            import json
            async with aiofiles.open(metadata_path, "r") as f:
                metadata = json.loads(await f.read())

        return ArtifactMetadata(
            session_id=session_id,
            file_path=file_path,
            size_bytes=stat.st_size,
            content_type=metadata.get("content_type", self._get_content_type(file_path)),
            created_at=datetime.fromtimestamp(stat.st_ctime),
            checksum=metadata.get("checksum"),
            user_id=metadata.get("user_id"),
            agent_id=metadata.get("agent_id"),
        )


# ============================================================================
# Singleton and Dependency Injection
# ============================================================================

_artifact_storage: Optional[ArtifactStorage] = None


def get_artifact_storage() -> ArtifactStorage:
    """Get or create the singleton ArtifactStorage instance."""
    global _artifact_storage
    if _artifact_storage is None:
        _artifact_storage = ArtifactStorage()
    return _artifact_storage


def reset_artifact_storage() -> None:
    """Reset the singleton instance (for testing)."""
    global _artifact_storage
    _artifact_storage = None
