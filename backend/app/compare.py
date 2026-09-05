"""Side by side — because the cheapest listing is rarely the cheapest purchase.

Every engine in this platform answers for one property. A buyer deciding
between three flats has to hold six numbers per flat in their head, and the
numbers that actually separate them are the ones no listing shows:

  Two flats at the same asking price differ by lakhs once stamp duty and GST
  land, because one is under construction. One of them may be unfundable
  outright. The cheaper one may face west, which in Bengaluru is a real cost
  in both comfort and resale.

So this does not rank. Ranking implies one winner and hides the trade, and the
trade is the decision. It lines the properties up on the dimensions that move
money, then says plainly where they genuinely diverge and where the difference
is noise.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import affordability as afford
from . import valuation as val
from .models import Property

MAX_COMPARE = 4


def _rupees(v: Optional[float]) -> str:
    if v is None:
        return "—"
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh / 100:.2f} Cr".replace(".00 Cr", " Cr")
    if lakh >= 1:
        return f"₹{lakh:.2f} L".replace(".00 L", " L")
    return "₹" + f"{round(v):,}"


def compare(session: Session, property_ids: List[int],
            monthly_income_inr: Optional[float] = None) -> Dict[str, Any]:
    """Line properties up on what actually costs money."""
    ids = list(dict.fromkeys(property_ids))          # de-dupe, keep order
    if len(ids) < 2:
        raise ValueError("Choose at least two properties to compare.")
    if len(ids) > MAX_COMPARE:
        raise ValueError(
            f"Compare up to {MAX_COMPARE} at once — beyond that the table stops "
            f"being readable and starts being a spreadsheet.")

    columns: List[Dict[str, Any]] = []
    for pid in ids:
        prop = session.get(Property, pid)
        if prop is None:
            raise ValueError(f"No property {pid}")
        if not prop.price_inr:
            raise ValueError(
                f"“{prop.title}” is a letting, not a sale listing — it has no "
                f"purchase price, so it cannot be compared against one.")

        cost = afford.acquisition_cost(prop.price_inr, prop.possession)
        loan = afford.loan_eligibility(
            prop.price_inr, monthly_income_inr or 200_000, 0.0,
            afford.DEFAULT_RATE_ANNUAL, afford.DEFAULT_TENURE_YEARS,
            prop.khata, prop.possession)
        try:
            v = val.value(session, pid)
        except ValueError:
            v = {}
        try:
            y = afford.rental_yield(session, pid)
        except ValueError:
            y = {}

        sun = v.get("sunlight") or val.sunlight_profile(prop.facing)

        columns.append({
            "property_id": pid,
            "title": prop.title,
            "locality": prop.location_name,
            "config": prop.config,
            "area_sqft": prop.area_sqft,
            "khata": prop.khata,
            "possession": prop.possession,
            "is_verified": prop.is_verified,
            "listed_inr": prop.price_inr,
            "listed_display": _rupees(prop.price_inr),
            "all_in_inr": cost["total_inr"],
            "all_in_display": cost["total_display"],
            "extras_inr": cost["extras_inr"],
            "extras_display": cost["extras_display"],
            "extras_percent": cost["extras_percent"],
            "fundable": loan["fundable"],
            "max_loan_display": loan["max_loan_display"],
            "emi_inr": loan["emi_inr"],
            "emi_display": loan["emi_display"],
            "cash_needed_inr": loan["down_payment_inr"] + cost["extras_inr"],
            "cash_needed_display": _rupees(loan["down_payment_inr"] + cost["extras_inr"]),
            "estimate_display": v.get("estimate_display"),
            "gap_percent": v.get("gap_percent"),
            "confidence": v.get("confidence"),
            "facing": sun["facing"],
            "sun_hours": sun["annual_direct_sun_hours"],
            "sun_note": sun["peak_exposure"],
            "yield_percent": y.get("net_yield_percent"),
            "rate_per_sqft": (round(prop.price_inr / prop.area_sqft)
                              if prop.area_sqft else None),
        })

    return {
        "count": len(columns),
        "columns": columns,
        "findings": _findings(columns),
        "assumed_income_inr": monthly_income_inr or 200_000,
        "disclaimer": (
            "Every figure is an estimate computed from the listed price and the "
            "configured Karnataka rates. Confirm duties with your advocate and "
            "get a written sanction letter before committing money."),
    }


def _findings(cols: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Say where these genuinely diverge — and where they do not."""
    out: List[Dict[str, str]] = []

    cheapest_listed = min(cols, key=lambda c: c["listed_inr"])
    cheapest_allin = min(cols, key=lambda c: c["all_in_inr"])
    if cheapest_listed["property_id"] != cheapest_allin["property_id"]:
        gap = cheapest_listed["all_in_inr"] - cheapest_allin["all_in_inr"]
        out.append({
            "severity": "high",
            "title": "The cheapest listing is not the cheapest purchase",
            "detail": (
                f"“{cheapest_listed['title']}” is listed lowest, but once duties "
                f"land it costs {_rupees(gap)} MORE than "
                f"“{cheapest_allin['title']}”. "
                + ("The difference is GST — one of these is under construction "
                   "and the other is not."
                   if cheapest_listed["extras_percent"] > cheapest_allin["extras_percent"] + 2
                   else "The difference is the duty band.")),
        })

    unfundable = [c for c in cols if not c["fundable"]]
    if unfundable and len(unfundable) < len(cols):
        names = ", ".join(f"“{c['title']}”" for c in unfundable)
        out.append({
            "severity": "high",
            "title": f"{len(unfundable)} of these cannot be financed",
            "detail": (
                f"{names} — most banks will not lend against B-Khata, so it has "
                f"to be bought substantially in cash. That is not a small price "
                f"difference; for most buyers it removes the option entirely."),
        })

    over = [c for c in cols if (c.get("gap_percent") or 0) > 7]
    if over:
        c = max(over, key=lambda x: x["gap_percent"])
        out.append({
            "severity": "medium",
            "title": f"“{c['title']}” is asking above our estimate",
            "detail": (f"Listed {c['gap_percent']}% above what comparable verified "
                       f"stock supports. That is room to negotiate, or a reason to "
                       f"ask what the seller knows that the comparables do not."),
        })

    unverified = [c for c in cols if not c["is_verified"]]
    if unverified:
        out.append({
            "severity": "medium",
            "title": f"{len(unverified)} still has no advocate opinion on file",
            "detail": ", ".join(f"“{c['title']}”" for c in unverified)
                      + " — the price is provisional until the title is examined. "
                        "Do not pay a token against an unexamined title.",
        })

    west = [c for c in cols if (c["facing"] or "").lower().startswith("west")]
    if west and len(west) < len(cols):
        out.append({
            "severity": "low",
            "title": "One of these faces west",
            "detail": (f"“{west[0]['title']}” takes the afternoon sun on its facade. "
                       f"In Bengaluru that means higher cooling costs and a softer "
                       f"resale market than an east-facing equivalent."),
        })

    rates = [c["rate_per_sqft"] for c in cols if c["rate_per_sqft"]]
    if len(rates) >= 2 and max(rates) - min(rates) < min(rates) * 0.06:
        out.append({
            "severity": "info",
            "title": "On rate per square foot these are the same property",
            "detail": (f"All within 6% of ₹{min(rates):,}/sq ft. The decision is not "
                       f"price — it is title, funding and orientation."),
        })

    if not out:
        out.append({
            "severity": "info",
            "title": "Nothing material separates these",
            "detail": "Same duty band, all fundable, all examined. Choose on the "
                      "things a spreadsheet cannot hold: the commute and the light.",
        })
    return out
