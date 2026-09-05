"""Encryption at rest for uploaded title documents.

Title documents contain a seller's name, address and property identifiers.
A stolen backup or a misconfigured bucket leaks all of it at once, so the
bytes are encrypted before they touch disk.

Fernet = AES-128-CBC with an HMAC-SHA256 authentication tag. Authenticated,
so a tampered ciphertext fails to decrypt rather than returning garbage.

BE CLEAR ABOUT WHAT THIS PROTECTS AGAINST
    Stolen disk, stolen backup, a snapshot handed to the wrong person: yes.
    An attacker who compromises the running application: NO — the process
    must hold the key to serve files, so anything that owns the process owns
    the key. Encryption at rest is not a substitute for access control; it
    sits behind it.

KEY MANAGEMENT IS THE WHOLE GAME
    A key sitting next to the ciphertext is decoration. DOCUMENT_KEY belongs
    in a secrets manager (AWS KMS, GCP Secret Manager, Vault), injected at
    boot, never committed and never written next to the uploads directory.
    With no key set the service stores documents in the clear and says so at
    /api/v1/health — it does not pretend.
"""

import base64
import hashlib
import logging
from typing import Optional

from .config import settings

log = logging.getLogger("uvicorn.error")

_MAGIC = b"VLT1"          # so an encrypted blob is identifiable on disk
_fernet = None
_checked = False


def _load():
    global _fernet, _checked
    if _checked:
        return _fernet
    _checked = True

    raw = (settings.DOCUMENT_KEY or "").strip()
    if not raw:
        log.warning(
            "DOCUMENT_KEY is not set — uploaded title documents are stored "
            "UNENCRYPTED. Set a key before accepting real sellers' papers.")
        return None

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        log.error("DOCUMENT_KEY is set but the 'cryptography' package is not "
                  "installed. Storing in the clear. pip install cryptography")
        return None

    try:
        # Accept either a real Fernet key or any passphrase, derived to 32 bytes.
        if len(raw) == 44 and raw.endswith("="):
            key = raw.encode()
        else:
            key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        _fernet = Fernet(key)
        log.info("Document encryption at rest is ON.")
    except Exception as exc:
        log.error("DOCUMENT_KEY could not be used (%s). Storing in the clear.", exc)
        _fernet = None
    return _fernet


def enabled() -> bool:
    return _load() is not None


def seal(data: bytes) -> bytes:
    f = _load()
    if f is None:
        return data
    return _MAGIC + f.encrypt(data)


def unseal(blob: bytes) -> bytes:
    """Decrypt if sealed. Plaintext written before a key existed still reads."""
    if not blob.startswith(_MAGIC):
        return blob
    f = _load()
    if f is None:
        raise ValueError(
            "This document is encrypted but DOCUMENT_KEY is not set. "
            "Restore the key that was in use when it was uploaded.")
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(blob[len(_MAGIC):])
    except InvalidToken:
        raise ValueError(
            "Document failed authentication — it was encrypted with a "
            "different key, or the stored bytes were tampered with.")
