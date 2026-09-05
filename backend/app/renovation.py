"""Renovation staging and repair estimates.

Two jobs:

  Staging    Take a room photo and produce a restyled version. The image work
             itself is a generation call; without a provider configured this
             returns a plan and says plainly that no image was produced. It
             does not return the original photo pretending to be a result.

  Costing    Estimate what the work costs in Bengaluru, and what it returns.
             The rates below are per-square-foot finishing costs at 2026
             Bengaluru contractor prices. They are estimates for orienting a
             buyer, not quotations, and a real quote will differ.

The recovery figures are the honest part of this module. Most renovation
"ROI" content implies work pays for itself; almost none of it does. A kitchen
at ₹4 lakh does not add ₹4 lakh to the sale price. Where recovery is below
100% this module says so, because a buyer deciding whether to renovate before
selling is exactly who gets hurt by the optimistic version.
"""

import os
from typing import Any, Dict, List, Optional

# Bengaluru finishing rates, ₹ per sq ft of the treated area, 2026.
# low = builder-grade materials, high = premium.
SCOPES: Dict[str, Dict[str, Any]] = {
    "paint": {
        "label": "Repaint",
        "rate_low": 18, "rate_high": 35,
        "recovery": 1.07,
        "days": 4,
        "note": ("The only change that reliably returns more than it costs. "
                 "Neutral shades, not a feature wall."),
    },
    "flooring": {
        "label": "Flooring — vitrified tile",
        "rate_low": 85, "rate_high": 180,
        "recovery": 0.72,
        "days": 12,
        "note": "Replaces visibly worn floors. Rarely worth it if the existing tile is sound.",
    },
    "kitchen": {
        "label": "Modular kitchen",
        "rate_low": 1400, "rate_high": 3200,
        "recovery": 0.62,
        "days": 21,
        "note": ("Costed on kitchen area only. High spend, moderate return — "
                 "buyers notice a dated kitchen but rarely pay full value for a new one."),
    },
    "bathroom": {
        "label": "Bathroom refit",
        "rate_low": 1100, "rate_high": 2600,
        "recovery": 0.68,
        "days": 16,
        "note": "Costed per bathroom area. Waterproofing is the part that must not be cheap.",
    },
    "electrical": {
        "label": "Rewiring",
        "rate_low": 90, "rate_high": 160,
        "recovery": 0.55,
        "days": 10,
        "note": ("Invisible to a buyer, so it returns little — but an old "
                 "aluminium-wired flat is a genuine fire risk and a lender concern."),
    },
    "plumbing": {
        "label": "Plumbing replacement",
        "rate_low": 70, "rate_high": 140,
        "recovery": 0.5,
        "days": 9,
        "note": "Same trade-off as rewiring: necessary, not rewarded at resale.",
    },
    "false_ceiling": {
        "label": "False ceiling and lighting",
        "rate_low": 120, "rate_high": 260,
        "recovery": 0.45,
        "days": 8,
        "note": "Cosmetic. Loses money at resale; do it for yourself, not for the buyer.",
    },
    "deep_clean": {
        "label": "Deep clean and declutter",
        "rate_low": 8, "rate_high": 16,
        "recovery": 3.5,
        "days": 2,
        "note": ("The highest-return work available by a wide margin, and the "
                 "step most sellers skip."),
    },
}

STYLES = ("contemporary", "minimal", "traditional", "scandinavian", "industrial")

ROOMS = ("living", "bedroom", "kitchen", "bathroom", "balcony", "whole_home")


def _rupees(v: float) -> str:
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh/100:.2f} Cr".replace(".00 Cr", " Cr")
    if lakh >= 1:
        return f"₹{lakh:.2f} L".replace(".00 L", " L")
    return "₹" + f"{int(v):,}"


def estimate(scopes: List[str], area_sqft: float,
             grade: str = "standard") -> Dict[str, Any]:
    """Cost a renovation and say honestly what comes back at resale."""
    if not area_sqft or area_sqft <= 0:
        raise ValueError("area_sqft must be a positive number")

    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise ValueError(
            f"Unknown scope {unknown[0]!r}. Choose from: {', '.join(sorted(SCOPES))}")
    if not scopes:
        raise ValueError("Choose at least one scope of work.")

    grade = (grade or "standard").lower()
    if grade not in ("budget", "standard", "premium"):
        raise ValueError("grade must be budget, standard or premium")

    # Kitchens and bathrooms are costed on their own footprint, not the flat's.
    room_share = {"kitchen": 0.09, "bathroom": 0.06}

    lines: List[Dict[str, Any]] = []
    total_low = total_high = total_recovered = 0.0
    longest = 0

    for key in scopes:
        spec = SCOPES[key]
        treated = area_sqft * room_share.get(key, 1.0)
        low = spec["rate_low"] * treated
        high = spec["rate_high"] * treated

        if grade == "budget":
            point = low
        elif grade == "premium":
            point = high
        else:
            point = (low + high) / 2

        recovered = point * spec["recovery"]
        total_low += low
        total_high += high
        total_recovered += recovered
        longest = max(longest, spec["days"])

        lines.append({
            "scope": key,
            "label": spec["label"],
            "treated_sqft": round(treated),
            "cost_inr": round(point),
            "cost_display": _rupees(point),
            "range_inr": [round(low), round(high)],
            "recovery_percent": round(spec["recovery"] * 100),
            "value_added_inr": round(recovered),
            "net_inr": round(recovered - point),
            "working_days": spec["days"],
            "note": spec["note"],
        })

    if grade == "budget":
        total = total_low
    elif grade == "premium":
        total = total_high
    else:
        total = (total_low + total_high) / 2

    lines.sort(key=lambda l: l["net_inr"], reverse=True)
    worth_it = [l for l in lines if l["net_inr"] > 0]

    return {
        "area_sqft": area_sqft,
        "grade": grade,
        "lines": lines,
        "total_cost_inr": round(total),
        "total_cost_display": _rupees(total),
        "range_display": f"{_rupees(total_low)} – {_rupees(total_high)}",
        "estimated_value_added_inr": round(total_recovered),
        "estimated_value_added_display": _rupees(total_recovered),
        "net_inr": round(total_recovered - total),
        "net_display": _rupees(abs(total_recovered - total)),
        "pays_for_itself": total_recovered > total,
        # Trades overlap; the schedule is the longest trade plus a margin,
        # not the sum of every trade run end to end.
        "estimated_weeks": max(1, round((longest + 5) / 5)),
        "recommendation": (
            "Every item here returns more than it costs."
            if len(worth_it) == len(lines) else
            f"Only {', '.join(l['label'] for l in worth_it)} returns more than "
            f"it costs. The rest is worth doing to live with, not to sell."
            if worth_it else
            "None of this returns its cost at resale. Do it because you want "
            "to live with it, and price the sale on the work's condition rather "
            "than expecting to recover the spend."),
        "is_estimate": True,
        "disclaimer": (
            "Bengaluru contractor rates, 2026. An orienting estimate, not a "
            "quotation — get three quotes before committing. Recovery figures "
            "are typical resale outcomes and are not guaranteed."),
    }


def staging_available() -> bool:
    """Whether an image provider is actually configured."""
    return bool(os.getenv("REPLICATE_API_TOKEN") or os.getenv("OPENAI_API_KEY"))


def stage(room: str, style: str, image_bytes: Optional[bytes] = None,
          area_sqft: Optional[float] = None) -> Dict[str, Any]:
    """Plan a virtual staging pass, and run it if a provider is configured.

    With no provider this returns the plan and `image: None`, and says so. It
    never returns the input photo dressed up as a generated result — a seller
    looking at an unchanged room and being told it was staged is worse than
    being told nothing happened.
    """
    room = (room or "").strip().lower()
    style = (style or "").strip().lower()
    if room not in ROOMS:
        raise ValueError(f"room must be one of: {', '.join(ROOMS)}")
    if style not in STYLES:
        raise ValueError(f"style must be one of: {', '.join(STYLES)}")
    if image_bytes is not None and not image_bytes:
        raise ValueError("The uploaded image is empty.")

    # "living" alone reads as a person, not a room — spell the room out.
    room_label = {
        "living": "living room", "bedroom": "bedroom", "kitchen": "kitchen",
        "bathroom": "bathroom", "balcony": "balcony", "whole_home": "home",
    }[room]
    prompt = (
        f"Photorealistic interior of an Indian {room_label} in a "
        f"{style} style. Keep the existing walls, windows, doors and floor "
        f"plan exactly as they are — restyle furniture, soft furnishings, "
        f"lighting and decor only. Natural daylight, no people, no text.")

    suggested = {
        "living": ["deep_clean", "paint", "false_ceiling"],
        "bedroom": ["deep_clean", "paint"],
        "kitchen": ["deep_clean", "kitchen"],
        "bathroom": ["bathroom"],
        "balcony": ["deep_clean", "paint"],
        "whole_home": ["deep_clean", "paint", "flooring"],
    }[room]

    costing = None
    if area_sqft:
        costing = estimate(suggested, area_sqft)

    return {
        "room": room,
        "style": style,
        "prompt": prompt,
        "image": None,
        "rendered": False,
        "provider_configured": staging_available(),
        "status": (
            "Ready to render — a provider is configured."
            if staging_available() else
            "No image provider configured, so nothing was generated. Set "
            "REPLICATE_API_TOKEN or OPENAI_API_KEY to render. The plan and "
            "the costing below are real either way."),
        "suggested_scopes": suggested,
        "costing": costing,
        "honesty_note": (
            "Staged images show a possibility, not the property. Any listing "
            "using one must say it is virtually staged — showing a buyer "
            "furniture that is not there is a misrepresentation."),
    }
