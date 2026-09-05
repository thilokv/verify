"""Outbound WhatsApp delivery via the Meta Graph API.

Inbound webhooks were handled from the start; without this the agent computed
a reply and dropped it. Sending closes the loop.

Delivery is best-effort and must never take the webhook down: Meta retries a
non-200 webhook, which would re-run the whole conversation turn and double-send.
So every failure here is logged and swallowed, and the caller still returns 200.

With no WHATSAPP_TOKEN configured this runs in dry-run mode: the message is
logged and recorded as "dry_run" rather than silently vanishing.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .config import settings

log = logging.getLogger("uvicorn.error")

GRAPH_VERSION = "v21.0"
_TIMEOUT_SECONDS = 8


def is_live() -> bool:
    return bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_ID)


def send_text(to: str, body: str) -> Dict[str, Any]:
    """Send one text message. Returns a delivery record; never raises."""
    if not body:
        return {"status": "skipped", "reason": "empty body"}

    # WhatsApp hard-caps a text body at 4096 characters.
    if len(body) > 4096:
        body = body[:4093] + "..."

    if not is_live():
        log.info("[whatsapp dry-run] -> %s: %s", to, body.replace("\n", " ")[:160])
        return {"status": "dry_run", "to": to, "chars": len(body)}

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        message_id = (data.get("messages") or [{}])[0].get("id")
        return {"status": "sent", "to": to, "message_id": message_id}

    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:
            pass
        # 131047: outside the 24-hour customer service window — needs a
        # pre-approved template, not a free-form text. Worth naming explicitly
        # because it is the most common production failure.
        log.error("WhatsApp send failed %s for %s: %s", exc.code, to, detail)
        return {"status": "failed", "to": to, "http_status": exc.code, "detail": detail}

    except Exception as exc:                      # network, DNS, timeout
        log.error("WhatsApp send error for %s: %s", to, exc)
        return {"status": "failed", "to": to, "detail": str(exc)}
