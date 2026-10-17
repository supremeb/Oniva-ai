"""KMS-style signing for the audit chain (U1, KTD9).

``KeyManagementService`` is the small surface the chain needs: sign a payload, verify a
signature (by key id, so old entries survive rotation), expose the active key id and its public
PEM. ``LocalDevKMS`` is the development/test implementation: RSA-2048 PSS key pairs on disk.
The factory ``get_kms()`` fails closed in production unless a real backend is configured.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import get_settings

logger = logging.getLogger(__name__)

_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)
_PROD_ENVIRONMENTS = ("production", "prod")


@runtime_checkable
class KeyManagementService(Protocol):
    """Signing backend for audit entries."""

    @property
    def key_id(self) -> str:
        """Identifier of the active signing key."""

    @property
    def public_key_pem(self) -> str:
        """PEM of the active public key."""

    def sign(self, data: bytes) -> bytes:
        """Return the raw signature over ``data``."""

    def verify(self, data: bytes, signature: bytes, key_id: Optional[str] = None) -> bool:
        """Verify ``signature`` over ``data`` with ``key_id`` (default: the active key)."""


class LocalDevKMS:
    """RSA-2048 PSS key pairs under ``keys_dir``. Development and test only."""

    def __init__(self, keys_dir: Path, key_id: Optional[str] = None):
        self._dir = Path(keys_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._key_id = key_id or self._read_active() or uuid.uuid4().hex[:16]
        self._ensure_key(self._key_id)
        self._write_active(self._key_id)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_pem(self) -> str:
        return self._load_or_create(self._key_id).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def sign(self, data: bytes) -> bytes:
        return self._load_or_create(self._key_id).sign(data, _PSS, hashes.SHA256())

    def verify(self, data: bytes, signature: bytes, key_id: Optional[str] = None) -> bool:
        try:
            public_key = self._load_or_create(key_id or self._key_id).public_key()
            public_key.verify(signature, data, _PSS, hashes.SHA256())
            return True
        except Exception as exc:  # missing key, bad base64, wrong key, tampered bytes
            logger.warning("Audit signature verification failed: %s", exc)
            return False

    # -- key storage ---------------------------------------------------------------------

    def _priv_path(self, key_id: str) -> Path:
        return self._dir / f"{key_id}.pem"

    def _pub_path(self, key_id: str) -> Path:
        return self._dir / f"{key_id}.pub.pem"

    def _active_path(self) -> Path:
        return self._dir / "active"

    def _read_active(self) -> Optional[str]:
        path = self._active_path()
        if path.exists():
            value = path.read_text().strip()
            return value or None
        return None

    def _write_active(self, key_id: str) -> None:
        self._active_path().write_text(key_id)

    def _ensure_key(self, key_id: str) -> None:
        if not self._priv_path(key_id).exists():
            self._load_or_create(key_id)

    def _load_or_create(self, key_id: str) -> rsa.RSAPrivateKey:
        path = self._priv_path(key_id)
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        self._pub_path(key_id).write_bytes(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        return private_key


_KMS_CACHE: dict[tuple[str, str], KeyManagementService] = {}


def get_kms() -> KeyManagementService:
    """Return the configured KMS, failing closed in production when none is set."""
    settings = get_settings()
    backend = (getattr(settings, "AUDIT_KMS_BACKEND", "") or "local").lower()
    if backend == "local":
        if getattr(settings, "ENVIRONMENT", "development") in _PROD_ENVIRONMENTS:
            raise RuntimeError(
                "AUDIT_KMS_BACKEND=local is refused in production; configure a real KMS backend."
            )
        keys_dir = str(Path(getattr(settings, "SESSION_BASE_PATH", "./sessions") or "./sessions") / ".audit_keys")
        cache_key = ("local", keys_dir)
        if cache_key not in _KMS_CACHE:
            _KMS_CACHE[cache_key] = LocalDevKMS(keys_dir)
        return _KMS_CACHE[cache_key]
    raise RuntimeError(f"Unsupported AUDIT_KMS_BACKEND: {backend}")
