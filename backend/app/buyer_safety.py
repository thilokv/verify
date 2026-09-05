"""Buyer-side protection.

The seller side asks "is this property real?". This side asks a different
question: "is this buyer about to lose their money?"

Most Indian property fraud takes the buyer's cash before any title problem
surfaces — a token paid to someone with no right to sell, a booking amount
wired to a personal account, an advance to an "agent" who then vanishes. By
the time the encumbrance certificate is read, the money is gone.

So the rules below are deliberately blunt and appear at the moment a buyer is
about to act, not buried in terms. The single most valuable sentence this
platform can show a buyer is: do not pay anything before the title opinion.
"""

from typing import Any, Dict, List, Optional

# Rules a buyer should never break. Shown verbatim before contact or payment.
GOLDEN_RULES = [
    ("NEVER_PAY_BEFORE_TITLE",
     "Do not pay a rupee — token, booking or advance — until an advocate has "
     "read the title and said in writing that it is clean.",
     "Token money is almost never recovered. Once it is paid, the buyer's "
     "leverage is gone and any defect becomes their problem."),

    ("NEVER_PERSONAL_ACCOUNT",
     "Never transfer to a personal account, UPI handle or cash. Pay only to "
     "the registered owner or a builder's escrow account named in the "
     "agreement.",
     "Payment redirection is a standard fraud: the account details change at "
     "the last moment, often by a spoofed message."),

    ("VERIFY_SELLER_IS_OWNER",
     "Check that the person you are dealing with is the owner named on the "
     "sale deed. Ask for the original, not a photocopy or a phone photo.",
     "Impersonation and General Power of Attorney sales are the two most "
     "common ways a property is 'sold' by someone with no right to sell it."),

    ("NO_GPA_PURCHASE",
     "Do not buy on a General Power of Attorney, agreement-to-sell or will.",
     "The Supreme Court held in Suraj Lamp & Industries v. State of Haryana "
     "(2011) that these do not transfer ownership. You would pay full price "
     "and not become the owner."),

    ("SEE_IT_YOURSELF",
     "Visit the property and confirm the survey number on the ground matches "
     "the documents before any payment.",
     "Buyers are shown one plot and sold another. Boundary substitution is "
     "common in plotted layouts."),

    ("REGISTER_IMMEDIATELY",
     "Register the sale deed at the sub-registrar's office. An unregistered "
     "agreement does not make you the owner.",
     "Unregistered transfers leave the seller as legal owner, free to sell "
     "again."),
]

VISIT_CHECKLIST = [
    "Tell someone where you are going and when you expect to be back.",
    "Meet at the property or the sales office, not at a private residence.",
    "Ask to see an original photo ID and the original title document.",
    "Photograph the survey stone or plot number and match it to the papers.",
    "Do not carry cash. There is no reason to pay anything at a first visit.",
    "If you are pressed to decide today or pay to 'block' the unit, leave.",
]

# Pressure lines that reliably precede advance-fee fraud.
RED_FLAG_PHRASES = [
    ("pay token today", "Urgency about a token payment"),
    ("block the unit", "Pressure to pay to 'block' a property"),
    ("only today", "Manufactured deadline"),
    ("cash only", "Cash-only demand"),
    ("gpa", "General Power of Attorney sale"),
    ("power of attorney", "General Power of Attorney sale"),
    ("no need for advocate", "Discouraging legal review"),
    ("registration later", "Deferring registration"),
    ("personal account", "Payment to a personal account"),
    ("google pay", "Payment by personal UPI"),
    ("phonepe", "Payment by personal UPI"),
    ("advance to hold", "Advance demanded to hold a property"),
]


def scan_message(text: str) -> List[Dict[str, str]]:
    """Flag pressure tactics in what a seller or agent has told the buyer."""
    low = (text or "").lower()
    hits, seen = [], set()
    for phrase, label in RED_FLAG_PHRASES:
        if phrase in low and label not in seen:
            seen.add(label)
            hits.append({"phrase": phrase, "concern": label})
    return hits


def brief(prop: Dict[str, Any],
          verification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What this specific buyer should know before contacting this seller."""
    warnings: List[Dict[str, str]] = []
    v = verification or {}
    level = v.get("level") or prop.get("risk_status") or "NOT_SUBMITTED"
    verified = bool(prop.get("is_verified"))

    if not verified:
        warnings.append({
            "severity": "high",
            "title": "No advocate has checked this title",
            "detail": (
                "This listing has not been verified. Treat everything the "
                "seller says about ownership as unconfirmed, and pay nothing."),
        })

    if level == "FLAGGED":
        warnings.append({
            "severity": "high",
            "title": "Automated checks flagged a problem",
            "detail": ("Our document checks found something inconsistent on "
                       "this listing. Do not proceed without an advocate."),
        })
    elif level in ("NOT_SUBMITTED", "PARTIAL"):
        warnings.append({
            "severity": "medium",
            "title": "Documents not fully submitted",
            "detail": ("The seller has not provided the full document set, so "
                       "there is nothing yet for an advocate to examine."),
        })

    khata = (prop.get("khata") or "").upper()
    if khata.startswith("B"):
        warnings.append({
            "severity": "medium",
            "title": "B-Khata property",
            "detail": ("Most banks will not lend against B-Khata. Resale is "
                       "harder and regularisation is not guaranteed."),
        })

    if (prop.get("property_type") or "") == "Plot" and not prop.get("rera_id"):
        warnings.append({
            "severity": "medium",
            "title": "No RERA number on this plot",
            "detail": ("Confirm the layout is approved and, where the project "
                       "requires it, RERA-registered before paying."),
        })

    if (prop.get("possession") or "").lower().startswith("under"):
        warnings.append({
            "severity": "medium",
            "title": "Under construction",
            "detail": ("Payments are milestone-linked and delivery risk is "
                       "real. Check the RERA registration and the promised "
                       "completion date on the state RERA portal yourself."),
        })

    if verified and not warnings:
        stance = "PROCEED_WITH_NORMAL_CARE"
        headline = ("An advocate's title opinion is on file. Follow the payment "
                    "rules below anyway.")
    elif any(w["severity"] == "high" for w in warnings):
        stance = "DO_NOT_PAY"
        headline = "Do not pay anything for this property yet."
    else:
        stance = "PROCEED_WITH_CAUTION"
        headline = "Some things need checking before you commit money."

    return {
        "stance": stance,
        "headline": headline,
        "verified": verified,
        "level": level,
        "warnings": warnings,
        "golden_rules": [
            {"code": c, "rule": r, "why": w} for c, r, w in GOLDEN_RULES],
        "visit_checklist": VISIT_CHECKLIST,
        "disclaimer": (
            "This platform never asks a buyer to pay it, and never holds a "
            "buyer's money. Anyone claiming to collect a fee on our behalf is "
            "committing fraud — report it."),
    }
