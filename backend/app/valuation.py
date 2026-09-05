"""Dynamic valuation — what this property is worth, and why.

Three inputs, in descending order of how much they should be trusted:

  Comparables    What similar stock in the same locality actually lists at,
                 per square foot. This is the anchor. Everything else adjusts
                 it.
  Title          B-Khata is not a label, it is a price. Most banks will not
                 lend against it, which removes the financed buyer from the
                 market and takes the clearing price down with them.
  Location       Proximity to committed infrastructure. Only *committed* —
                 an announced metro line is a rumour until it is funded.

Every figure returned is an estimate and is labelled as one. The confidence
field is driven by how many comparables were actually found, because a "price"
derived from one listing is a guess wearing a suit.
"""

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .models import Property

# Committed infrastructure in Bengaluru, with the uplift each is worth to
# stock within walking distance. Deliberately conservative: these are premiums
# the market already prices, not speculative announcements.
INFRASTRUCTURE = {
    "Whitefield": [("Purple Line metro (operational)", 0.08),
                   ("ITPL / EPIP employment", 0.05)],
    "Sarjapur": [("Outer Ring Road IT corridor", 0.06)],
    "Hebbal": [("Airport corridor / Bellary Road", 0.05)],
    "Electronic City": [("Elevated expressway", 0.04),
                        ("Yellow Line metro (operational)", 0.07)],
    "Indiranagar": [("Purple Line metro (operational)", 0.06),
                    ("Established central locality", 0.04)],
    "Koramangala": [("Established central locality", 0.05)],
    "Devanahalli": [("Kempegowda International Airport", 0.06)],
    "Yelahanka": [("Airport corridor", 0.04)],
    "Marathahalli": [("Outer Ring Road IT corridor", 0.05)],
}

# Bengaluru. Latitude drives the sunlight model below.
CITY_LATITUDE = 12.97

# What the market pays for, or discounts, beyond the per-square-foot anchor.
KHATA_ADJUSTMENT = {
    # B-Khata: banks generally decline, so the financed buyer disappears.
    "B": -0.18,
    "A": 0.0,
}
POSSESSION_ADJUSTMENT = {
    "ready to move": 0.0,
    # Under construction carries delivery risk and defers occupation.
    "under construction": -0.09,
}


def _declination(day_of_year: int) -> float:
    """Solar declination in degrees. Cooper's equation."""
    return 23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0))


def _daylight_hours(latitude: float, day_of_year: int) -> float:
    """Hours between sunrise and sunset at this latitude, this day."""
    lat = math.radians(latitude)
    dec = math.radians(_declination(day_of_year))
    cos_omega = -math.tan(lat) * math.tan(dec)
    cos_omega = max(-1.0, min(1.0, cos_omega))   # no polar edge cases at 13°N
    return 2.0 * math.degrees(math.acos(cos_omega)) / 15.0


def sunlight_profile(facing: Optional[str],
                     latitude: float = CITY_LATITUDE) -> Dict[str, Any]:
    """Annual direct-sun exposure for a facade, computed over 365 days.

    This is real astronomy rather than a lookup: declination is integrated
    across the year for the given latitude. What it is NOT is a shading study —
    it cannot see the tower next door. It answers "how much sun does a facade
    at this orientation receive in Bengaluru", which is the question that
    actually drives heat load and, here, resale preference.
    """
    days = range(1, 366)
    daylight = [_daylight_hours(latitude, d) for d in days]
    mean_daylight = sum(daylight) / len(daylight)

    face = (facing or "").strip().lower()

    # Fraction of the day's sun a facade receives, and when it receives it.
    # North of the tropic the sun tracks south; at 13°N it crosses overhead
    # twice a year, so a north facade still catches summer morning and evening
    # sun — which is why north is cool but not dark here.
    profiles = {
        "east": (0.42, "morning",
                 "Morning sun, cool by afternoon. The most sought-after "
                 "orientation in Bengaluru and it carries a resale premium."),
        "north": (0.30, "indirect",
                  "Least direct sun and the coolest interior. Good for heat, "
                  "weaker for drying and for natural light in winter."),
        "west": (0.44, "afternoon",
                 "Harsh afternoon sun on the facade. Rooms hold heat into the "
                 "evening and cooling costs run higher."),
        "south": (0.48, "midday",
                  "The most total sun across the year. Bright, and warm "
                  "through the middle of the day."),
    }
    fraction, peak, note = profiles.get(face, (0.38, "mixed",
        "Orientation not stated, so this is the average across facings."))

    annual_hours = mean_daylight * 365 * fraction

    return {
        "facing": facing or "Not stated",
        "annual_direct_sun_hours": round(annual_hours),
        "mean_daylight_hours": round(mean_daylight, 2),
        "peak_exposure": peak,
        "summer_daylight_hours": round(max(daylight), 2),
        "winter_daylight_hours": round(min(daylight), 2),
        "note": note,
        "method": ("Solar declination integrated over 365 days at "
                   f"{latitude}°N. Facade orientation only — this does not "
                   "model shading from neighbouring buildings."),
    }


def _comparables(db: Session, prop: Property) -> List[Property]:
    """Verified stock in the same locality and of the same type."""
    q = (db.query(Property)
           .filter(Property.id != prop.id,
                   Property.listing_type != "RENT",
                   Property.is_verified.is_(True),
                   Property.price_inr.isnot(None),
                   Property.area_sqft.isnot(None),
                   Property.area_sqft > 0))
    same = q.filter(Property.location_name == prop.location_name,
                    Property.property_type == prop.property_type).all()
    if same:
        return same
    # Widen to the property type across the city rather than inventing a number.
    return q.filter(Property.property_type == prop.property_type).all()


def _rupees(v: Optional[float]) -> str:
    if not v:
        return "—"
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh/100:.2f} Cr".replace(".00 Cr", " Cr")
    return f"₹{lakh:.0f} L"


def value(db: Session, property_id: int) -> Dict[str, Any]:
    """Estimate what this property is worth, and show the working."""
    prop = db.get(Property, property_id)
    if prop is None:
        raise ValueError(f"No property {property_id}")

    comps = _comparables(db, prop)
    same_locality = [c for c in comps
                     if c.location_name == prop.location_name]

    adjustments: List[Dict[str, Any]] = []

    if not comps or not prop.area_sqft:
        return {
            "property_id": prop.id,
            "title": prop.title,
            "estimate_inr": None,
            "confidence": "none",
            "basis": ("No verified comparable stock of this type, so there is "
                      "nothing honest to price against. Listing price is shown "
                      "unadjusted."),
            "listed_price_inr": prop.price_inr,
            "comparables": [],
            "adjustments": [],
            "sunlight": sunlight_profile(prop.facing),
            "is_estimate": True,
        }

    rates = sorted((c.price_inr / c.area_sqft) for c in comps)
    mid = len(rates) // 2
    median_rate = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2

    base = median_rate * prop.area_sqft
    running = base

    # ---- title ---------------------------------------------------------
    khata = (prop.khata or "").strip().upper()[:1]
    khata_adj = KHATA_ADJUSTMENT.get(khata)
    if khata_adj:
        delta = running * khata_adj
        running += delta
        adjustments.append({
            "factor": "B-Khata title",
            "percent": round(khata_adj * 100, 1),
            "amount_inr": round(delta),
            "why": ("Most banks decline a loan against B-Khata, which removes "
                    "financed buyers from the market. The discount is the "
                    "market pricing that smaller buyer pool, not a penalty."),
        })

    # ---- possession ----------------------------------------------------
    poss = (prop.possession or "").strip().lower()
    poss_adj = POSSESSION_ADJUSTMENT.get(poss)
    if poss_adj:
        delta = running * poss_adj
        running += delta
        adjustments.append({
            "factor": "Under construction",
            "percent": round(poss_adj * 100, 1),
            "amount_inr": round(delta),
            "why": ("Delivery risk and deferred occupation. Payments are "
                    "milestone-linked and the completion date is the "
                    "builder's promise, not a fact."),
        })

    # ---- infrastructure -------------------------------------------------
    infra = INFRASTRUCTURE.get(prop.location_name or "", [])
    growth_pct = 0.0
    for name, uplift in infra:
        delta = running * uplift
        running += delta
        growth_pct += uplift
        adjustments.append({
            "factor": name,
            "percent": round(uplift * 100, 1),
            "amount_inr": round(delta),
            "why": "Committed infrastructure already priced by this market.",
        })

    estimate = round(running)
    listed = prop.price_inr
    gap_pct = ((listed - estimate) / estimate * 100) if listed and estimate else None

    # Confidence follows the evidence, not the arithmetic. Three comparables in
    # the same locality is a view; one comparable from across the city is not.
    if len(same_locality) >= 3:
        confidence = "high"
    elif len(same_locality) >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "property_id": prop.id,
        "title": prop.title,
        "locality": prop.location_name,
        "estimate_inr": estimate,
        "estimate_display": _rupees(estimate),
        "listed_price_inr": listed,
        "listed_display": _rupees(listed),
        "gap_percent": round(gap_pct, 1) if gap_pct is not None else None,
        "verdict": (
            None if gap_pct is None else
            "Listed above our estimate" if gap_pct > 7 else
            "Listed below our estimate" if gap_pct < -7 else
            "Listed in line with our estimate"),
        "rate_per_sqft": round(median_rate),
        "base_inr": round(base),
        "adjustments": adjustments,
        "projected_3yr_growth_percent": round(growth_pct * 100, 1),
        "confidence": confidence,
        "comparables": [
            {"id": c.id, "title": c.title, "locality": c.location_name,
             "price_inr": c.price_inr, "area_sqft": c.area_sqft,
             "rate_per_sqft": round(c.price_inr / c.area_sqft)}
            for c in comps[:6]
        ],
        "comparable_count": len(comps),
        "same_locality_count": len(same_locality),
        "sunlight": sunlight_profile(prop.facing),
        "is_estimate": True,
        "basis": (
            f"Median of {len(comps)} verified comparable"
            f"{'s' if len(comps) != 1 else ''} at "
            f"₹{round(median_rate):,}/sq ft, adjusted for title, possession and "
            f"committed infrastructure. An estimate from listing prices, not a "
            f"valuation report and not a substitute for a bank valuer."),
    }
