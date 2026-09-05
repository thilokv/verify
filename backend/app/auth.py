"""Staff authentication.

Secure by default. If STAFF_API_KEYS is unset the service does NOT fall open —
it generates a random key at boot and prints it once to the log. Set
REQUIRE_AUTH=false to disable deliberately (tests and throwaway demos only).

Two tiers:
  public  search, chat, health, read-only inventory
  staff   everything that writes, plus anything exposing buyer PII

Buyer contact data sits behind staff auth because lead rows carry phone
numbers, and DPDP Act 2023 obligations attach from the first stored lead.
This is an API-key gate, not an identity system: it authenticates the caller
as staff, it does not tell you which person acted. Put a real IdP in front
before more than a handful of people hold a key.
"""

import logging
import secrets
from typing import List, Optional

from fastapi import Header, HTTPException, status

from .config import settings

log = logging.getLogger("uvicorn.error")

_generated_key: Optional[str] = None


def _configured_keys() -> List[str]:
    raw = settings.STAFF_API_KEYS or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def bootstrap_keys() -> None:
    """Called once at startup. Announces the auth posture loudly."""
    global _generated_key

    if not settings.REQUIRE_AUTH:
        log.warning(
            "AUTH DISABLED (REQUIRE_AUTH=false). Every write endpoint and all "
            "buyer contact data are open to anyone who can reach this port. "
            "Never run this way outside local development."
        )
        return

    if _configured_keys():
        log.info("Staff auth enabled with %d configured key(s).", len(_configured_keys()))
        return

    _generated_key = "staff_" + secrets.token_urlsafe(24)
    log.warning(
        "No STAFF_API_KEYS configured — generated an ephemeral staff key for "
        "this process only:\n\n    %s\n\n"
        "It changes on every restart. Set STAFF_API_KEYS in the environment "
        "for anything persistent.",
        _generated_key,
    )


def _valid(candidate: str) -> bool:
    keys = _configured_keys()
    if _generated_key:
        keys = keys + [_generated_key]
    # compare_digest against every key so a wrong key costs the same time as
    # a right one; short-circuiting here leaks key length and prefixes.
    return any(secrets.compare_digest(candidate, k) for k in keys)


def require_staff(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> str:
    """FastAPI dependency. Accepts `Authorization: Bearer <key>` or `X-API-Key`."""
    if not settings.REQUIRE_AUTH:
        return "auth-disabled"

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Staff credentials required. Send 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not _valid(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid staff key.",
        )

    return "staff"
