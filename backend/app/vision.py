"""Floor-plan reading.

A buyer uploads the plan of the home they want — a 2D drawing or a 3D render —
and it is read into a structured brief, which then drives the ordinary hybrid
search. This is the one feature in the product that genuinely needs a vision
model; there is no rule-based substitute for reading a drawing.

So it degrades honestly: with no vision-capable provider configured the
endpoint returns `available: false` and a plain reason, rather than pretending
to have read the image. It never guesses dimensions.
"""

import base64
import json
import logging
from typing import Any, Dict, Optional

from .config import settings

log = logging.getLogger("uvicorn.error")

MAX_BYTES = 8 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_PROMPT = """This image is a floor plan or a 3D render of a home a buyer wants.

Read only what is actually visible. Do not estimate a dimension that is not
drawn or labelled — omit it instead. If the image is not a floor plan or is
unreadable, say so.

Reply with ONLY this JSON:
{
  "is_floor_plan": true|false,
  "bedrooms": integer or null,
  "bathrooms": integer or null,
  "built_up_sqft": integer or null,
  "floors": integer or null,
  "layout_notes": ["short factual observations, max 6"],
  "unreadable": ["what you could not make out"],
  "search_brief": "one sentence describing this home as a buyer would ask for it"
}"""


def available() -> bool:
    """Vision needs a real multimodal provider — the stub cannot see."""
    if settings.LLM_PROVIDER == "anthropic":
        return bool(settings.ANTHROPIC_API_KEY)
    if settings.LLM_PROVIDER == "openai":
        return bool(settings.OPENAI_API_KEY)
    return False


def unavailable_reason() -> str:
    if settings.LLM_PROVIDER == "stub":
        return ("Plan reading needs a vision model. Set LLM_PROVIDER=anthropic "
                "or openai with the matching API key. You can still describe "
                "the home in words.")
    return "The configured model provider has no API key set."


def _parse(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if "```" in text:
        text = max(text.split("```"), key=len).lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("model returned no JSON object")
    return json.loads(text[start:end + 1])


def read_plan(image_bytes: bytes, media_type: str,
              note: Optional[str] = None) -> Dict[str, Any]:
    """Return a structured reading of the plan. Raises ValueError on bad input."""
    if media_type not in ALLOWED:
        raise ValueError(f"Unsupported image type {media_type!r}. "
                         f"Use JPEG, PNG, WebP or GIF.")
    if len(image_bytes) > MAX_BYTES:
        raise ValueError(f"Image is {len(image_bytes) // 1024 // 1024} MB; "
                         f"the limit is {MAX_BYTES // 1024 // 1024} MB.")
    if not available():
        return {"available": False, "reason": unavailable_reason()}

    prompt = _PROMPT
    if note:
        prompt += f"\n\nThe buyer also said: {note[:500]}"

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    if settings.LLM_PROVIDER == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    else:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:{media_type};base64,{b64}"}},
            ]}],
        )
        raw = resp.choices[0].message.content or ""

    try:
        data = _parse(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("Plan reading returned unparseable output: %s", exc)
        return {"available": True, "is_floor_plan": False,
                "error": "The plan could not be read reliably. Try a clearer image."}

    data["available"] = True
    return data


def brief_from(reading: Dict[str, Any], note: Optional[str] = None) -> str:
    """Turn a plan reading into a search brief."""
    if reading.get("search_brief"):
        brief = str(reading["search_brief"])
    else:
        bits = []
        if reading.get("bedrooms"):
            bits.append(f"{reading['bedrooms']}BHK")
        if reading.get("built_up_sqft"):
            bits.append(f"{reading['built_up_sqft']} sqft")
        brief = " ".join(bits) or "home"
    return f"{brief} {note}".strip() if note else brief
