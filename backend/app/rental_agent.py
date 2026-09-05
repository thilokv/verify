"""Ultron — the rental-side intake agent.

Jarvis lists a property for sale. Ultron lists one for rent, and a rental is
not a small sale: the money at risk is the deposit, the paperwork is thinner,
and the fraud is faster. So this agent does two jobs at once — it collects the
listing, and it checks the terms against Karnataka law while collecting them.

Same discipline as Jarvis: an explicit state machine, not a free LLM loop. A
letting is a data-collection task with a required set of fields.

    ASK_WHAT -> ASK_WHERE -> ASK_CONFIG -> ASK_RENT -> ASK_DEPOSIT
             -> ASK_FURNISHING -> ASK_TERM -> CONFIRM -> DONE

Two things it will argue with a landlord about, because both are common in
Bengaluru and both are now unlawful or unwise:

  Deposit    Ten months' deposit was the Bengaluru norm for years. The Model
             Tenancy Act 2021 caps a residential deposit at two months' rent.
             Ultron flags anything above that rather than quietly listing it.

  Lease term Eleven months is the standard term precisely because a lease of
             twelve months or more must be registered. Ultron says so instead
             of letting a landlord stumble into an unregistered long lease.

The listing it creates is listing_type=RENT, so it can never appear in buyer
search, which sells things.
"""

import datetime as dt
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from .embeddings import embed_one
from .models import Property, RentalListing, utcnow

_YES = re.compile(r"\b(yes|yeah|yep|ok|okay|sure|please|correct|right|haan|ha|go ahead|confirm|list it)\b", re.I)
_NO = re.compile(r"\b(no|nope|wrong|change|not right|nahi|edit)\b", re.I)
_SKIP = re.compile(r"\b(skip|don'?t know|dont know|not sure|na|n/a|none|later)\b", re.I)

_TYPES = {
    "Villa": ["villa", "bungalow", "independent house", "row house"],
    "Commercial": ["commercial", "office", "shop", "retail", "warehouse", "godown"],
    "Flat": ["flat", "apartment", "condo", "bhk", "studio"],
}

_CONFIG = re.compile(r"\b(\d)\s*[- ]?\s*(?:bhk|bedroom|bed|br)\b", re.I)
_AREA = re.compile(r"\b([\d,]{2,7})\s*(?:sq\.?\s*ft|sqft|square feet|sft)\b", re.I)

# Rent and deposit are monthly figures in thousands, not crores. "25k", "25,000",
# "25 thousand", "1.2 lakh" for a large commercial let.
_THOUSAND = re.compile(r"([\d.]+)\s*(?:k|thousand)\b", re.I)
_LAKH = re.compile(r"([\d.]+)\s*(?:lakh|lac)\b", re.I)
_PLAIN = re.compile(r"(?:₹|rs\.?\s*)?([\d][\d,]{2,8})(?!\s*(?:sq|sft|bhk))", re.I)
_MONTHS = re.compile(r"\b(\d{1,2})\s*month", re.I)

# Order matters and is load-bearing: "unfurnished" and "semi furnished" both
# CONTAIN "furnished", so the specific forms must be tested first or an
# unfurnished flat lists as Furnished — backwards, and in front of a tenant.
_FURNISHING = (
    ("Unfurnished", ["unfurnished", "un furnished", "bare shell", "bare",
                     "empty", "no furniture", "nothing"]),
    ("Semi-furnished", ["semi furnished", "semi-furnished", "semifurnished",
                        "semi", "partly", "partially"]),
    ("Furnished", ["fully furnished", "full furnished", "furnished"]),
)

# Landlords quote deposits in words far more often than digits.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# Model Tenancy Act 2021, s.11: residential deposit capped at two months' rent
# (commercial at six). Karnataka's own tenancy rules follow the same shape.
DEPOSIT_CAP_MONTHS = 2
DEPOSIT_CAP_MONTHS_COMMERCIAL = 6

# A lease of 12 months or more attracts compulsory registration under the
# Registration Act 1908, s.17. Eleven months is the standard dodge.
REGISTRATION_THRESHOLD_MONTHS = 12

FIELDS = ["property_type", "locality", "config", "area_sqft", "monthly_rent_inr",
          "deposit_inr", "furnishing", "lease_months"]


def _rupees(v: Optional[float]) -> str:
    if not v:
        return "—"
    return "₹" + f"{int(v):,}"


def _months(text: str) -> Optional[int]:
    """Read a count of months written as digits or as a word."""
    m = _MONTHS.search(text)
    if m:
        return int(m.group(1))
    for word, value in _WORD_NUMBERS.items():
        if re.search(rf"\b{word}\s*(?:months?|month)\b", text, re.I):
            return value
    if re.search(r"\ba\s+year\b|\bone\s+year\b", text, re.I):
        return 12
    m = re.search(r"(\d)\s*years?\b", text, re.I)
    if m:
        return int(m.group(1)) * 12
    return None


def _amount(text: str) -> Optional[float]:
    """Read a rupee amount written the way landlords write it."""
    m = _LAKH.search(text)
    if m:
        return float(m.group(1)) * 1e5
    m = _THOUSAND.search(text)
    if m:
        return float(m.group(1)) * 1000
    m = _PLAIN.search(text)
    if m:
        value = float(m.group(1).replace(",", ""))
        # A bare number under 1000 in a rent answer means thousands: "45" is
        # ₹45,000 a month, never ₹45.
        return value * 1000 if value < 1000 else value
    return None


def extract(text: str, draft: Dict[str, Any], stage: str = "") -> Dict[str, Any]:
    """Pull every field we can from one message. Never overwrites with None."""
    low = (text or "").lower()

    for canonical, words in _TYPES.items():
        if any(w in low for w in words):
            draft["property_type"] = canonical
            break

    m = _CONFIG.search(low)
    if m:
        draft["config"] = f"{m.group(1)}BHK"
    if "studio" in low:
        draft.setdefault("config", "Studio")

    m = _AREA.search(low)
    if m:
        draft["area_sqft"] = float(m.group(1).replace(",", ""))

    for canonical, words in _FURNISHING:
        if any(w in low for w in words):
            draft["furnishing"] = canonical
            break

    # Amounts are ambiguous out of context, so they are read only when the
    # question being answered is the one that asked for them.
    if stage == "ASK_RENT" and draft.get("monthly_rent_inr") is None:
        amount = _amount(low)
        if amount:
            draft["monthly_rent_inr"] = amount

    if stage == "ASK_DEPOSIT" and draft.get("deposit_inr") is None:
        months = _months(low)
        rent = draft.get("monthly_rent_inr")
        if months and rent:
            # "ten months" — the Bengaluru way of quoting a deposit.
            draft["deposit_inr"] = float(months) * rent
            draft["deposit_quoted_months"] = months
        else:
            amount = _amount(low)
            if amount:
                draft["deposit_inr"] = amount

    if stage == "ASK_TERM":
        months = _months(low)
        if months:
            draft["lease_months"] = months

    return draft


def compliance(draft: Dict[str, Any]) -> list:
    """Terms that are unlawful or unwise, checked while the landlord types.

    These are advisory. Ultron lists the property either way — refusing would
    just push the letting off-platform, where nobody sees the deposit at all.
    """
    notes = []
    rent = draft.get("monthly_rent_inr")
    deposit = draft.get("deposit_inr")
    months = draft.get("lease_months")
    commercial = draft.get("property_type") == "Commercial"

    if rent and deposit:
        cap_months = DEPOSIT_CAP_MONTHS_COMMERCIAL if commercial else DEPOSIT_CAP_MONTHS
        quoted = deposit / rent
        if quoted > cap_months + 0.01:
            notes.append({
                "severity": "high",
                "code": "DEPOSIT_OVER_CAP",
                "title": f"Deposit is {quoted:.0f} months' rent",
                "detail": (
                    f"The Model Tenancy Act 2021 caps a "
                    f"{'commercial' if commercial else 'residential'} security "
                    f"deposit at {cap_months} months' rent — here "
                    f"{_rupees(cap_months * rent)}. Ten months was the old "
                    f"Bengaluru norm and is exactly what the Act was written to "
                    f"stop. A tenant can contest the excess, and you would be "
                    f"holding money you have to return."),
            })

    if months and months >= REGISTRATION_THRESHOLD_MONTHS:
        notes.append({
            "severity": "medium",
            "code": "LEASE_NEEDS_REGISTRATION",
            "title": f"A {months}-month lease must be registered",
            "detail": (
                "Under s.17 of the Registration Act 1908 a lease of twelve "
                "months or more has to be registered at the sub-registrar's "
                "office, with stamp duty. An unregistered long lease is not "
                "admissible as evidence if the letting is ever disputed. "
                "Eleven months is the standard term for this reason."),
        })

    return notes


def _summary(d: Dict[str, Any]) -> str:
    rent = d.get("monthly_rent_inr")
    deposit = d.get("deposit_inr")
    rows = [
        ("Type", d.get("property_type")),
        ("Locality", d.get("locality")),
        ("Configuration", d.get("config")),
        ("Area", f"{int(d['area_sqft'])} sq ft" if d.get("area_sqft") else None),
        ("Rent", f"{_rupees(rent)} / month" if rent else None),
        ("Deposit", (f"{_rupees(deposit)}"
                     + (f" ({deposit/rent:.0f} months)" if rent and deposit else ""))
         if deposit else None),
        ("Furnishing", d.get("furnishing")),
        ("Term", f"{d['lease_months']} months" if d.get("lease_months") else None),
    ]
    return "\n".join(f"{k}: {v}" for k, v in rows if v and v != "—")


def _next_question(d: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return (stage, question) for the first field still missing."""
    if not d.get("property_type"):
        return "ASK_WHAT", (
            "Let's get your place on the rental list. What are you letting — "
            "an apartment, a villa, or commercial space?")
    if not d.get("locality"):
        return "ASK_WHERE", "Which locality is it in?"
    if d.get("property_type") != "Commercial" and not d.get("config"):
        return "ASK_CONFIG", "How many bedrooms — 1BHK, 2BHK, 3BHK? Say 'studio' if it's a studio."
    if not d.get("area_sqft"):
        return "ASK_CONFIG", "Roughly how many square feet?"
    if d.get("monthly_rent_inr") is None:
        return "ASK_RENT", "What monthly rent are you asking? You can say '45k' or '45,000'."
    if d.get("deposit_inr") is None:
        return "ASK_DEPOSIT", (
            "How much security deposit? You can give an amount, or say it the "
            "usual way — 'two months', 'ten months'.")
    if not d.get("furnishing"):
        return "ASK_FURNISHING", "Is it furnished, semi-furnished, or unfurnished?"
    if not d.get("lease_months"):
        return "ASK_TERM", (
            "How long is the lease? Eleven months is the usual term here — "
            "twelve or more has to be registered.")
    return None, ""


def handle(db: Session, state: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Advance the rental intake one turn.

    `state` is held by the caller and passed back each turn, exactly like
    Jarvis, so the agent stays stateless server-side.
    """
    draft: Dict[str, Any] = dict(state.get("draft") or {})
    stage: str = state.get("stage") or "ASK_WHAT"
    created: Optional[Dict[str, Any]] = None

    if stage == "ASK_WHERE" and text.strip() and not draft.get("locality"):
        # The locality answer is free text — a name we do not want to parse
        # away, since Bengaluru localities rarely match a fixed list.
        cleaned = text.strip().strip(".,")
        if len(cleaned) <= 60:
            draft["locality"] = cleaned[:1].upper() + cleaned[1:]

    if stage == "ASK_FURNISHING" and _SKIP.search(text or ""):
        draft["furnishing"] = "Unfurnished"

    draft = extract(text or "", draft, stage)

    # ---- confirmation ---------------------------------------------------
    if stage == "CONFIRM":
        if _YES.search(text or ""):
            prop = Property(
                title=_title_for(draft),
                property_type=draft.get("property_type"),
                config=draft.get("config"),
                location_name=draft.get("locality"),
                city="Bengaluru",
                # A letting has no asking price. Leaving this null is what keeps
                # a rental out of every price filter in the sale catalogue.
                price_inr=None,
                area_sqft=draft.get("area_sqft"),
                possession="Ready to move",
                listing_type="RENT",
                is_verified=False,
                verification_note=(
                    "Listed via rental intake. Ownership not yet confirmed; no "
                    "advocate opinion on file."),
                description=(
                    f"{draft.get('furnishing', 'Unfurnished')} letting at "
                    f"{_rupees(draft.get('monthly_rent_inr'))} per month."),
                amenities=[],
            )
            db.add(prop)
            db.flush()
            prop.embedding = embed_one(prop.embedding_text())

            months = draft.get("lease_months") or 11
            rental = RentalListing(
                property_id=prop.id,
                monthly_rent_inr=draft.get("monthly_rent_inr"),
                deposit_inr=draft.get("deposit_inr"),
                lease_terms=f"{months} months",
                furnishing=draft.get("furnishing") or "Unfurnished",
                available_from=utcnow(),
                status="ACTIVE",
            )
            db.add(rental)
            db.commit()
            db.refresh(prop)
            db.refresh(rental)
            created = {"property": prop.to_public(), "rental_id": rental.id}

            notes = compliance(draft)
            tail = ""
            if notes:
                tail = ("\n\nTwo things to fix before a tenant signs — they are "
                        "listed below the summary.") if len(notes) > 1 else (
                       "\n\nOne thing to fix before a tenant signs — it is "
                       "listed below the summary.")

            return {
                "reply": (
                    f"Listed — '{prop.title}' is on the rental list at "
                    f"{_rupees(draft.get('monthly_rent_inr'))} a month.\n\n"
                    "It shows as ownership-unconfirmed until someone checks the "
                    "khata or tax receipt against your name. Tenants are told "
                    "that, because a deposit paid to someone who does not own "
                    "the place is the single most common rental fraud here."
                    + tail),
                "stage": "DONE",
                "draft": draft,
                "created": created,
                "summary": _summary(draft),
                "compliance": notes,
            }

        if _NO.search(text or ""):
            return {
                "reply": "Tell me what to change and I'll update it.",
                "stage": "REVISING", "draft": draft, "created": None,
                "summary": _summary(draft), "compliance": compliance(draft),
            }

    # ---- collect --------------------------------------------------------
    next_stage, question = _next_question(draft)
    notes = compliance(draft)

    if next_stage is None:
        return {
            "reply": "Here's the letting. Shall I put it on the rental list?",
            "stage": "CONFIRM",
            "draft": draft,
            "created": None,
            "summary": _summary(draft),
            "compliance": notes,
        }

    return {
        "reply": question,
        "stage": next_stage,
        "draft": draft,
        "created": None,
        "summary": _summary(draft),
        "compliance": notes,
    }


def _title_for(d: Dict[str, Any]) -> str:
    config = d.get("config")
    kind = (d.get("property_type") or "Property")
    head = f"{config} {kind.lower()}" if config else kind
    head = head[:1].upper() + head[1:]
    where = d.get("locality")
    return f"{head} to let, {where}" if where else f"{head} to let"
