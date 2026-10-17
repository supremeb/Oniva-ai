"""Webhook secret handling: generate, encrypt (Fernet via the shared encryption service), decrypt, mask.

``auth_config`` stores ``secret_enc`` (hmac) or ``key_enc`` (api_key), never the plaintext (plan 003 KTD8).
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from app.core.encryption import get_encryption_service

logger = logging.getLogger(__name__)

SECRET_KEYS = {"secret": "secret_enc", "key": "key_enc"}


def generate_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def encrypt_secret(plaintext: str) -> str:
    return get_encryption_service().encrypt(plaintext)


def decrypt_secret(ciphertext: Optional[str]) -> Optional[str]:
    """Plaintext of an encrypted value, or None when it is missing or cannot be decrypted."""
    if not ciphertext:
        return None
    try:
        return get_encryption_service().decrypt(ciphertext)
    except Exception:
        logger.warning("webhook secret could not be decrypted")
        return None


def mask(value: Optional[str]) -> str:
    return "" if not value else "****" + value[-4:]


def seal_auth_config(config: Optional[dict]) -> dict:
    """Copy of ``config`` with every plaintext secret/key replaced by its encrypted ``*_enc`` twin."""
    sealed = dict(config or {})
    for plain, enc in SECRET_KEYS.items():
        value = sealed.pop(plain, None)
        if value:
            sealed[enc] = encrypt_secret(str(value))
    return sealed


def resolve_secret(auth_config: Optional[dict], plain: str, enc: str) -> Optional[str]:
    """The usable secret: decrypted ``*_enc``; a not-yet-migrated plaintext value is still honoured."""
    config: Any = auth_config or {}
    if config.get(enc):
        return decrypt_secret(config[enc])
    legacy = config.get(plain)
    return str(legacy) if legacy else None
