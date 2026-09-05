"""Seller verification — the anti-fraud checklist.

WHAT THIS IS
    A structured intake of the documents and disclosures that Karnataka
    conveyancing normally requires, plus the questions that catch the fraud
    patterns which actually recur in Indian property transactions.

WHAT THIS IS NOT
    Legal advice, a title opinion, or a government verification service. This
    platform has no connection to any land-records system. Nothing here proves
    a document is genuine — it records what the seller CLAIMS to hold, and
    flags the combinations that warrant an advocate's attention first.
    Requirements vary by state, by municipality, and by how the property was
    acquired. An advocate signs off. This only decides who they look at first.

DELIBERATE DESIGN CHOICE — NO IDENTITY NUMBERS
    We ask *whether* the seller holds a document, never for the number on it.
    Aadhaar in particular is restricted under the Aadhaar Act 2016 and must
    not be collected or stored by a platform that has no authorised purpose
    for it. Storing PAN, Aadhaar or account numbers in a listing database
    creates breach liability with no matching benefit — the advocate verifies
    identity in person, against originals.
"""

from typing import Any, Dict, List, Optional

# --------------------------------------------------------------- documents

# code, label, why it matters
CORE = [
    ("SALE_DEED", "Sale deed in the seller's name",
     "The instrument that conveys title. Without it the seller cannot sell."),
    ("MOTHER_DEED", "Mother deed / parent documents",
     "Traces the chain of ownership backwards. Gaps in the chain are where "
     "disputed title hides."),
    ("EC", "Encumbrance Certificate (30 years)",
     "Shows mortgages, liens and registered claims over the period examined."),
    ("KHATA", "Khata certificate and extract",
     "Municipal record of who pays tax on the property. A-Khata is regular; "
     "B-Khata signals an irregular property with restricted lending."),
    ("TAX_RECEIPT", "Latest property tax paid receipt",
     "Unpaid tax attaches to the property, not the previous owner."),
]

BY_TYPE: Dict[str, List] = {
    "Flat": CORE + [
        ("OC", "Occupancy Certificate",
         "BBMP's certificate that the building is legally fit to occupy. "
         "Buildings occupied without an OC are common and hard to finance."),
        ("SANCTIONED_PLAN", "Approved building plan",
         "Deviations from the sanctioned plan can force demolition."),
        ("SOCIETY_NOC", "Society / association NOC and share certificate",
         "Confirms no outstanding dues and that the association recognises "
         "the seller."),
        ("RERA", "RERA registration (projects after 2017)",
         "Mandatory for most projects; the registration number is publicly "
         "checkable on the state RERA portal."),
    ],
    "Villa": CORE + [
        ("OC", "Occupancy Certificate", "Legal fitness to occupy."),
        ("CC", "Completion Certificate", "Construction completed as sanctioned."),
        ("SANCTIONED_PLAN", "Sanctioned building plan and licence",
         "Deviations can force demolition."),
        ("RERA", "RERA registration (if part of a registered project)",
         "Checkable on the state RERA portal."),
    ],
    "Plot": CORE + [
        ("CONVERSION", "DC conversion order",
         "Agricultural land must be converted for non-agricultural use. "
         "Selling an unconverted plot for housing is a common fraud."),
        ("LAYOUT_APPROVAL", "Layout approval (BDA / BMRDA / BBMP / DTCP)",
         "Unapproved layouts cannot be regularised easily and banks will not "
         "lend against them."),
        ("SURVEY_SKETCH", "Survey sketch, Tippani and Akarband",
         "Fixes the boundaries. Boundary overlap is the most common land "
         "dispute in Karnataka."),
    ],
    "Agricultural": [
        ("RTC", "RTC / Pahani (record of rights)",
         "The primary revenue record of cultivation and possession."),
        ("MUTATION", "Mutation register extract (MR)",
         "Records the transfer of revenue entries to the current holder."),
        ("SURVEY_SKETCH", "Survey sketch, Tippani, Akarband, Podi",
         "Fixes boundaries and the extent."),
        ("EC", "Encumbrance Certificate", "Registered claims over the land."),
        ("MOTHER_DEED", "Parent documents", "Chain of ownership."),
        ("LAND_REFORM", "Land-reform / ceiling compliance position",
         "Karnataka amended its land-reform restrictions in 2020. The current "
         "position must be confirmed by an advocate for this specific parcel."),
        ("CONVERSION", "Conversion order (only if selling for non-agricultural use)",
         "Required before the land may be used for housing."),
    ],
    "Commercial": CORE + [
        ("OC", "Occupancy Certificate", "Legal fitness to occupy."),
        ("SANCTIONED_PLAN", "Sanctioned plan", "Deviations carry demolition risk."),
        ("FIRE_NOC", "Fire safety NOC", "Required for most commercial occupancy."),
        ("TRADE_LICENCE", "Trade licence / commercial conversion",
         "Confirms the premises may lawfully be used commercially."),
    ],
}


def required_documents(property_type: Optional[str]) -> List[Dict[str, str]]:
    docs = BY_TYPE.get(property_type or "", CORE)
    return [{"code": c, "label": l, "why": w} for c, l, w in docs]


# --------------------------------------------------------------- questions

# id, question, safe_answer, risk if unsafe, explanation
QUESTIONS = [
    ("IS_OWNER",
     "Are you the owner named on the sale deed?",
     True, "high",
     "If the seller is not the registered owner, everything else is moot until "
     "the actual owner's authority is established."),

    ("GPA_SALE",
     "Are you selling under a General Power of Attorney rather than as the owner?",
     False, "high",
     "The Supreme Court held in Suraj Lamp & Industries v. State of Haryana "
     "(2011) that GPA/agreement-to-sell/will transfers do NOT convey title. "
     "GPA sales are one of the most common vehicles for property fraud in "
     "India. A GPA sale needs an advocate before anything else proceeds."),

    ("CO_OWNERS",
     "Are there co-owners, joint holders or family members with a share?",
     False, "high",
     "Every co-owner must consent and sign. A sale without a co-owner's "
     "consent is voidable, and this is a frequent source of post-sale "
     "litigation."),

    ("INHERITED",
     "Was the property inherited?",
     False, "medium",
     "Inherited property needs a legal heir certificate or succession "
     "certificate, and consent from all heirs. Undisclosed heirs surface "
     "after the sale."),

    ("MORTGAGED",
     "Is there an active loan or mortgage on the property?",
     False, "high",
     "The original title documents will be with the lender. A no-objection "
     "certificate and loan closure are required before registration."),

    ("LITIGATION",
     "Is any court case, stay order or dispute pending on this property?",
     False, "high",
     "Property under litigation cannot be safely transacted. Lis pendens "
     "binds the buyer to the outcome of the case."),

    ("ACQUISITION",
     "Has any authority issued an acquisition or notification affecting it?",
     False, "high",
     "Land notified for acquisition cannot be sold as clear title, and "
     "compensation goes to the recorded owner."),

    ("OTHER_AGREEMENT",
     "Have you signed an agreement to sell with anyone else?",
     False, "high",
     "Double-selling. An existing agreement gives that buyer a claim, and "
     "this is a deliberate fraud pattern, not just an oversight."),

    ("TENANT",
     "Is anyone else in possession — a tenant or occupant?",
     False, "medium",
     "Possession must be handed over clear. Sitting tenants can be extremely "
     "difficult to remove after purchase."),

    ("BOUNDARY_DISPUTE",
     "Is there any boundary disagreement with a neighbour?",
     False, "medium",
     "Boundary disputes are the most common land litigation in Karnataka and "
     "must be settled before, not after."),
]


def questionnaire() -> List[Dict[str, Any]]:
    return [
        {"id": qid, "question": q, "safe_answer": safe,
         "risk_if_unsafe": risk, "why": why}
        for qid, q, safe, risk, why in QUESTIONS
    ]


_Q_INDEX = {qid: (safe, risk, why, q) for qid, q, safe, risk, why in QUESTIONS}


# ------------------------------------------------------------- assessment

def assess(property_type: Optional[str],
           answers: Dict[str, Any],
           documents_held: List[str]) -> Dict[str, Any]:
    """Score a seller's disclosures. Returns findings, never a clearance."""
    findings: List[Dict[str, str]] = []
    held = {str(d).upper() for d in (documents_held or [])}

    # ---- disclosures ------------------------------------------------
    unanswered = []
    for qid, (safe, risk, why, question) in _Q_INDEX.items():
        if qid not in answers or answers[qid] is None:
            unanswered.append(question)
            continue
        given = bool(answers[qid])
        if given != safe:
            findings.append({
                "severity": risk,
                "code": qid,
                "detail": why,
                "question": question,
            })

    # ---- documents ---------------------------------------------------
    required = required_documents(property_type)
    missing = [d for d in required if d["code"] not in held]
    for d in missing:
        core_codes = {c for c, _, _ in CORE}
        findings.append({
            "severity": "high" if d["code"] in core_codes else "medium",
            "code": "MISSING_" + d["code"],
            "detail": f"{d['label']} not provided. {d['why']}",
            "question": "",
        })

    highs = sum(1 for f in findings if f["severity"] == "high")
    mediums = sum(1 for f in findings if f["severity"] == "medium")

    if unanswered:
        status = "INCOMPLETE"
        headline = f"{len(unanswered)} disclosure question(s) still unanswered."
    elif highs:
        status = "HIGH_RISK"
        headline = (f"{highs} serious issue(s) found. Do not accept a token "
                    f"payment until an advocate has reviewed this.")
    elif mediums:
        status = "NEEDS_REVIEW"
        headline = f"{mediums} item(s) an advocate should examine before listing."
    else:
        status = "READY_FOR_ADVOCATE"
        headline = ("Nothing detected in this screen. The advocate's title "
                    "opinion is the next and decisive step.")

    return {
        "status": status,
        "headline": headline,
        "high_count": highs,
        "medium_count": mediums,
        "unanswered": unanswered,
        "documents_required": required,
        "documents_held": sorted(held),
        "documents_missing": [d["code"] for d in missing],
        "findings": sorted(findings,
                           key=lambda f: 0 if f["severity"] == "high" else 1),
        "disclaimer": (
            "This is a self-declaration checklist, not a verification. Nothing "
            "here has been checked against any government record, and no "
            "document has been authenticated. It does not constitute legal "
            "advice. A licensed advocate must examine the originals and issue "
            "a title opinion before any money changes hands. Requirements "
            "differ by state and by how the property was acquired."
        ),
    }
