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

from . import cities
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
# What the market pays for a shared facility, as a fraction of the base. These
# are modest on purpose: a clubhouse is priced into a project, not a windfall,
# and stacking six amenities at 3% each would invent a quarter of the value.
AMENITY_UPLIFT = {
    "swimming pool": 0.020, "clubhouse": 0.018, "gym": 0.012,
    "power backup": 0.012, "covered parking": 0.015, "24x7 security": 0.010,
    "kids play area": 0.008, "lift": 0.008, "park": 0.006,
}
AMENITY_CAP = 0.06        # the whole bundle, never more than this


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


def _solar_position(latitude: float, day: int, hour: float):
    """Altitude and azimuth of the sun, in degrees. Azimuth is from true north."""
    lat = math.radians(latitude)
    dec = math.radians(_declination(day))
    H = math.radians(15.0 * (hour - 12.0))          # hour angle

    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(H)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)
    if alt <= 0:
        return -1.0, 0.0                            # below the horizon

    cos_az = ((math.sin(dec) - math.sin(alt) * math.sin(lat))
              / (math.cos(alt) * math.cos(lat) or 1e-9))
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.degrees(math.acos(cos_az))            # 0 = north
    if H > 0:                                       # afternoon: sun in the west
        az = 360.0 - az
    return math.degrees(alt), az


# Facade normals, degrees from true north.
_FACING_NORMAL = {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}

_FACING_NOTE = {
    "east": ("Morning sun, cool by afternoon. The most sought-after orientation "
             "in this market and it carries a resale premium."),
    "north": ("Least direct sun and the coolest interior. Good for heat, weaker "
              "for drying and for natural light in winter."),
    "west": ("Harsh afternoon sun on the facade. Rooms hold heat into the "
             "evening and cooling costs run higher."),
    "south": ("The most total sun across the year. Bright, and warm through the "
              "middle of the day."),
}


def sunlight_profile(facing: Optional[str],
                     latitude: float = CITY_LATITUDE) -> Dict[str, Any]:
    """Annual hours of direct sun ON THIS FACADE, integrated over the year.

    Real astronomy rather than a lookup, and latitude genuinely matters here:
    at 13°N the sun crosses overhead twice a year and spends part of it to the
    NORTH, so a north facade in Chennai catches sun a north facade in Delhi
    never does. An earlier version multiplied mean day length by a fixed
    per-facing fraction — but mean day length over a full year is ~12 hours at
    every latitude, so that cancelled latitude out and reported the same number
    for Delhi and Bengaluru.

    What it is NOT is a shading study: it cannot see the tower next door. It
    answers how much sun a facade at this orientation receives at this latitude.
    """
    face = (facing or "").strip().lower()
    normal = _FACING_NORMAL.get(face)

    step = 1.0 / 6.0                                # ten-minute steps
    daylight_by_day, facade_hours = [], 0.0

    for day in range(1, 366):
        lit = 0.0
        hour = 0.0
        while hour < 24.0:
            alt, az = _solar_position(latitude, day, hour)
            if alt > 0:
                lit += step
                if normal is None:
                    facade_hours += step * 0.38     # orientation not stated
                else:
                    diff = abs((az - normal + 180.0) % 360.0 - 180.0)
                    if diff < 90.0:
                        facade_hours += step
            hour += step
        daylight_by_day.append(lit)

    mean_daylight = sum(daylight_by_day) / len(daylight_by_day)

    return {
        "facing": facing or "Not stated",
        "latitude": latitude,
        "annual_direct_sun_hours": round(facade_hours),
        "mean_daylight_hours": round(mean_daylight, 2),
        "peak_exposure": {"east": "morning", "west": "afternoon",
                          "south": "midday", "north": "indirect"}.get(face, "mixed"),
        "summer_daylight_hours": round(max(daylight_by_day), 2),
        "winter_daylight_hours": round(min(daylight_by_day), 2),
        "note": _FACING_NOTE.get(
            face, "Orientation not stated, so this is the average across facings."),
        "method": (f"Solar altitude and azimuth computed at ten-minute steps "
                   f"across 365 days at {latitude}°N, counting only the hours "
                   f"the sun is actually in front of this facade. Orientation "
                   f"only — it does not model shading from neighbouring "
                   f"buildings."),
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
            "sunlight": sunlight_profile(prop.facing, cities.get(prop.city)["latitude"]),
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

    # ---- amenities ------------------------------------------------------
    # Stored on every listing and, until now, never priced. Capped as a bundle
    # so a long amenity list cannot manufacture value.
    listed = [str(a).strip().lower() for a in (prop.amenities or [])]
    matched = [(a, AMENITY_UPLIFT[a]) for a in AMENITY_UPLIFT if a in listed]
    if matched:
        raw = sum(u for _, u in matched)
        uplift = min(raw, AMENITY_CAP)
        delta = running * uplift
        running += delta
        adjustments.append({
            "factor": f"Amenities ({len(matched)})",
            "percent": round(uplift * 100, 1),
            "amount_inr": round(delta),
            "why": ("" .join([", ".join(a for a, _ in matched).capitalize(), ". "])
                    + ("Capped as a bundle — a long amenity list does not "
                       "compound into real value."
                       if raw > AMENITY_CAP else
                       "Priced modestly: these are shared facilities, not extra "
                       "square feet.")),
        })

    # ---- infrastructure -------------------------------------------------
    infra = cities.get(prop.city)["infrastructure"].get(prop.location_name or "", [])
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
