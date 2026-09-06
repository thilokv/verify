"""From handshake to keys — the sequence nobody explains.

An Indian buyer is told the price and the EMI. They are almost never told the
ORDER: which document comes before which payment, and which step protects them
if the one after it goes wrong. So money moves early, at the token stage, before
the checks that would have justified it.

Two things this insists on, because both are routinely got wrong:

  Money follows the check, never the other way round. The advocate's opinion
  and the encumbrance certificate come BEFORE the token, not after. Once a
  token has moved, the buyer has no leverage and a document to argue with
  rather than a decision to make.

  Registration is not the end. The sale deed is registered and the buyer
  believes they are done — but the khata is still in the seller's name.
  Khata transfer is a separate application to the BBMP, and skipping it is the
  single most common Bengaluru mistake. It surfaces years later, when they try
  to sell or borrow, and the seller is long gone.

Stages are generic to a Karnataka apartment purchase. Amounts scale with the
price passed in; percentages are the configured rates, dated like every other
rate in this codebase.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import affordability as afford
from . import cities
from .models import Property


def _rupees(v: Optional[float]) -> str:
    if v is None:
        return "—"
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh / 100:.2f} Cr".replace(".00 Cr", " Cr")
    if lakh >= 1:
        return f"₹{lakh:.2f} L".replace(".00 L", " L")
    return "₹" + f"{round(v):,}"


# (key, stage, when, what happens, documents, what goes wrong)
STAGES = (
    ("shortlist", "Shortlist and negotiate", "Week 0",
     "You agree a price verbally. Nothing is binding and nothing should have "
     "moved yet.",
     ["Listing particulars", "Photo ID of whoever claims to own it"],
     "Paying a 'holding amount' here. It is not a legal step, and it is the "
     "money most often lost — there is no document to enforce."),

    ("diligence", "Title examination", "Week 1–2",
     "An advocate reads the chain of title and you pull a 30-year encumbrance "
     "certificate. This is the step that decides whether to proceed at all.",
     ["Encumbrance certificate (30 years)", "Mother deed and chain of title",
      "Khata certificate and extract", "Latest tax paid receipt",
      "Approved plan and occupancy certificate", "RERA registration (if applicable)"],
     "Doing this AFTER paying a token. By then the money is committed and the "
     "advocate is writing an excuse rather than a decision."),

    ("agreement", "Agreement to sell", "Week 2–3",
     "The first binding document. Token typically 10–20% of the price, paid "
     "only once the title has been examined.",
     ["Agreement to sell, stamped", "Payment receipt naming the mode of payment"],
     "An unstamped or unregistered agreement. In Karnataka an agreement to "
     "sell attracts 0.1% stamp duty — unstamped, it is far weaker evidence if "
     "the seller walks."),

    ("loan", "Loan sanction", "Week 3–5",
     "The bank runs its own legal and technical valuation. Its lawyers look at "
     "the same title independently — a second opinion you are paying for anyway.",
     ["Sanction letter", "Bank's legal and technical report",
      "Income proof, and the agreement to sell"],
     "Assuming sanction is a formality. A bank refusing the property — not you "
     "— is how B-Khata purchases collapse after the token has moved."),

    ("deed", "Sale deed drafting", "Week 5–6",
     "The operative document. Every name, survey number, boundary and area is "
     "checked against the mother deed before anyone signs.",
     ["Draft sale deed", "PAN of both parties", "Aadhaar for e-signature"],
     "A typo in the survey number or the schedule of property. It is a "
     "correction deed and a fresh trip to the sub-registrar to fix."),

    ("registration", "Stamp duty and registration", "Week 6",
     "You pay the state and the transfer is recorded. Both parties attend the "
     "sub-registrar's office with two witnesses.",
     ["Stamp duty challan", "Registered sale deed", "Two witnesses with ID"],
     "Under-declaring the consideration to save duty. It is an offence, and it "
     "caps the cost base you can prove when you sell — the tax comes back "
     "larger at the far end."),

    ("khata", "Khata transfer (BBMP)", "Week 7–10",
     "A SEPARATE application to move the khata into your name. Registration "
     "does not do this for you.",
     ["Registered sale deed", "Latest tax paid receipt",
      "Khata transfer application with fee"],
     "Skipping it. The most common Bengaluru mistake by a wide margin: the "
     "buyer believes registration finished the job, the khata stays in the "
     "seller's name, and it surfaces years later when they try to sell or "
     "borrow — with the seller long gone."),

    ("possession", "Possession and transfers", "Week 8–12",
     "Keys, and the utilities and society records moved into your name.",
     ["Possession letter", "BESCOM and BWSSB name transfer",
      "Society share certificate and NOC"],
     "Taking keys before the electricity and water accounts are transferred. "
     "Unpaid arrears follow the meter, not the person who ran them up."),
)


def timeline(session: Session, property_id: Optional[int] = None,
             price_inr: Optional[float] = None) -> Dict[str, Any]:
    """The stage-by-stage sequence, with the money attached to each."""
    prop = None
    if property_id is not None:
        prop = session.get(Property, property_id)
        if prop is None:
            raise ValueError(f"No property {property_id}")
        if not prop.price_inr:
            raise ValueError(
                "This is a letting, not a sale — it has no purchase sequence. "
                "A tenancy is an agreement and a deposit, not a conveyance.")
        price_inr = prop.price_inr

    if not price_inr or price_inr <= 0:
        raise ValueError("Give a property, or a price to work the sequence against.")

    city_name = prop.city if prop else None
    c = cities.get(city_name)
    cost = afford.acquisition_cost(price_inr, prop.possession if prop else None,
                                   city=city_name)
    duty = next(l["amount_inr"] for l in cost["lines"]
                if l["item"].startswith("Stamp duty"))
    cess = next(l["amount_inr"] for l in cost["lines"] if l["item"].startswith("Cess"))
    surcharge = next(l["amount_inr"] for l in cost["lines"]
                     if l["item"].startswith("Surcharge"))
    registration = next(l["amount_inr"] for l in cost["lines"]
                        if l["item"].startswith("Registration"))
    token = price_inr * 0.15

    money = {
        "diligence": (25_000, "Advocate's title opinion and the EC. The cheapest "
                              "money in the whole transaction."),
        "agreement": (token + price_inr * 0.001,
                      f"Token of about {_rupees(token)} at 15%, plus 0.1% stamp "
                      f"duty on the agreement itself."),
        "registration": (duty + cess + surcharge + registration,
                         "Stamp duty, cess, surcharge and registration — all of it "
                         "from your own pocket. No bank lends against this."),
        "khata": (5_000, "Application fee and the betterment charge, indicative."),
    }

    stages = []
    for i, (key, name, when, what, docs, wrong) in enumerate(STAGES, 1):
        if key == "khata":
            # Every state has this step; only Karnataka calls it a khata.
            name = f"Record transfer ({c['title_authority']})"
            what = c["transfer_step"]
            docs = [d.replace("Khata transfer application with fee",
                              f"{c['title_authority']} transfer application with fee")
                    for d in docs]
        if key == "diligence":
            docs = [c["title_document"] if "Khata certificate" in d else d for d in docs]
        amount, why = money.get(key, (None, None))
        stages.append({
            "n": i,
            "key": key,
            "stage": name,
            "when": when,
            "what": what,
            "documents": list(docs),
            "goes_wrong": wrong,
            "money_inr": round(amount) if amount else None,
            "money_display": _rupees(amount) if amount else None,
            "money_why": why,
            # The platform's own checks, placed where they belong in the order.
            "platform": {
                "diligence": "Title check and the evidence engine run here — "
                             "before any money moves.",
                "loan": "The affordability engine already told you whether a bank "
                        "will lend on this khata class.",
                "khata": "Ask the agent to watch this listing's title; a change "
                         "after you have paid is exactly what it is for.",
            }.get(key),
        })

    return {
        "property_id": property_id,
        "title": prop.title if prop else None,
        "price_inr": round(price_inr),
        "price_display": _rupees(price_inr),
        "total_outlay_display": cost["total_display"],
        "stages": stages,
        "city": city_name or cities.DEFAULT_CITY,
        "state": c["state"],
        "title_document": c["title_document"],
        "title_authority": c["title_authority"],
        "title_note": c["title_note"],
        "rule": ("Money follows the check, never the other way round. The "
                 "advocate's opinion and the encumbrance certificate come before "
                 "the token — once a token has moved you have a document to argue "
                 "with instead of a decision to make."),
        "most_missed": ("Registration is not the end. " + c["transfer_step"]),
        "rates_as_of": afford.RATES_AS_OF,
    }
