"""WhatsApp lead-qualification agent — Pin 3 of the blueprint.

Implements the dialogue flow from §6.1 as an explicit state machine:

    GREETING -> QUALIFYING -> PRESENTING -> BOOKING -> DONE

A state machine rather than a free LLM loop, deliberately: the qualification
path is fixed, auditable and cheap, and it cannot hallucinate a price or a
possession date. The optional LLM layer only rephrases — it is never the
source of a property fact.

The blueprint's system prompt (§6.2) is included verbatim as AGENT_SYSTEM and
is used when LLM_PROVIDER is set to a real provider.
"""

import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .config import settings
from .llm import get_llm
from .models import Lead, SiteVisit
from .search import find_locality, parse_constraints, search

AGENT_SYSTEM = """You are an expert, polite and efficient AI real estate assistant.
Your job is to qualify buyer intent, answer project questions accurately from the
supplied data, and schedule physical site visits.

BEHAVIOUR RULES:
- Keep replies short, professional and conversational — under 60 words.
- Support English, Hindi and regional languages; match the user's language.
- Never fabricate project specs. If a detail is missing, offer to connect the
  buyer with a human advisor.
- Never quote a price, possession date or title status that is not in the
  supplied project data.
- Always close with a single clear question that advances the conversation.
"""

_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|sure|please|haan|ha|theek|done|book|schedule)\b", re.I)
_NO = re.compile(r"\b(no|not now|later|nope|nahi|maybe later)\b", re.I)



def _money(v: Optional[float]) -> str:
    if not v:
        return "price on request"
    lakh = v / 100000.0
    if lakh >= 100:
        return f"₹{lakh / 100:.2f} Cr".replace(".00 Cr", " Cr")
    return f"₹{lakh:.0f} L"


def get_or_create_lead(db: Session, phone: str) -> Lead:
    lead = db.query(Lead).filter(Lead.buyer_phone == phone).one_or_none()
    if lead is None:
        lead = Lead(buyer_phone=phone, stage="GREETING", status="NEW", chat_history=[])
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def _log(lead: Lead, role: str, text: str) -> None:
    history: List[Dict[str, str]] = list(lead.chat_history or [])
    history.append({"role": role, "text": text})
    lead.chat_history = history[-40:]


def _score(lead: Lead) -> int:
    """Intent score from what the buyer has actually given us."""
    score = 30
    if lead.preferred_location:
        score += 15
    if lead.budget_max:
        score += 25
    if lead.property_type:
        score += 10
    if lead.timeline:
        score += 10
    if lead.status == "VISIT_SCHEDULED":
        score = 95
    return min(score, 100)


def handle_message(db: Session, phone: str, text: str) -> Dict[str, Any]:
    """Advance the conversation one turn. Returns the reply and new state."""
    lead = get_or_create_lead(db, phone)
    _log(lead, "user", text)

    # Absorb anything the buyer volunteered, whatever stage we are in.
    constraints = parse_constraints(text)
    if constraints["max_price"]:
        lead.budget_max = constraints["max_price"]
    if constraints["property_type"]:
        lead.property_type = constraints["property_type"]
    locality = find_locality(text)
    if locality:
        lead.preferred_location = locality
    if re.search(r"\b(immediate|urgent|this month|ready)\b", text, re.I):
        lead.timeline = "immediate"
    elif re.search(r"\b(3|three|6|six)\s*month", text, re.I):
        lead.timeline = "3-6 months"

    reply, matches = _advance(db, lead, text)

    lead.intent_score = _score(lead)
    _log(lead, "agent", reply)
    db.commit()

    return {
        "reply": reply,
        "stage": lead.stage,
        "status": lead.status,
        "intent_score": lead.intent_score,
        "lead_id": lead.id,
        "matches": matches,
    }


def _advance(db: Session, lead: Lead, text: str):
    """State transitions. Returns (reply_text, matches)."""
    stage = lead.stage or "GREETING"

    # ---- GREETING ------------------------------------------------------
    if stage == "GREETING":
        lead.stage = "QUALIFYING"
        if lead.preferred_location or lead.budget_max:
            return _qualify_or_present(db, lead)
        return (
            "Hello! Thanks for reaching out. I'm your digital property assistant "
            "and I can help you find verified plots, apartments or villas. "
            "To share the best options — what location and property type are you "
            "looking for?",
            [],
        )

    # ---- QUALIFYING ----------------------------------------------------
    if stage == "QUALIFYING":
        return _qualify_or_present(db, lead)

    # ---- PRESENTING ----------------------------------------------------
    if stage == "PRESENTING":
        if _YES.search(text):
            lead.stage = "BOOKING"
            return (
                "Great. Would Saturday or Sunday morning suit you better for the "
                "site visit?",
                [],
            )
        if _NO.search(text):
            lead.stage = "DONE"
            lead.status = "QUALIFIED"
            return (
                "No problem — I've saved your preferences and our advisor will "
                "follow up with new matches. Anything else you'd like to know "
                "about these properties?",
                [],
            )
        # Treated as a refined brief: search again.
        return _present(db, lead, text)

    # ---- BOOKING -------------------------------------------------------
    if stage == "BOOKING":
        slot = text.strip()
        visit = SiteVisit(lead_id=lead.id, scheduled_for=slot, status="REQUESTED")
        db.add(visit)
        lead.status = "VISIT_SCHEDULED"
        lead.stage = "DONE"
        return (
            f"Perfect — your site visit is noted for {slot}. Our site manager will "
            "confirm and send the map pin shortly. See you then!",
            [],
        )

    # ---- DONE ----------------------------------------------------------
    if _YES.search(text) and lead.status != "VISIT_SCHEDULED":
        lead.stage = "BOOKING"
        return ("Happy to arrange that. Which day works best for a site visit?", [])

    return (
        "Thanks! An advisor from our team will be in touch. If you'd like to see "
        "other options, tell me your budget and preferred area.",
        [],
    )


def _qualify_or_present(db: Session, lead: Lead):
    """Ask for whatever is still missing; present as soon as we have enough."""
    if not lead.preferred_location and not lead.property_type:
        return (
            "Happy to help. Which area are you looking in, and is it a plot, "
            "apartment or villa?",
            [],
        )
    if not lead.budget_max:
        return (
            "Got it. What budget range do you have in mind, and are you looking "
            "to buy immediately or within 3–6 months?",
            [],
        )
    return _present(db, lead, None)


def _present(db: Session, lead: Lead, refinement: Optional[str]):
    """Run the vector search and present up to two matches."""
    brief_parts = [
        lead.property_type or "",
        f"in {lead.preferred_location}" if lead.preferred_location else "",
        f"under {lead.budget_max / 100000:.0f} lakh" if lead.budget_max else "",
        refinement or "",
    ]
    brief = " ".join(p for p in brief_parts if p).strip() or "property in Bengaluru"

    result = search(db, brief, limit=2, max_price=lead.budget_max, explain=True)
    matches = result["matches"]

    if not matches:
        lead.stage = "DONE"
        lead.status = "UNMATCHED"
        return (
            "I don't have a verified match for that brief right now. I've logged "
            "your requirement — our team sources off-platform stock too and will "
            "come back to you. Would you like to widen the budget or the area?",
            [],
        )

    lead.stage = "PRESENTING"
    lead.status = "QUALIFIED"

    lines = []
    for m in matches:
        line = f"• {m['title']} — {m['location_name']}, {_money(m['price_inr'])}"
        if m.get("possession"):
            line += f", {m['possession'].lower()}"
        if m.get("is_verified"):
            line += ", title verified"
        lines.append(line)

    body = "Based on that, these fit best:\n" + "\n".join(lines)
    reply = body + "\n\nWould you like me to book a site visit this weekend?"

    if get_llm().generative:
        try:
            reply = get_llm().reply(
                AGENT_SYSTEM,
                [{"role": "user", "content":
                  f"Buyer brief: {brief}\n\nVerified matches:\n" + "\n".join(lines) +
                  "\n\nWrite the next WhatsApp message presenting these and asking "
                  "if they want a site visit. Under 60 words."}],
            )
        except Exception:
            pass  # fall back to the deterministic copy above

    return reply, matches
