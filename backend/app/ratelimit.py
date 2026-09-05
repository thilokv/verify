"""Rate limiting.

Three things this stops, in order of how much they cost you:

  Wallet drain   /plan/match and /search call a paid model. Unmetered, one
                 script empties the API budget overnight.
  Catalogue theft  A competitor scraping every listing and every price.
  Contact spam   Flooding the WhatsApp agent to create thousands of leads.

In-process fixed windows, deliberately: no Redis dependency for a pilot. That
means limits are PER PROCESS — run four workers and the effective limit is
four times what is written here. Move the counters to Redis before scaling
out, or the numbers below become a comfortable fiction.
"""

import threading
import time
from typing import Dict, Optional, Tuple

# path prefix -> (max requests, window seconds)
LIMITS: Tuple[Tuple[str, int, int], ...] = (
    ("/api/v1/plan/match", 10, 3600),        # paid vision call
    ("/api/v1/listings/submit", 5, 3600),    # public write
    ("/api/v1/uploads", 40, 3600),
    ("/api/v1/chat", 60, 3600),
    ("/api/v1/seller/chat", 120, 3600),
    ("/api/v1/search", 120, 600),
    ("/api/v1/report", 10, 3600),
)

_lock = threading.Lock()
_hits: Dict[str, Tuple[int, float]] = {}


def _rule_for(path: str) -> Optional[Tuple[str, int, int]]:
    for prefix, limit, window in LIMITS:
        if path.startswith(prefix) or (prefix in path):
            return prefix, limit, window
    return None


def check(client: str, path: str) -> Optional[Dict[str, int]]:
    """Return None if allowed, or {retry_after, limit, window} if throttled."""
    rule = _rule_for(path)
    if rule is None:
        return None
    prefix, limit, window = rule
    key = f"{client}|{prefix}"
    now = time.time()

    with _lock:
        count, started = _hits.get(key, (0, now))
        if now - started >= window:
            count, started = 0, now
        count += 1
        _hits[key] = (count, started)

        if len(_hits) > 20000:            # crude bound; restart clears it
            for k, (_, t) in list(_hits.items()):
                if now - t > 3600:
                    _hits.pop(k, None)

        if count > limit:
            return {"retry_after": int(window - (now - started)) + 1,
                    "limit": limit, "window": window}
    return None


def client_key(request) -> str:
    """Identify the caller.

    X-Forwarded-For is trusted ONLY because a reverse proxy is assumed in
    front. Exposed directly to the internet, a client can forge it and evade
    every limit here — terminate at a proxy that overwrites the header.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", "") or "unknown"


def reset() -> None:
    with _lock:
        _hits.clear()
