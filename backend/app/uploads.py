"""File uploads — property photos and title documents.

This endpoint accepts files from anyone with a listing id, so it is written
defensively:

  * The client filename is NEVER used on disk. It is attacker-controlled and
    is the classic path-traversal vector ("../../etc/passwd"). We generate a
    random name and keep the original only as a display label.
  * Content type is checked against the file's actual magic bytes, not the
    Content-Type header, which the client chooses freely.
  * SVG is refused. It is an image to a browser and a script host to an
    attacker; serving user SVG from your own origin is stored XSS.
  * Files are served back with Content-Disposition: attachment and a fixed
    content type, so nothing uploaded here executes in a viewer's browser.
  * Size is capped before the bytes ever reach disk.

Local disk is a pilot-scale decision. Move to object storage with signed URLs
before this is exposed to real sellers.
"""

import os
import re
import secrets
from typing import Dict, Optional, Tuple

MAX_BYTES = 10 * 1024 * 1024

# magic-byte prefixes -> (extension, canonical media type)
_SIGNATURES: Tuple[Tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"%PDF-", ".pdf", "application/pdf"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)

KINDS = {"photo", "document"}

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._ -]")


def _detect(data: bytes) -> Optional[Tuple[str, str]]:
    for prefix, ext, media in _SIGNATURES:
        if data.startswith(prefix):
            return ext, media
    # WebP: "RIFF????WEBP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def safe_label(name: str) -> str:
    """A display-only label. Never used to build a path."""
    base = os.path.basename(name or "")
    base = _SAFE_LABEL.sub("_", base).strip() or "upload"
    return base[:80]


def store(root: str, property_id: int, kind: str, filename: str,
          data: bytes, seal=None) -> Dict[str, object]:
    """Validate and write. Raises ValueError with a message fit for the user."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(
            f"File is {len(data) / 1048576:.1f} MB; the limit is "
            f"{MAX_BYTES // 1048576} MB.")

    detected = _detect(data)
    if detected is None:
        raise ValueError(
            "Unrecognised file. Upload a JPEG, PNG, WebP, GIF or PDF. "
            "(The file's actual content is checked, not its name.)")
    ext, media = detected

    if kind == "document" and media.startswith("image/"):
        # Allowed: photographs of documents are the norm. Noted so the
        # reviewer knows OCR quality may be poor.
        pass

    folder = os.path.join(root, str(int(property_id)))
    os.makedirs(folder, exist_ok=True)

    stored_name = secrets.token_hex(16) + ext
    path = os.path.join(folder, stored_name)

    payload = seal(data) if seal else data
    encrypted = bool(seal) and payload is not data

    # 0600: readable only by the service account, not by other local users.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)

    return {
        "stored_name": stored_name,
        "label": safe_label(filename),
        "media_type": media,
        "bytes": len(data),          # size of the ORIGINAL, not the ciphertext
        "kind": kind,
        "path": path,
        "encrypted": encrypted,
    }


def resolve(root: str, property_id: int, stored_name: str) -> str:
    """Map a stored name back to a path, refusing anything that escapes root."""
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z]{3,4}", stored_name or ""):
        raise ValueError("Bad file reference.")
    folder = os.path.realpath(os.path.join(root, str(int(property_id))))
    path = os.path.realpath(os.path.join(folder, stored_name))
    if not path.startswith(os.path.realpath(root) + os.sep):
        raise ValueError("Bad file reference.")
    if not os.path.exists(path):
        raise ValueError("No such file.")
    return path
