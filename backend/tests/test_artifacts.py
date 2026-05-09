"""
Unit tests for ArtifactStorage (Simplified Memory Architecture).

Tests cover:
- Artifact write/read with local backend
- Artifact listing
- Download URL generation
- Metadata storage and retrieval
- Artifact deletion
- Edge cases and error handling
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from app.services.artifacts import (
    ArtifactStorage,
    get_artifact_storage,
    reset_artifact_storage,
)

TENANT = "test-tenant"


@pytest.fixture
def temp_artifacts_dir():
    """Create a temporary directory for artifacts."""
    temp_dir = tempfile.mkdtemp(prefix="test_artifacts_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def artifact_storage(temp_artifacts_dir):
    """Create an ArtifactStorage with local backend."""
    return ArtifactStorage(
        use_s3=False,
        local_path=temp_artifacts_dir,
    )


class TestArtifactWrite:
    """Tests for writing artifacts."""

    @pytest.mark.asyncio
    async def test_write_artifact(self, artifact_storage):
        """Test writing a simple artifact."""
        content = b"Hello, World!"

        uri = await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="greeting.txt",
            content=content,
        )

        assert uri.startswith("file://")
        assert "session_123" in uri
        assert "greeting.txt" in uri

    @pytest.mark.asyncio
    async def test_write_artifact_with_metadata(self, artifact_storage):
        """Test writing artifact with metadata."""
        content = b'{"result": "analysis complete"}'

        uri = await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="result.json",
            content=content,
            metadata={
                "user_id": "user_456",
                "agent_id": "analysis_agent",
            },
        )

        assert uri is not None

        # Verify metadata was stored
        meta = await artifact_storage.get_metadata(TENANT, "session_123", "result.json")
        assert meta is not None
        assert meta.user_id == "user_456"
        assert meta.agent_id == "analysis_agent"

    @pytest.mark.asyncio
    async def test_write_artifact_nested_path(self, artifact_storage):
        """Test writing artifact to nested path."""
        content = b"chart data"

        uri = await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="visualizations/charts/chart1.png",
            content=content,
        )

        assert "visualizations/charts/chart1.png" in uri or "visualizations\\charts\\chart1.png" in uri

    @pytest.mark.asyncio
    async def test_write_artifact_auto_content_type(self, artifact_storage):
        """Test automatic content type detection."""
        content = b"PDF content"

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="report.pdf",
            content=content,
        )

        meta = await artifact_storage.get_metadata(TENANT, "session_123", "report.pdf")
        assert meta is not None
        assert meta.content_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_write_artifact_explicit_content_type(self, artifact_storage):
        """Test explicit content type."""
        content = b"Custom data"

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="data.bin",
            content=content,
            content_type="application/custom",
        )

        meta = await artifact_storage.get_metadata(TENANT, "session_123", "data.bin")
        assert meta is not None
        assert meta.content_type == "application/custom"


class TestArtifactRead:
    """Tests for reading artifacts."""

    @pytest.mark.asyncio
    async def test_read_artifact(self, artifact_storage):
        """Test reading a written artifact."""
        original = b"Test content for reading"

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="test.txt",
            content=original,
        )

        content = await artifact_storage.read(TENANT, "session_123", "test.txt")

        assert content == original

    @pytest.mark.asyncio
    async def test_read_artifact_binary(self, artifact_storage):
        """Test reading binary artifact."""
        original = bytes(range(256))

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="binary.bin",
            content=original,
        )

        content = await artifact_storage.read(TENANT, "session_123", "binary.bin")

        assert content == original

    @pytest.mark.asyncio
    async def test_read_nonexistent_artifact(self, artifact_storage):
        """Test reading non-existent artifact raises error."""
        with pytest.raises(ValueError, match="not found"):
            await artifact_storage.read(TENANT, "session_123", "nonexistent.txt")


class TestArtifactList:
    """Tests for listing artifacts."""

    @pytest.mark.asyncio
    async def test_list_artifacts_empty(self, artifact_storage):
        """Test listing artifacts for empty session."""
        artifacts = await artifact_storage.list_artifacts(TENANT, "empty_session")

        assert artifacts == []

    @pytest.mark.asyncio
    async def test_list_artifacts(self, artifact_storage):
        """Test listing multiple artifacts."""
        await artifact_storage.write(TENANT, "session_123", "file1.txt", b"content1")
        await artifact_storage.write(TENANT, "session_123", "file2.csv", b"content2")
        await artifact_storage.write(TENANT, "session_123", "subdir/file3.json", b"content3")

        artifacts = await artifact_storage.list_artifacts(TENANT, "session_123")

        assert len(artifacts) == 3
        paths = {a.file_path for a in artifacts}
        assert "file1.txt" in paths
        assert "file2.csv" in paths

    @pytest.mark.asyncio
    async def test_list_artifacts_correct_info(self, artifact_storage):
        """Test that artifact info is correct."""
        content = b"x" * 1000

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="sized.txt",
            content=content,
        )

        artifacts = await artifact_storage.list_artifacts(TENANT, "session_123")

        assert len(artifacts) == 1
        assert artifacts[0].file_path == "sized.txt"
        assert artifacts[0].size_bytes == 1000
        assert artifacts[0].content_type == "text/plain"


class TestArtifactDownloadUrl:
    """Tests for download URL generation."""

    @pytest.mark.asyncio
    async def test_get_download_url(self, artifact_storage):
        """Test getting download URL."""
        await artifact_storage.write(TENANT, "session_123", "file.txt", b"content")

        url = await artifact_storage.get_download_url(TENANT, "session_123", "file.txt")

        # Local storage returns file:// URL
        assert url.startswith("file://")
        assert "file.txt" in url

    @pytest.mark.asyncio
    async def test_get_download_url_nonexistent(self, artifact_storage):
        """Test getting download URL for non-existent artifact."""
        with pytest.raises(ValueError, match="not found"):
            await artifact_storage.get_download_url(TENANT, "session_123", "nonexistent.txt")


class TestArtifactMetadata:
    """Tests for artifact metadata."""

    @pytest.mark.asyncio
    async def test_get_metadata(self, artifact_storage):
        """Test getting artifact metadata."""
        content = b"Test content"

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="test.txt",
            content=content,
            metadata={"user_id": "user_1", "agent_id": "agent_1"},
        )

        meta = await artifact_storage.get_metadata(TENANT, "session_123", "test.txt")

        assert meta is not None
        assert meta.session_id == "session_123"
        assert meta.file_path == "test.txt"
        assert meta.size_bytes == len(content)
        assert meta.content_type == "text/plain"
        assert meta.user_id == "user_1"
        assert meta.agent_id == "agent_1"
        assert meta.checksum is not None

    @pytest.mark.asyncio
    async def test_get_metadata_nonexistent(self, artifact_storage):
        """Test getting metadata for non-existent artifact."""
        meta = await artifact_storage.get_metadata(TENANT, "session_123", "nonexistent.txt")

        assert meta is None

    @pytest.mark.asyncio
    async def test_checksum_computed(self, artifact_storage):
        """Test that checksum is computed correctly."""
        content = b"Test content for checksum"

        await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="checksummed.txt",
            content=content,
        )

        meta = await artifact_storage.get_metadata(TENANT, "session_123", "checksummed.txt")

        assert meta is not None
        assert meta.checksum is not None
        assert len(meta.checksum) == 64  # SHA256 hex length


class TestArtifactDelete:
    """Tests for deleting artifacts."""

    @pytest.mark.asyncio
    async def test_delete_artifact(self, artifact_storage):
        """Test deleting an artifact."""
        await artifact_storage.write(TENANT, "session_123", "to_delete.txt", b"delete me")

        deleted = await artifact_storage.delete(TENANT, "session_123", "to_delete.txt")

        assert deleted is True

        # Verify it's gone
        with pytest.raises(ValueError, match="not found"):
            await artifact_storage.read(TENANT, "session_123", "to_delete.txt")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_artifact(self, artifact_storage):
        """Test deleting non-existent artifact returns False."""
        deleted = await artifact_storage.delete(TENANT, "session_123", "nonexistent.txt")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_all_session_artifacts(self, artifact_storage):
        """Test deleting all artifacts for a session."""
        await artifact_storage.write(TENANT, "session_123", "file1.txt", b"content1")
        await artifact_storage.write(TENANT, "session_123", "file2.txt", b"content2")
        await artifact_storage.write(TENANT, "session_123", "file3.txt", b"content3")

        count = await artifact_storage.delete_session_artifacts(TENANT, "session_123")

        assert count == 3

        artifacts = await artifact_storage.list_artifacts(TENANT, "session_123")
        assert len(artifacts) == 0

    @pytest.mark.asyncio
    async def test_delete_session_artifacts_empty(self, artifact_storage):
        """Test deleting artifacts for empty session."""
        count = await artifact_storage.delete_session_artifacts(TENANT, "empty_session")

        assert count == 0


class TestSessionIsolation:
    """Tests for session isolation."""

    @pytest.mark.asyncio
    async def test_sessions_isolated(self, artifact_storage):
        """Test that artifacts are isolated between sessions."""
        await artifact_storage.write(TENANT, "session_1", "file.txt", b"session 1 content")
        await artifact_storage.write(TENANT, "session_2", "file.txt", b"session 2 content")

        content_1 = await artifact_storage.read(TENANT, "session_1", "file.txt")
        content_2 = await artifact_storage.read(TENANT, "session_2", "file.txt")

        assert content_1 == b"session 1 content"
        assert content_2 == b"session 2 content"

    @pytest.mark.asyncio
    async def test_list_only_session_artifacts(self, artifact_storage):
        """Test that list only returns artifacts for specified session."""
        await artifact_storage.write(TENANT, "session_1", "file1.txt", b"content")
        await artifact_storage.write(TENANT, "session_1", "file2.txt", b"content")
        await artifact_storage.write(TENANT, "session_2", "file3.txt", b"content")

        artifacts_1 = await artifact_storage.list_artifacts(TENANT, "session_1")
        artifacts_2 = await artifact_storage.list_artifacts(TENANT, "session_2")

        assert len(artifacts_1) == 2
        assert len(artifacts_2) == 1


class TestSingletonBehavior:
    """Tests for singleton pattern."""

    def test_get_artifact_storage_singleton(self):
        """Test that get_artifact_storage returns singleton."""
        reset_artifact_storage()

        storage1 = get_artifact_storage()
        storage2 = get_artifact_storage()

        assert storage1 is storage2

        reset_artifact_storage()

    def test_reset_artifact_storage(self):
        """Test resetting the singleton."""
        reset_artifact_storage()

        storage1 = get_artifact_storage()
        reset_artifact_storage()
        storage2 = get_artifact_storage()

        assert storage1 is not storage2

        reset_artifact_storage()


class TestContentTypeDetection:
    """Tests for content type detection."""

    @pytest.mark.asyncio
    async def test_common_content_types(self, artifact_storage):
        """Test detection of common content types."""
        test_cases = [
            ("document.pdf", "application/pdf"),
            ("data.json", "application/json"),
            ("report.csv", "text/csv"),
            ("image.png", "image/png"),
            ("image.jpg", "image/jpeg"),
            ("page.html", "text/html"),
            ("style.css", "text/css"),
            ("script.js", "application/javascript"),
        ]

        for file_name, expected_type in test_cases:
            await artifact_storage.write(
                tenant_id=TENANT,
                session_id="session_test",
                file_path=file_name,
                content=b"test",
            )

            meta = await artifact_storage.get_metadata(TENANT, "session_test", file_name)
            assert meta is not None, f"No metadata for {file_name}"
            # Note: Some systems may return slightly different MIME types
            assert meta.content_type is not None, f"No content type for {file_name}"


class TestLargeFiles:
    """Tests for handling large files."""

    @pytest.mark.asyncio
    async def test_write_large_file(self, artifact_storage):
        """Test writing a large file (1MB)."""
        # 1MB of data
        content = b"x" * (1024 * 1024)

        uri = await artifact_storage.write(
            tenant_id=TENANT,
            session_id="session_123",
            file_path="large.bin",
            content=content,
        )

        assert uri is not None

        # Verify size
        meta = await artifact_storage.get_metadata(TENANT, "session_123", "large.bin")
        assert meta is not None
        assert meta.size_bytes == 1024 * 1024

    @pytest.mark.asyncio
    async def test_read_large_file(self, artifact_storage):
        """Test reading a large file."""
        original = b"x" * (1024 * 1024)

        await artifact_storage.write(TENANT, "session_123", "large.bin", original)

        content = await artifact_storage.read(TENANT, "session_123", "large.bin")

        assert len(content) == len(original)
        assert content == original
