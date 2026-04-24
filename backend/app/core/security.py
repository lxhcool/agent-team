"""Security utilities for API key encryption."""

import base64
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings


def _get_fernet() -> Fernet:
    """Derive a Fernet key from the configured encryption key."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"team-agent-salt",  # Fixed salt for deterministic key derivation
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(settings.encryption_key.encode()))
    return Fernet(key)


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for storage."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an API key from storage."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


def mask_api_key(key: str) -> str:
    """Mask an API key for display (e.g., sk-xxx...xxx)."""
    if len(key) <= 8:
        return "***"
    return key[:4] + "..." + key[-4:]
