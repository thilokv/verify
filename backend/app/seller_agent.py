"""Jarvis — the seller-side intake agent.

Same design as the buyer-side WhatsApp agent: an explicit state machine, not a
free LLM loop. A seller listing a property is a data-collection task with a
required set of fields, and a state machine collects them reliably, cheaply and
auditably. The optional LLM layer only rephrases; it never decides what has
been collected and never invents a field value.

    ASK_WHAT -> ASK_WHERE -> ASK_SIZE -> ASK_PRICE -> ASK_POSSESSION
             -> ASK_TITLE -> CONFIRM -> DONE

The listing it creates always enters unverified, exactly like the REST path —
there is no route into the catalogue that bypasses the verification gate.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .embeddings import embed_one
from .models import Property
from .search import find_locality

_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|sure|please|correct|right|haan|ha|go ahead|confirm|list it)\b", re.I)
_NO = re.compile(r"\b(no|nope|wrong|change|not right|nahi|edit)\b", re.I)
_SKIP = re.compile(r"\b(skip|don'?t know|dont know|not sure|na|n/a|none|later)\b", re.I)

_TYPES = {
    "Plot": ["plot", "land", "site", "parcel", "acre", "farm"],
    "Villa": ["villa", "bungalow", "independent", "row house"],
    "Flat": ["flat", "apartment", "condo", "bhk"],
    "Commercial": ["commercial", "office", "shop", "retail", "warehouse"],
}

_CONFIG = re.compile(r"\b(\d)\s*[- ]?\s*(?:bhk|bedroom|bed|br)\b", re.I)
_AREA = re.compile(r"\b([\d,]{2,7})\s*(?:sq\.?\s*ft|sqft|square feet|sft)\b", re.I)
_PRICE_CR = re.compile(r"([\d.]+)\s*(?:cr|crore)", re.I)
_PRICE_L = re.compile(r"([\d.]+)\s*(?:lakh|lac|l)\b", re.I)
_RERA = re.compile(r"\bPRM/[A-Z]{2}/RERA/[A-Za-z0-9/]+\b", re.I)

FIELDS = ["property_type", "locality", "config", "area_sqft", "price_inr",
          "possession", "khata", "rera_id"]


def _money(v: Optional[float]) -> str:
    if not v:
        return "—"
    lakh = v / 1e5
    return f"₹{lakh/100:.2f} Cr".replace(".00 Cr", " Cr") if lakh >= 100 else f"₹{lakh:.0f} L"


def extract(text: str, draft: Dict[str, Any]) -> Dict[str, Any]:
    """Pull every field we can from one message. Never overwrites with None."""
    low = (text or "").lower()

    for canonical, words in _TYPES.items():
        if any(w in low for w in words):
            draft["property_type"] = canonical
            break

    loc = find_locality(text)
    if loc:
        draft["locality"] = loc

    m = _CONFIG.search(text)
    if m:
        draft["config"] = f"{m.group(1)}BHK"

    m = _AREA.search(text)
    if m:
        draft["area_sqft"] = float(m.group(1).replace(",", ""))

    m = _PRICE_CR.search(text)
    if m:
        draft["price_inr"] = float(m.group(1)) * 1e7
    else:
        m = _PRICE_L.search(text)
        if m:
            draft["price_inr"] = float(m.group(1)) * 1e5

    if re.search(r"\bready|immediate|move[- ]in|completed\b", low):
        draft["possession"] = "Ready to move"
    elif re.search(r"\bunder construction|building|not ready\b", low):
        draft["possession"] = "Under construction"

    if re.search(r"\ba[\s-]?khata\b", low):
        draft["khata"] = "A-Khata"
    elif re.search(r"\bb[\s-]?khata\b", low):
        draft["khata"] = "B-Khata"

    m = _RERA.search(text)
    if m:
        draft["rera_id"] = m.group(0)

    return draft


def _summary(d: Dict[str, Any]) -> str:
    rows = [
        ("Type", d.get("property_type")),
        ("Locality", d.get("locality")),
        ("Configuration", d.get("config")),
        ("Area", f"{int(d['area_sqft'])} sq ft" if d.get("area_sqft") else None),
        ("Asking price", _money(d.get("price_inr"))),
        ("Possession", d.get("possession")),
        ("Khata", d.get("khata")),
        ("RERA", d.get("rera_id")),
    ]
    return "\n".join(f"{k}: {v}" for k, v in rows if v and v != "—")


def _next_question(d: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return (stage, question) for the first field still missing."""
    if not d.get("property_type"):
        return "ASK_WHAT", (
            "Let's get your property listed. What are you selling — an apartment, "
            "a villa, a plot, or commercial space?")
    if not d.get("locality"):
        return "ASK_WHERE", "Which locality is it in?"
    if d.get("property_type") == "Flat" and not d.get("config"):
        return "ASK_SIZE", "How many bedrooms — 2BHK, 3BHK?"
    if not d.get("area_sqft"):
        return "ASK_SIZE", "Roughly how many square feet?"
    if not d.get("price_inr"):
        return "ASK_PRICE", "What price are you asking? You can say it as '1.2 crore' or '85 lakh'."
    if not d.get("possession"):
        return "ASK_POSSESSION", "Is it ready to move, or still under construction?"
    if not d.get("khata"):
        return "ASK_TITLE", (
            "Last one, and it matters most to buyers: is the khata A-Khata or "
            "B-Khata? Say 'skip' if you're not sure.")
    return None, ""


def handle(db: Session, state: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Advance the intake one turn.

    `state` is held by the caller (the browser) and passed back each turn, so
    the agent stays stateless server-side — no session table for a flow the
    seller may abandon halfway.
    """
    draft: Dict[str, Any] = dict(state.get("draft") or {})
    stage: str = state.get("stage") or "ASK_WHAT"
    created: Optional[Dict[str, Any]] = None

    if stage == "ASK_TITLE" and _SKIP.search(text or ""):
        draft["khata"] = "Not stated"
    else:
        draft = extract(text or "", draft)

    # ---- confirmation ---------------------------------------------------
    if stage == "CONFIRM":
        if _YES.search(text or ""):
            prop = Property(
                title=_title_for(draft),
                property_type=draft.get("property_type"),
                config=draft.get("config"),
                location_name=draft.get("locality"),
                city="Bengaluru",
                price_inr=draft.get("price_inr"),
                area_sqft=draft.get("area_sqft"),
                possession=draft.get("possession"),
                khata=None if draft.get("khata") == "Not stated" else draft.get("khata"),
                rera_id=draft.get("rera_id"),
                description=draft.get("description"),
                amenities=[],
                # Never trusted on entry — same rule as the REST path.
                is_verified=False,
                verification_note=(
                    "Listed via seller intake. Encumbrance certificate and khata "
                    "extract not yet pulled; no advocate opinion on file."),
            )
            db.add(prop)
            db.flush()
            prop.embedding = embed_one(prop.embedding_text())
            db.commit()
            db.refresh(prop)
            created = prop.to_public()

            return {
                "reply": (
                    f"Done — '{prop.title}' is listed, and it shows as "
                    "verification pending, so buyers cannot see it yet.\n\n"
                    "Now the part that actually protects everyone. Below are "
                    "the ownership questions and the documents Karnataka "
                    "conveyancing normally needs. Answer honestly — a wrong "
                    "answer here does not stop a sale, it just means the "
                    "problem surfaces after money has moved, which is far "
                    "worse for you than for the buyer.\n\n"
                    "One rule: never type an Aadhaar, PAN or account number "
                    "anywhere in this app. We only need to know which "
                    "documents you hold."),
                "stage": "VERIFY",
                "draft": draft,
                "created": created,
                "summary": _summary(draft),
            }

        if _NO.search(text or ""):
            return {
                "reply": "No problem — tell me what to change and I'll update it.",
                "stage": "REVISING", "draft": draft, "created": None,
                "summary": _summary(draft),
            }

    # ---- collect --------------------------------------------------------
    next_stage, question = _next_question(draft)

    if next_stage is None:
        return {
            "reply": "Here's what I have. Shall I add it to the catalogue?",
            "stage": "CONFIRM",
            "draft": draft,
            "created": None,
            "summary": _summary(draft),
        }

    return {
        "reply": question,
        "stage": next_stage,
        "draft": draft,
        "created": None,
        "summary": _summary(draft),
    }


def _title_for(d: Dict[str, Any]) -> str:
    # Not .capitalize() — it lowercases the rest and turns "3BHK" into "3bhk".
    config = d.get("config")          # already "3BHK"
    kind = (d.get("property_type") or "Property")
    head = f"{config} {kind.lower()}" if config else kind
    head = head[:1].upper() + head[1:]
    where = d.get("locality")
    return f"{head}, {where}" if where else head
