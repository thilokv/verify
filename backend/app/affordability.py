"""What this property actually costs, and whether a bank will fund it.

Two things collapse Indian property deals late, and neither is on the listing:

  The price is not the price   Stamp duty, registration, cess and GST add
                               roughly 6.5–12% on top. On a ₹1.24 Cr flat that
                               is about ₹8 lakh the buyer has not budgeted, and
                               it becomes payable at the sub-registrar's office
                               — the one moment they cannot postpone.

  The bank may say no          Around seven in ten purchases are financed. A
                               B-Khata property is not merely worth less; most
                               banks will not lend against it at all. A buyer
                               who has arranged 80% financing discovers this
                               after paying a token.

So this module answers three questions the sticker price hides: what will I
actually pay, how much will a bank give me, and will they lend on *this*.

Every rate here is a configured constant with a date, not a hardcoded truth.
Stamp duty moves in state budgets and GST moves in Council meetings; a stale
rate that looks authoritative is worse than no number at all, so the response
carries `rates_as_of` and says plainly that it must be re-checked.
"""

import datetime as dt
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .models import Property, RentalListing

RATES_AS_OF = "2026-04-01"

# ---- Karnataka acquisition charges ---------------------------------------
# Stamp duty is banded on consideration value. Cess and surcharge are charged
# ON the stamp duty, not on the property value — a detail that is routinely
# got wrong and understates the bill.
STAMP_DUTY_BANDS = (
    (2_100_000, 0.02),      # up to ₹21 lakh
    (4_500_000, 0.03),      # ₹21–45 lakh
    (float("inf"), 0.05),   # above ₹45 lakh
)
REGISTRATION_RATE = 0.01
CESS_ON_STAMP_DUTY = 0.10       # 10% of the stamp duty
SURCHARGE_ON_STAMP_DUTY_URBAN = 0.02
SURCHARGE_ON_STAMP_DUTY_RURAL = 0.03

# GST applies only while a property is under construction. A completed unit
# with an occupancy certificate, and any resale, carries none.
GST_UNDER_CONSTRUCTION = 0.05
GST_AFFORDABLE = 0.01
AFFORDABLE_CEILING_INR = 4_500_000   # metro definition

ADVOCATE_FEE_RANGE = (15_000, 30_000)

# ---- lending --------------------------------------------------------------
# RBI-linked loan-to-value ceilings, banded on property value.
LTV_BANDS = (
    (3_000_000, 0.90),
    (7_500_000, 0.80),
    (float("inf"), 0.75),
)
# Banks size an EMI against net monthly income. The ratio widens with income
# because a higher earner has more absolute room after essentials.
FOIR_BANDS = (
    (50_000, 0.40),
    (100_000, 0.50),
    (float("inf"), 0.55),
)
DEFAULT_RATE_ANNUAL = 0.086
DEFAULT_TENURE_YEARS = 20

# Khata classes most lenders will not touch.
UNFUNDABLE_KHATA = ("B",)


def _rupees(v: Optional[float]) -> str:
    if v is None:
        return "—"
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh / 100:.2f} Cr".replace(".00 Cr", " Cr")
    if lakh >= 1:
        return f"₹{lakh:.2f} L".replace(".00 L", " L")
    return "₹" + f"{round(v):,}"


def _banded(value: float, bands) -> float:
    for ceiling, rate in bands:
        if value <= ceiling:
            return rate
    return bands[-1][1]


def stamp_duty_rate(price_inr: float) -> float:
    return _banded(price_inr, STAMP_DUTY_BANDS)


def acquisition_cost(price_inr: float, possession: Optional[str] = None,
                     urban: bool = True) -> Dict[str, Any]:
    """Everything payable on top of the agreed price."""
    if price_inr is None or price_inr <= 0:
        raise ValueError("price_inr must be a positive amount")

    duty_rate = stamp_duty_rate(price_inr)
    stamp_duty = price_inr * duty_rate
    cess = stamp_duty * CESS_ON_STAMP_DUTY
    surcharge = stamp_duty * (SURCHARGE_ON_STAMP_DUTY_URBAN if urban
                              else SURCHARGE_ON_STAMP_DUTY_RURAL)
    registration = price_inr * REGISTRATION_RATE

    under_construction = (possession or "").strip().lower().startswith("under")
    if under_construction:
        gst_rate = (GST_AFFORDABLE if price_inr <= AFFORDABLE_CEILING_INR
                    else GST_UNDER_CONSTRUCTION)
        gst = price_inr * gst_rate
    else:
        gst_rate, gst = 0.0, 0.0

    advocate = sum(ADVOCATE_FEE_RANGE) / 2
    extras = stamp_duty + cess + surcharge + registration + gst + advocate
    total = price_inr + extras

    lines: List[Dict[str, Any]] = [
        {"item": "Agreed price", "amount_inr": round(price_inr),
         "display": _rupees(price_inr), "note": "What the listing says."},
        {"item": f"Stamp duty ({duty_rate * 100:.0f}%)",
         "amount_inr": round(stamp_duty), "display": _rupees(stamp_duty),
         "note": "Payable to the state at registration. Not negotiable."},
        {"item": "Cess (10% of stamp duty)", "amount_inr": round(cess),
         "display": _rupees(cess),
         "note": "Charged on the duty, not on the property value."},
        {"item": f"Surcharge ({'urban' if urban else 'rural'})",
         "amount_inr": round(surcharge), "display": _rupees(surcharge),
         "note": "Also charged on the duty."},
        {"item": "Registration (1%)", "amount_inr": round(registration),
         "display": _rupees(registration),
         "note": "Sub-registrar's fee to record the transfer in your name."},
    ]
    if gst:
        lines.append({
            "item": f"GST ({gst_rate * 100:.0f}%)", "amount_inr": round(gst),
            "display": _rupees(gst),
            "note": ("Under-construction only. A completed unit with an "
                     "occupancy certificate, and any resale, carries no GST."),
        })
    lines.append({
        "item": "Advocate / title opinion", "amount_inr": round(advocate),
        "display": _rupees(advocate),
        "note": (f"Typical range {_rupees(ADVOCATE_FEE_RANGE[0])}–"
                 f"{_rupees(ADVOCATE_FEE_RANGE[1])}. The cheapest insurance "
                 f"in the whole transaction."),
    })

    return {
        "price_inr": round(price_inr),
        "extras_inr": round(extras),
        "extras_display": _rupees(extras),
        "extras_percent": round(extras / price_inr * 100, 1),
        "total_inr": round(total),
        "total_display": _rupees(total),
        "under_construction": under_construction,
        "lines": lines,
        "headline": (
            f"{_rupees(price_inr)} listed, {_rupees(total)} to actually own it "
            f"— {_rupees(extras)} more than the price you were quoted."),
        "rates_as_of": RATES_AS_OF,
        "disclaimer": (
            "Karnataka rates as configured on " + RATES_AS_OF + ". Stamp duty "
            "changes in state budgets and GST in Council meetings — confirm "
            "current rates with your advocate before you budget on them."),
    }


def emi(principal: float, annual_rate: float, years: int) -> float:
    """Equated monthly instalment."""
    if principal <= 0:
        return 0.0
    months = years * 12
    r = annual_rate / 12
    if r == 0:
        return principal / months
    factor = (1 + r) ** months
    return principal * r * factor / (factor - 1)


def loan_eligibility(price_inr: float, monthly_income_inr: float,
                     existing_emi_inr: float = 0.0,
                     annual_rate: float = DEFAULT_RATE_ANNUAL,
                     years: int = DEFAULT_TENURE_YEARS,
                     khata: Optional[str] = None,
                     possession: Optional[str] = None) -> Dict[str, Any]:
    """How much a bank will lend, and whether it will lend on this at all."""
    if monthly_income_inr is None or monthly_income_inr <= 0:
        raise ValueError("monthly_income_inr must be a positive amount")
    if years <= 0:
        raise ValueError("years must be positive")

    blockers: List[Dict[str, str]] = []

    khata_class = (khata or "").strip().upper()[:1]
    fundable = khata_class not in UNFUNDABLE_KHATA
    if not fundable:
        blockers.append({
            "code": "B_KHATA_NOT_FUNDABLE",
            "severity": "high",
            "title": "Most banks will not lend against B-Khata",
            "detail": ("This is not a worse rate — it is usually a refusal. A "
                       "B-Khata property has to be bought substantially in "
                       "cash. Confirm with your lender in writing BEFORE you "
                       "pay a token, not after."),
        })

    ltv = _banded(price_inr, LTV_BANDS)
    foir = _banded(monthly_income_inr, FOIR_BANDS)

    # Two independent ceilings; the bank applies whichever binds first.
    ceiling_by_property = price_inr * ltv
    affordable_emi = max(0.0, monthly_income_inr * foir - existing_emi_inr)

    months = years * 12
    r = annual_rate / 12
    factor = (1 + r) ** months
    ceiling_by_income = (affordable_emi * (factor - 1) / (r * factor)) if r else affordable_emi * months

    max_loan = min(ceiling_by_property, ceiling_by_income)
    binding = ("your income" if ceiling_by_income < ceiling_by_property
               else "the property value")

    if not fundable:
        max_loan = 0.0

    down_payment = max(0.0, price_inr - max_loan)
    monthly = emi(max_loan, annual_rate, years)

    if (possession or "").strip().lower().startswith("under"):
        blockers.append({
            "code": "PRE_EMI_WHILE_BUILDING",
            "severity": "medium",
            "title": "You pay pre-EMI before you can move in",
            "detail": ("On an under-construction unit the bank releases money "
                       "in tranches and you pay interest on what has been "
                       "released — while still paying rent. Budget for both at "
                       "once, for as long as the build runs over."),
        })

    return {
        "price_inr": round(price_inr),
        "monthly_income_inr": round(monthly_income_inr),
        "fundable": fundable,
        "max_loan_inr": round(max_loan),
        "max_loan_display": _rupees(max_loan),
        "down_payment_inr": round(down_payment),
        "down_payment_display": _rupees(down_payment),
        "down_payment_percent": (round(down_payment / price_inr * 100, 1)
                                 if price_inr else None),
        "emi_inr": round(monthly),
        "emi_display": _rupees(monthly),
        "ltv_applied": round(ltv * 100),
        "foir_applied": round(foir * 100),
        "binding_constraint": binding,
        "annual_rate_percent": round(annual_rate * 100, 2),
        "tenure_years": years,
        "total_interest_inr": round(monthly * years * 12 - max_loan) if max_loan else 0,
        "total_interest_display": _rupees(monthly * years * 12 - max_loan) if max_loan else "—",
        "blockers": blockers,
        "headline": (
            "No mainstream lender is likely to fund this property."
            if not fundable else
            f"A bank should lend about {_rupees(max_loan)} — you need "
            f"{_rupees(down_payment)} of your own, and the EMI is about "
            f"{_rupees(monthly)} a month."),
        "rates_as_of": RATES_AS_OF,
        "disclaimer": (
            "An indication, not a sanction. Every lender underwrites its own "
            "way and will look at your credit history, employment and the "
            "builder's approvals. Get a written sanction letter before you "
            "commit money."),
    }


def affordability(session: Session, property_id: int,
                  monthly_income_inr: Optional[float] = None,
                  existing_emi_inr: float = 0.0,
                  annual_rate: float = DEFAULT_RATE_ANNUAL,
                  years: int = DEFAULT_TENURE_YEARS) -> Dict[str, Any]:
    """The full picture for one property: true cost, and whether it is fundable."""
    prop = session.get(Property, property_id)
    if prop is None:
        raise ValueError(f"No property {property_id}")
    if not prop.price_inr:
        raise ValueError(
            "This listing has no asking price, so there is nothing to cost. "
            "A letting is priced monthly — see the rental view instead.")

    cost = acquisition_cost(prop.price_inr, prop.possession)
    out: Dict[str, Any] = {
        "property_id": prop.id,
        "title": prop.title,
        "khata": prop.khata,
        "acquisition": cost,
        "loan": None,
        "rates_as_of": RATES_AS_OF,
    }

    if monthly_income_inr:
        out["loan"] = loan_eligibility(
            prop.price_inr, monthly_income_inr, existing_emi_inr,
            annual_rate, years, prop.khata, prop.possession)
        # The gap people miss: the loan covers the price, never the extras.
        # Stamp duty and registration always come out of your own pocket.
        cash_needed = out["loan"]["down_payment_inr"] + cost["extras_inr"]
        out["cash_needed_inr"] = round(cash_needed)
        out["cash_needed_display"] = _rupees(cash_needed)
        out["cash_note"] = (
            f"You need {_rupees(cash_needed)} in hand: {_rupees(out['loan']['down_payment_inr'])} "
            f"down payment plus {_rupees(cost['extras_inr'])} of duties and fees. "
            f"No bank lends against stamp duty or registration.")

    return out


def _estimated_rent(session: Session, prop: Property):
    """Median rent per square foot from comparable lettings, applied to this.

    Returns (monthly_rent, description) or (None, reason). Prefers lettings in
    the same locality; widens to the city rather than inventing a number.
    """
    rows = (session.query(RentalListing, Property)
            .join(Property, Property.id == RentalListing.property_id)
            .filter(RentalListing.monthly_rent_inr.isnot(None),
                    Property.area_sqft.isnot(None),
                    Property.area_sqft > 0)
            .all())
    if not rows or not prop.area_sqft:
        return None, "no comparable lettings"

    local = [(r, p) for r, p in rows if p.location_name == prop.location_name]
    pool, scope = (local, f"lettings in {prop.location_name}") if local else (
        rows, "lettings across the city")

    rates = sorted(float(r.monthly_rent_inr) / float(p.area_sqft) for r, p in pool)
    mid = len(rates) // 2
    median = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2

    n = len(pool)
    scope = scope if n != 1 else scope.rstrip("s").replace("letting", "letting")
    noun = "letting" if n == 1 else "lettings"
    where = (f"in {prop.location_name}" if local else "across the city")
    return median * float(prop.area_sqft), (
        f"{n} comparable {noun} {where} at about ₹{median:.0f}/sq ft a month")


def rental_yield(session: Session, property_id: int) -> Dict[str, Any]:
    """What this returns as an investment, gross and after the real costs."""
    prop = session.get(Property, property_id)
    if prop is None:
        raise ValueError(f"No property {property_id}")

    price = prop.price_inr
    if not price:
        return {
            "property_id": property_id,
            "title": prop.title,
            "gross_yield_percent": None,
            "net_yield_percent": None,
            "basis": ("This is a letting, not a sale listing — it has no "
                      "purchase price, so there is no yield to compute. Yield "
                      "answers 'if I bought this, what would it return'."),
        }

    letting = (session.query(RentalListing)
               .filter_by(property_id=property_id).first())

    if letting is not None and letting.monthly_rent_inr:
        monthly_rent = float(letting.monthly_rent_inr)
        rent_source = "the letting registered against this property"
        rent_is_estimate = False
    else:
        # The common case, and the one that makes this useful: an investor is
        # looking at a SALE listing. It has no letting of its own — Ultron
        # records lettings as separate RENT listings — so the rent is estimated
        # from comparable lettings nearby, per square foot.
        monthly_rent, rent_source = _estimated_rent(session, prop)
        rent_is_estimate = True
        if monthly_rent is None:
            return {
                "property_id": property_id,
                "title": prop.title,
                "gross_yield_percent": None,
                "net_yield_percent": None,
                "basis": ("No letting on this property and no comparable "
                          "lettings nearby to estimate a rent from. Add a few "
                          "lettings under Rentals and this becomes computable."),
            }

    annual_rent = monthly_rent * 12
    cost = acquisition_cost(price, prop.possession)
    all_in = cost["total_inr"]

    # Costs a gross-yield headline quietly ignores.
    vacancy = annual_rent * 0.08          # about a month between tenants
    maintenance = annual_rent * 0.05
    property_tax = price * 0.002
    net_annual = annual_rent - vacancy - maintenance - property_tax

    gross = annual_rent / price * 100
    net_on_all_in = net_annual / all_in * 100

    return {
        "property_id": property_id,
        "title": prop.title,
        "monthly_rent_inr": round(monthly_rent),
        "rent_is_estimate": rent_is_estimate,
        "rent_source": rent_source,
        "annual_rent_inr": round(annual_rent),
        "price_inr": round(price),
        "all_in_cost_inr": all_in,
        "gross_yield_percent": round(gross, 2),
        "net_yield_percent": round(net_on_all_in, 2),
        "deductions": [
            {"item": "Vacancy (8%)", "amount_inr": round(vacancy),
             "note": "About a month between tenants, every year."},
            {"item": "Maintenance (5%)", "amount_inr": round(maintenance),
             "note": "Repairs, painting between tenants, society dues you carry."},
            {"item": "Property tax", "amount_inr": round(property_tax),
             "note": "Annual BBMP tax, indicative."},
        ],
        "verdict": (
            "Below a fixed deposit. This is a bet on capital appreciation, not income."
            if net_on_all_in < 6 else
            "Comparable to a fixed deposit, with property risk attached."
            if net_on_all_in < 8 else
            "A strong income yield for Indian residential property."),
        "basis": (
            f"Net yield is computed on the all-in cost of {_rupees(all_in)}, not "
            f"the sticker price — the duties are real money you will never get "
            f"back. Gross yield on the sticker price alone would read "
            f"{round(gross, 2)}%. Rent taken from {rent_source}."),
        "rates_as_of": RATES_AS_OF,
    }
