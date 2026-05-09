from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
from app.config import get_settings


class EncryptionService:
    """
    Handles encryption/decryption of sensitive data (API keys, credentials)
    Uses Fernet (symmetric encryption) with key derived from SECRET_KEY
    """

    def __init__(self):
        settings = get_settings()

        # Validate ENCRYPTION_SALT in production
        if settings.ENVIRONMENT in ["production", "prod"]:
            insecure_salt_patterns = [
                "default",
                "change",
                "changeme",
                "salt",
                "test",
                "example",
                "oniva",
            ]
            salt_lower = settings.ENCRYPTION_SALT.lower()
            if any(pattern in salt_lower for pattern in insecure_salt_patterns):
                raise ValueError(
                    f"\n"
                    f"{'=' * 70}\n"
                    f"ENCRYPTION_SALT VALIDATION FAILED\n"
                    f"{'=' * 70}\n"
                    f"\n"
                    f"ENCRYPTION_SALT contains insecure default value in production.\n"
                    f"Generate a secure salt using:\n"
                    f"  python scripts/generate_secrets.py\n"
                    f"\n"
                    f"Then store ENCRYPTION_SALT in your secrets manager.\n"
                    f"{'=' * 70}\n"
                )

        # Convert hex-encoded salt from settings to bytes
        # In production, ENCRYPTION_SALT should be a 32-byte hex string from generate_secrets.py
        try:
            salt = bytes.fromhex(settings.ENCRYPTION_SALT)
        except (ValueError, AttributeError):
            # Fallback for development: use settings value as-is
            salt = settings.ENCRYPTION_SALT.encode() if isinstance(settings.ENCRYPTION_SALT, str) else settings.ENCRYPTION_SALT

        # Derive encryption key from SECRET_KEY
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
        self.cipher = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext"""
        if not plaintext:
            return ""
        encrypted = self.cipher.encrypt(plaintext.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext and return plaintext"""
        if not ciphertext:
            return ""
        decoded = base64.urlsafe_b64decode(ciphertext.encode())
        decrypted = self.cipher.decrypt(decoded)
        return decrypted.decode()


# Singleton instance
_encryption_service = None


def get_encryption_service() -> EncryptionService:
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
