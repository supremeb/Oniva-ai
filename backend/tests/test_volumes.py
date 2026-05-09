"""
Unit tests for VolumeManager (Simplified Memory Architecture).

Tests cover:
- Volume creation from local filesystem
- Volume mounting to sessions
- Volume listing and deletion
- File reading from volumes
- Edge cases and error handling
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from app.services.volumes import (
    VolumeManager,
    get_volume_manager,
    reset_volume_manager,
)

TENANT = "test-tenant"


@pytest.fixture
def temp_volumes_dir():
    """Create a temporary directory for volumes."""
    temp_dir = tempfile.mkdtemp(prefix="test_volumes_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary directory for sessions."""
    temp_dir = tempfile.mkdtemp(prefix="test_sessions_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def volume_manager(temp_volumes_dir):
    """Create a VolumeManager with temp directory."""
    return VolumeManager(base_path=temp_volumes_dir)


@pytest.fixture
def sample_data_dir():
    """Create sample data files for testing."""
    temp_dir = tempfile.mkdtemp(prefix="test_source_")
    data_path = Path(temp_dir)

    # Create sample files
    (data_path / "prices.csv").write_text("date,open,high,low,close\n2026-01-01,100,105,99,103\n")
    (data_path / "metadata.json").write_text('{"symbol": "AAPL", "name": "Apple Inc."}')

    # Create a subdirectory with files
    subdir = data_path / "historical"
    subdir.mkdir()
    (subdir / "2025.csv").write_text("date,price\n2025-01-01,150\n")
    (subdir / "2024.csv").write_text("date,price\n2024-01-01,175\n")

    yield data_path
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestVolumeCreation:
    """Tests for creating volumes."""

    @pytest.mark.asyncio
    async def test_create_volume_from_local_directory(
        self, volume_manager, sample_data_dir
    ):
        """Test creating a volume from local directory."""
        info = await volume_manager.create_volume(
            TENANT,
            volume_name="market_data",
            source=str(sample_data_dir),
        )

        assert info.name == "market_data"
        assert info.file_count == 4  # prices.csv, metadata.json, 2025.csv, 2024.csv
        assert info.size_bytes > 0
        assert info.created_at is not None

    @pytest.mark.asyncio
    async def test_create_volume_from_single_file(
        self, volume_manager, sample_data_dir
    ):
        """Test creating a volume from a single file."""
        source_file = sample_data_dir / "prices.csv"

        info = await volume_manager.create_volume(
            TENANT,
            volume_name="prices_only",
            source=str(source_file),
        )

        assert info.name == "prices_only"
        assert info.file_count == 1

    @pytest.mark.asyncio
    async def test_create_volume_invalid_name(self, volume_manager, sample_data_dir):
        """Test that invalid volume names are rejected."""
        with pytest.raises(ValueError, match="Invalid volume_name"):
            await volume_manager.create_volume(
                TENANT,
                volume_name="invalid name with spaces",
                source=str(sample_data_dir),
            )

    @pytest.mark.asyncio
    async def test_create_volume_special_chars_rejected(
        self, volume_manager, sample_data_dir
    ):
        """Test that special characters in names are rejected."""
        with pytest.raises(ValueError, match="Invalid volume_name"):
            await volume_manager.create_volume(
                TENANT,
                volume_name="data/../../etc",
                source=str(sample_data_dir),
            )

    @pytest.mark.asyncio
    async def test_create_volume_already_exists(
        self, volume_manager, sample_data_dir
    ):
        """Test that creating duplicate volume fails without overwrite."""
        await volume_manager.create_volume(
            TENANT,
            volume_name="test_volume",
            source=str(sample_data_dir),
        )

        with pytest.raises(ValueError, match="already exists"):
            await volume_manager.create_volume(
                TENANT,
                volume_name="test_volume",
                source=str(sample_data_dir),
            )

    @pytest.mark.asyncio
    async def test_create_volume_overwrite(self, volume_manager, sample_data_dir):
        """Test overwriting an existing volume."""
        await volume_manager.create_volume(
            TENANT,
            volume_name="test_volume",
            source=str(sample_data_dir),
        )

        # Should succeed with overwrite=True
        info = await volume_manager.create_volume(
            TENANT,
            volume_name="test_volume",
            source=str(sample_data_dir),
            overwrite=True,
        )

        assert info.name == "test_volume"

    @pytest.mark.asyncio
    async def test_create_volume_source_not_found(self, volume_manager):
        """Test that missing source path raises error."""
        with pytest.raises(ValueError, match="does not exist"):
            await volume_manager.create_volume(
                TENANT,
                volume_name="test_volume",
                source="/nonexistent/path",
            )


class TestVolumeMounting:
    """Tests for mounting volumes to sessions."""

    @pytest.mark.asyncio
    async def test_mount_volume_to_session(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test mounting a volume to a session."""
        await volume_manager.create_volume(
            TENANT,
            volume_name="market_data",
            source=str(sample_data_dir),
        )

        mount_path = await volume_manager.mount_volume(
            TENANT,
            volume_name="market_data",
            session_id="session_123",
            session_base_path=temp_sessions_dir,
        )

        assert mount_path.exists()
        assert mount_path.is_symlink()
        # Verify symlink points to correct location (resolve both paths to handle macOS /var -> /private/var)
        assert mount_path.resolve() == (volume_manager.base_path / TENANT / "market_data").resolve()

    @pytest.mark.asyncio
    async def test_mount_nonexistent_volume(self, volume_manager, temp_sessions_dir):
        """Test mounting a volume that doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await volume_manager.mount_volume(
                TENANT,
                volume_name="nonexistent",
                session_id="session_123",
                session_base_path=temp_sessions_dir,
            )

    @pytest.mark.asyncio
    async def test_mount_same_volume_multiple_sessions(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test that same volume can be mounted to multiple sessions."""
        await volume_manager.create_volume(
            TENANT,
            volume_name="shared_data",
            source=str(sample_data_dir),
        )

        mount1 = await volume_manager.mount_volume(
            TENANT,
            volume_name="shared_data",
            session_id="session_1",
            session_base_path=temp_sessions_dir,
        )

        mount2 = await volume_manager.mount_volume(
            TENANT,
            volume_name="shared_data",
            session_id="session_2",
            session_base_path=temp_sessions_dir,
        )

        assert mount1.exists()
        assert mount2.exists()
        assert mount1 != mount2
        # Both should point to same volume
        assert mount1.resolve() == mount2.resolve()

    @pytest.mark.asyncio
    async def test_unmount_volume(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test unmounting a volume from a session."""
        await volume_manager.create_volume(
            TENANT,
            volume_name="test_data",
            source=str(sample_data_dir),
        )

        mount_path = await volume_manager.mount_volume(
            TENANT,
            volume_name="test_data",
            session_id="session_123",
            session_base_path=temp_sessions_dir,
        )

        assert mount_path.exists()

        unmounted = await volume_manager.unmount_volume(
            TENANT,
            volume_name="test_data",
            session_id="session_123",
            session_base_path=temp_sessions_dir,
        )

        assert unmounted is True
        assert not mount_path.exists()

    @pytest.mark.asyncio
    async def test_unmount_not_mounted(self, volume_manager, temp_sessions_dir):
        """Test unmounting returns False if not mounted."""
        unmounted = await volume_manager.unmount_volume(
            TENANT,
            volume_name="nonexistent",
            session_id="session_123",
            session_base_path=temp_sessions_dir,
        )

        assert unmounted is False

    @pytest.mark.asyncio
    async def test_list_session_mounts(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test listing mounts for a session."""
        await volume_manager.create_volume(TENANT, "vol1", str(sample_data_dir))
        await volume_manager.create_volume(TENANT, "vol2", str(sample_data_dir), overwrite=True)

        await volume_manager.mount_volume(TENANT, "vol1", "session_1", temp_sessions_dir)
        await volume_manager.mount_volume(TENANT, "vol2", "session_1", temp_sessions_dir)

        mounts = await volume_manager.list_session_mounts(TENANT, "session_1")

        assert len(mounts) == 2
        volume_names = {m.volume_name for m in mounts}
        assert volume_names == {"vol1", "vol2"}


class TestVolumeManagement:
    """Tests for volume listing, getting, and deletion."""

    @pytest.mark.asyncio
    async def test_list_volumes_empty(self, volume_manager):
        """Test listing volumes when none exist."""
        volumes = await volume_manager.list_volumes(TENANT)
        assert volumes == []

    @pytest.mark.asyncio
    async def test_list_volumes(self, volume_manager, sample_data_dir):
        """Test listing multiple volumes."""
        await volume_manager.create_volume(TENANT, "alpha", str(sample_data_dir))
        await volume_manager.create_volume(TENANT, "beta", str(sample_data_dir))
        await volume_manager.create_volume(TENANT, "gamma", str(sample_data_dir))

        volumes = await volume_manager.list_volumes(TENANT)

        assert len(volumes) == 3
        names = [v.name for v in volumes]
        assert names == ["alpha", "beta", "gamma"]  # Sorted alphabetically

    @pytest.mark.asyncio
    async def test_get_volume(self, volume_manager, sample_data_dir):
        """Test getting volume info."""
        await volume_manager.create_volume(TENANT, "test_volume", str(sample_data_dir))

        info = await volume_manager.get_volume(TENANT, "test_volume")

        assert info.name == "test_volume"
        assert info.file_count == 4
        assert info.size_bytes > 0
        assert isinstance(info.created_at, datetime)

    @pytest.mark.asyncio
    async def test_get_nonexistent_volume(self, volume_manager):
        """Test getting a volume that doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await volume_manager.get_volume(TENANT, "nonexistent")

    @pytest.mark.asyncio
    async def test_delete_volume(self, volume_manager, sample_data_dir):
        """Test deleting a volume."""
        await volume_manager.create_volume(TENANT, "to_delete", str(sample_data_dir))

        deleted = await volume_manager.delete_volume(TENANT, "to_delete")

        assert deleted is True

        volumes = await volume_manager.list_volumes(TENANT)
        assert len(volumes) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_volume(self, volume_manager):
        """Test deleting a volume that doesn't exist."""
        deleted = await volume_manager.delete_volume(TENANT, "nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_mounted_volume_fails(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test that deleting a mounted volume fails without force."""
        await volume_manager.create_volume(TENANT, "mounted", str(sample_data_dir))
        await volume_manager.mount_volume(TENANT, "mounted", "session_1", temp_sessions_dir)

        with pytest.raises(ValueError, match="is mounted"):
            await volume_manager.delete_volume(TENANT, "mounted")

    @pytest.mark.asyncio
    async def test_delete_mounted_volume_force(
        self, volume_manager, sample_data_dir, temp_sessions_dir
    ):
        """Test force deleting a mounted volume."""
        await volume_manager.create_volume(TENANT, "mounted", str(sample_data_dir))
        await volume_manager.mount_volume(TENANT, "mounted", "session_1", temp_sessions_dir)

        deleted = await volume_manager.delete_volume(TENANT, "mounted", force=True)

        assert deleted is True


class TestVolumeFileOperations:
    """Tests for reading files from volumes."""

    @pytest.mark.asyncio
    async def test_read_file(self, volume_manager, sample_data_dir):
        """Test reading a file from a volume."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        content = await volume_manager.read_file(TENANT, "data", "prices.csv")

        assert b"date,open,high,low,close" in content
        assert b"2026-01-01" in content

    @pytest.mark.asyncio
    async def test_read_file_in_subdirectory(self, volume_manager, sample_data_dir):
        """Test reading a file from a subdirectory."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        content = await volume_manager.read_file(TENANT, "data", "historical/2025.csv")

        assert b"date,price" in content

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, volume_manager, sample_data_dir):
        """Test reading a file that doesn't exist."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        with pytest.raises(ValueError, match="not found in volume"):
            await volume_manager.read_file(TENANT, "data", "nonexistent.txt")

    @pytest.mark.asyncio
    async def test_read_file_from_nonexistent_volume(self, volume_manager):
        """Test reading from a volume that doesn't exist."""
        with pytest.raises(ValueError, match="Volume.*not found"):
            await volume_manager.read_file(TENANT, "nonexistent", "file.txt")

    @pytest.mark.asyncio
    async def test_list_files_root(self, volume_manager, sample_data_dir):
        """Test listing files at volume root."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        files = await volume_manager.list_files(TENANT, "data")

        # Should include files and the 'historical' directory
        names = {f["name"] for f in files}
        assert "prices.csv" in names
        assert "metadata.json" in names
        assert "historical" in names

    @pytest.mark.asyncio
    async def test_list_files_subdirectory(self, volume_manager, sample_data_dir):
        """Test listing files in a subdirectory."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        files = await volume_manager.list_files(TENANT, "data", path="historical")

        names = {f["name"] for f in files}
        assert "2025.csv" in names
        assert "2024.csv" in names

    @pytest.mark.asyncio
    async def test_list_files_recursive(self, volume_manager, sample_data_dir):
        """Test recursive file listing."""
        await volume_manager.create_volume(TENANT, "data", str(sample_data_dir))

        files = await volume_manager.list_files(TENANT, "data", recursive=True)

        paths = {f["path"] for f in files}
        assert "prices.csv" in paths
        assert "historical/2025.csv" in paths or "historical\\2025.csv" in paths


class TestSingletonBehavior:
    """Tests for singleton pattern."""

    def test_get_volume_manager_singleton(self):
        """Test that get_volume_manager returns singleton."""
        reset_volume_manager()

        mgr1 = get_volume_manager()
        mgr2 = get_volume_manager()

        assert mgr1 is mgr2

        reset_volume_manager()

    def test_reset_volume_manager(self):
        """Test resetting the singleton."""
        reset_volume_manager()

        mgr1 = get_volume_manager()
        reset_volume_manager()
        mgr2 = get_volume_manager()

        assert mgr1 is not mgr2

        reset_volume_manager()
