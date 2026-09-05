"""Tenant-side protection.

The buyer side asks "is this title clean?". A tenant is exposed differently and
the difference matters:

  A buyer's money buys an asset. A tenant's money buys nothing — the deposit is
  handed over and simply held, often for years, by a private individual with no
  escrow, no interest, and no supervision. In Bengaluru ten months' rent was
  the norm, so a tenant routinely parts with four to eight lakh for a flat they
  will never own.

That is the whole risk in one sentence: the deposit is the largest unsecured
loan most tenants will ever make, and they make it to a stranger.

Two consequences shape this module. First, the rules below are about the
deposit and about who is really collecting it, not about title. Second, unlike
a buyer, a tenant has statutory rights — the Model Tenancy Act 2021 gives them
a cap, a deadline for return, and a right to notice. Most tenants do not know
this, and a landlord breaching it is relying on that. So the rights are stated
here as plainly as the warnings.
"""

from typing import Any, Dict, List, Optional

# What a tenant should never do, shown before they hand over money.
MONEY_RULES = [
    ("NEVER_PAY_BEFORE_AGREEMENT",
     "Do not pay a deposit, token or advance until you have a written "
     "agreement in your hand, signed, naming the amount and the date it comes "
     "back to you.",
     "A deposit paid on a spoken promise is almost never recovered. Once the "
     "money has moved, the tenant has no leverage and no document to point at."),

    ("CONFIRM_THEY_OWN_IT",
     "Ask for the khata, a tax receipt or an electricity bill in the "
     "landlord's name, and match it to their photo ID before paying anything.",
     "Collecting a deposit on a flat you do not own is the most common rental "
     "fraud in Indian cities. An empty flat and a set of keys prove nothing — "
     "both are easy to obtain for an afternoon."),

    ("DEPOSIT_IS_CAPPED",
     "A residential deposit is capped at two months' rent, and commercial at "
     "six, under the Model Tenancy Act 2021. You can refuse more.",
     "Ten months was the Bengaluru norm and is precisely what the Act was "
     "written to stop. Landlords still quote it because tenants do not know "
     "it is now contestable."),

    ("PAY_TRACEABLY",
     "Pay by bank transfer to an account in the landlord's own name. Never "
     "cash, never to a broker's personal account or UPI handle.",
     "Cash leaves you with nothing to prove you paid. A payment routed through "
     "a broker's personal account can vanish without ever reaching the owner."),

    ("PHOTOGRAPH_THE_CONDITION",
     "On the day you move in, photograph every room, every existing mark, and "
     "the meter readings. Send the set to the landlord the same day.",
     "Deductions at exit for damage that was already there is the standard way "
     "a deposit shrinks. Dated photographs shared at handover end that argument "
     "before it starts."),

    ("GET_IT_REGISTERED",
     "If the lease runs twelve months or longer, insist it is registered at "
     "the sub-registrar's office.",
     "An unregistered long lease is not admissible as evidence. If the letting "
     "is ever disputed, the tenant is the one who cannot prove their terms."),
]

VIEWING_CHECKLIST = [
    "Tell someone where you are going and when you expect to be back.",
    "See the property yourself. Never pay for one you have only seen in photos.",
    "Ask who owns it, and ask to see a document with that name on it.",
    "Check water supply, the borewell or tanker arrangement, and power backup.",
    "Ask what the maintenance charge is and who pays it — it is often extra.",
    "Photograph the meter readings and any existing damage before you agree.",
    "Do not carry cash. Nothing needs to be paid at a first viewing.",
    "If you are pressed to pay today to 'hold' it, leave.",
]

# What the Model Tenancy Act 2021 gives a tenant. Stated plainly because a
# landlord breaching these is usually relying on the tenant not knowing.
TENANT_RIGHTS = [
    ("Deposit is capped",
     "Two months' rent for a home, six for commercial premises. Anything above "
     "that can be contested."),
    ("Deposit comes back within a month",
     "The landlord must return it within one month of you handing back the "
     "property, less only what is genuinely owed."),
    ("Deductions must be itemised",
     "'Painting charges' or 'cleaning charges' applied as a flat percentage, "
     "with no bill and no proof of damage, are not lawful deductions."),
    ("You get notice before anyone enters",
     "The landlord must give at least 24 hours' written notice before entering, "
     "and may only come at a reasonable hour."),
    ("Essential services cannot be cut",
     "Water and electricity may not be withheld to force you out — not during "
     "a dispute, not over unpaid rent."),
    ("Rent cannot rise mid-term",
     "An increase needs three months' written notice and cannot take effect "
     "before the agreed term ends."),
    ("Eviction goes through the Rent Authority",
     "You cannot be removed by a locked gate, removed belongings, or a phone "
     "call. It requires due process."),
]

# Lines that reliably precede a rental deposit disappearing.
RED_FLAG_PHRASES = [
    ("pay to block", "Pressure to pay to 'block' the flat"),
    ("token to hold", "Advance demanded to hold a property"),
    ("advance to hold", "Advance demanded to hold a property"),
    ("someone else is coming", "Manufactured competition"),
    ("only today", "Manufactured deadline"),
    ("cash only", "Cash-only demand — leaves you no proof of payment"),
    ("no agreement", "Letting without a written agreement"),
    ("agreement later", "Deferring the written agreement"),
    ("no rental agreement", "Letting without a written agreement"),
    ("ten months deposit", "Deposit far above the statutory cap"),
    ("10 months deposit", "Deposit far above the statutory cap"),
    ("personal account", "Payment to a personal or broker's account"),
    ("google pay", "Payment by personal UPI"),
    ("phonepe", "Payment by personal UPI"),
    ("gpay", "Payment by personal UPI"),
    ("owner is abroad", "Absent-owner story — a standard fake-landlord setup"),
    ("owner is out of station", "Absent-owner story — a standard fake-landlord setup"),
    ("send the deposit", "Asked to transfer a deposit before viewing"),
    ("without seeing", "Asked to commit before viewing"),
    ("no police verification", "Skipping tenant police verification"),
]


def scan_message(text: str) -> List[Dict[str, str]]:
    """Flag pressure tactics in what a landlord or broker has told the tenant."""
    low = (text or "").lower()
    hits, seen = [], set()
    for phrase, label in RED_FLAG_PHRASES:
        if phrase in low and label not in seen:
            seen.add(label)
            hits.append({"phrase": phrase, "concern": label})
    return hits


def deposit_note(monthly_rent: Optional[float], deposit: Optional[float],
                 commercial: bool = False) -> Optional[Dict[str, str]]:
    """State the deposit against the statutory cap, in rupees the tenant can check."""
    if not monthly_rent or not deposit:
        return None
    cap_months = 6 if commercial else 2
    quoted = deposit / monthly_rent
    if quoted <= cap_months + 0.01:
        return None
    lawful = cap_months * monthly_rent
    return {
        "severity": "high",
        "title": f"Deposit is {quoted:.0f} months' rent",
        "detail": (
            f"₹{int(deposit):,} is being asked where the Model Tenancy Act "
            f"caps it at {cap_months} months — ₹{int(lawful):,}. The excess of "
            f"₹{int(deposit - lawful):,} is contestable. Ask for it in writing "
            f"before you agree to anything."),
    }


def brief(rental: Dict[str, Any], prop: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What this specific tenant should know before paying this landlord."""
    warnings: List[Dict[str, str]] = []
    p = prop or {}
    verified = bool(p.get("is_verified"))
    rent = rental.get("monthly_rent_inr")
    deposit = rental.get("deposit_inr")
    commercial = (p.get("property_type") or "") == "Commercial"

    if not verified:
        warnings.append({
            "severity": "high",
            "title": "Nobody has confirmed this landlord owns the property",
            "detail": (
                "We have not checked the khata or a tax receipt against the "
                "name of the person letting this. Ask to see one, and match it "
                "to their photo ID, before any money moves."),
        })

    note = deposit_note(rent, deposit, commercial)
    if note:
        warnings.append(note)

    terms = (rental.get("lease_terms") or "").lower()
    months = None
    for token in terms.split():
        if token.isdigit():
            months = int(token)
            break
    if months and months >= 12:
        warnings.append({
            "severity": "medium",
            "title": f"A {months}-month lease has to be registered",
            "detail": (
                "A lease of twelve months or more must be registered at the "
                "sub-registrar's office. Unregistered, it is not admissible as "
                "evidence — and you are the one who would need to prove your "
                "terms."),
        })

    if (rental.get("furnishing") or "") == "Furnished":
        warnings.append({
            "severity": "low",
            "title": "Furnished — get the inventory in writing",
            "detail": (
                "List every item and its condition as an annexure to the "
                "agreement, with photographs. Missing or damaged furniture is a "
                "common reason a deposit comes back short."),
        })

    if any(w["severity"] == "high" for w in warnings):
        stance = "DO_NOT_PAY"
        headline = "Do not pay a deposit on this yet."
    elif warnings:
        stance = "PROCEED_WITH_CAUTION"
        headline = "Some terms need fixing before you commit money."
    else:
        stance = "PROCEED_WITH_NORMAL_CARE"
        headline = ("Nothing is flagged on the terms. Follow the deposit rules "
                    "below anyway.")

    return {
        "stance": stance,
        "headline": headline,
        "verified": verified,
        "monthly_rent_inr": rent,
        "deposit_inr": deposit,
        "warnings": warnings,
        "money_rules": [
            {"code": c, "rule": r, "why": w} for c, r, w in MONEY_RULES],
        "viewing_checklist": VIEWING_CHECKLIST,
        "your_rights": [{"right": r, "detail": d} for r, d in TENANT_RIGHTS],
        "disclaimer": (
            "This platform never collects a tenant's deposit and never holds "
            "it. Anyone asking you to pay a deposit to us, or to a broker on "
            "our behalf, is committing fraud — report it."),
    }
