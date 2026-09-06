"""What changes when you cross a state line.

This platform was built for Bengaluru and had Karnataka baked into it — one
latitude, one stamp-duty table, and "khata" used as though it were a national
concept. It is not. Cross into Maharashtra and the buyer is asking for a
property card, not a khata; the duty is 6% with a metro cess on top; and a
south-facing flat in Delhi at 28.6°N gets a materially different amount of sun
than the same flat in Chennai at 13°N.

So the market facts live here, per city, rather than as constants scattered
through the pricing code. Three things genuinely differ and all three cost
money:

  Duty        Karnataka 5%, Maharashtra 6% plus metro cess, Tamil Nadu 7% with
              a 4% registration fee on top — nearly double Karnataka's total.
  Title       The document a buyer must actually demand has a different name
              and a different issuing office in every state.
  Latitude    Sunlight is astronomy, and astronomy does not care about state
              boundaries, but it does care about how far north you are.

Rates are dated. Stamp duty moves in state budgets and a stale rate presented
as fact is worse than no rate, so every response carries RATES_AS_OF and says
to confirm with an advocate.
"""

from typing import Any, Dict, List, Optional

RATES_AS_OF = "2026-04-01"

# Bands are (ceiling, rate) on consideration value; the last is the top band.
# `registration_cap_inr` is Maharashtra's flat cap; None means uncapped.
# `women_concession` is a percentage-point reduction where the state offers it.
CITIES: Dict[str, Dict[str, Any]] = {
    "Bengaluru": {
        "state": "Karnataka",
        "latitude": 12.97,
        "stamp_duty_bands": ((2_100_000, 0.02), (4_500_000, 0.03), (float("inf"), 0.05)),
        "registration_rate": 0.01,
        "registration_cap_inr": None,
        "cess_on_duty": 0.10,
        "surcharge_on_duty": 0.02,
        "women_concession": 0.0,
        "title_document": "Khata (A-Khata or B-Khata)",
        "title_authority": "BBMP",
        "title_note": ("B-Khata is the one that matters: most banks will not "
                       "lend against it, so it is a refusal rather than a "
                       "discount."),
        "transfer_step": "Khata transfer — a separate application to the BBMP "
                         "after registration, and the step most often skipped.",
        "infrastructure": {
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
        },
    },
    "Mumbai": {
        "state": "Maharashtra",
        "latitude": 19.08,
        "stamp_duty_bands": ((float("inf"), 0.06),),   # 5% + 1% metro cess
        "registration_rate": 0.01,
        "registration_cap_inr": 30_000,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.01,
        "title_document": "Property Card (city survey) and the chain of index-II",
        "title_authority": "City Survey Office / BMC",
        "title_note": ("There is no khata here. Ask for the property card and "
                       "index-II entries; for land outside the city survey, the "
                       "7/12 extract."),
        "transfer_step": "Mutation in the property card, plus the society "
                         "share-certificate transfer.",
        "infrastructure": {
            "Bandra West": [("Established prime locality", 0.06),
                            ("Bandra-Worli Sea Link", 0.04)],
            "Powai": [("Employment cluster", 0.05)],
            "Andheri": [("Metro Line 1 and airport proximity", 0.05)],
            "Thane": [("Metro Line 4 (under construction)", 0.03)],
        },
    },
    "Delhi NCR": {
        "state": "Delhi",
        "latitude": 28.61,
        "stamp_duty_bands": ((float("inf"), 0.06),),
        "registration_rate": 0.01,
        "registration_cap_inr": None,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.02,      # 4% for a sole woman purchaser
        "title_document": "Mutation record and the chain of conveyance deeds",
        "title_authority": "MCD / DDA",
        "title_note": ("Watch for GPA sales — a general power of attorney does "
                       "not convey title. Suraj Lamp v. State of Haryana (2011) "
                       "settled that, and Delhi is where it bites most often."),
        "transfer_step": "Mutation in the MCD records after registration.",
        "infrastructure": {
            "Gurugram": [("Rapid Metro and Cyber City employment", 0.06)],
            "Vasant Kunj": [("Established south-Delhi locality", 0.05)],
            "Noida": [("Aqua Line metro and Jewar airport corridor", 0.05)],
            "Dwarka": [("Airport Express Line", 0.04)],
        },
    },
    "Hyderabad": {
        "state": "Telangana",
        "latitude": 17.39,
        "stamp_duty_bands": ((float("inf"), 0.04),),
        "registration_rate": 0.005,
        "registration_cap_inr": None,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.375,    # 1.5% transfer duty, expressed on the 4%
        "women_concession": 0.0,
        "title_document": "Pattadar passbook and the market-value certificate",
        "title_authority": "GHMC / Dharani portal",
        "title_note": ("Agricultural land is recorded in Dharani; check that a "
                       "plot has been formally converted before it is sold as "
                       "residential."),
        "transfer_step": "Mutation through GHMC and the Dharani record.",
        "infrastructure": {
            "Gachibowli": [("HITEC City employment", 0.06)],
            "Kondapur": [("Outer Ring Road", 0.04)],
            "Banjara Hills": [("Established prime locality", 0.05)],
        },
    },
    "Chennai": {
        "state": "Tamil Nadu",
        "latitude": 13.08,
        "stamp_duty_bands": ((float("inf"), 0.07),),
        "registration_rate": 0.04,     # among the highest in India
        "registration_cap_inr": None,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.0,
        "title_document": "Patta and chitta extract",
        "title_authority": "Chennai Corporation / TN Revenue",
        "title_note": ("Patta is the record of title to the land. A flat buyer "
                       "should still see the undivided-share patta position."),
        "transfer_step": "Patta transfer through the taluk office.",
        "infrastructure": {
            "OMR": [("IT corridor", 0.05)],
            "Adyar": [("Established central locality", 0.05)],
            "Velachery": [("MRTS connectivity", 0.03)],
        },
    },
    "Pune": {
        "state": "Maharashtra",
        "latitude": 18.52,
        "stamp_duty_bands": ((float("inf"), 0.06),),
        "registration_rate": 0.01,
        "registration_cap_inr": 30_000,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.01,
        "title_document": "Property Card and 7/12 extract",
        "title_authority": "City Survey Office / PMC",
        "title_note": "Same regime as Mumbai; no khata.",
        "transfer_step": "Mutation in the property card.",
        "infrastructure": {
            "Hinjewadi": [("Rajiv Gandhi Infotech Park", 0.06)],
            "Kharadi": [("EON IT Park", 0.05)],
            "Baner": [("Mumbai-Bengaluru highway access", 0.04)],
        },
    },
    "Kolkata": {
        "state": "West Bengal",
        "latitude": 22.57,
        "stamp_duty_bands": ((4_000_000, 0.06), (float("inf"), 0.07)),
        "registration_rate": 0.01,
        "registration_cap_inr": None,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.0,
        "title_document": "Porcha (RS/LR record of rights)",
        "title_authority": "KMC / BL&LRO",
        "title_note": "Check the RS and LR records agree on the plot.",
        "transfer_step": "Mutation with the KMC and the land-records office.",
        "infrastructure": {
            "Salt Lake": [("Sector V employment", 0.05)],
            "New Town": [("Planned township", 0.04)],
        },
    },
    "Ahmedabad": {
        "state": "Gujarat",
        "latitude": 23.02,
        "stamp_duty_bands": ((float("inf"), 0.049),),
        "registration_rate": 0.01,
        "registration_cap_inr": None,
        "cess_on_duty": 0.0,
        "surcharge_on_duty": 0.0,
        "women_concession": 0.01,      # registration fee waived for women
        "title_document": "7/12 extract and the property card",
        "title_authority": "AMC / Revenue Department",
        "title_note": "Confirm non-agricultural (NA) conversion for any plot.",
        "transfer_step": "Mutation in the revenue record.",
        "infrastructure": {
            "SG Highway": [("Commercial spine", 0.05)],
            "Satellite": [("Established locality", 0.04)],
        },
    },
}

DEFAULT_CITY = "Bengaluru"


def get(city: Optional[str]) -> Dict[str, Any]:
    """Facts for a city, falling back to the beachhead rather than guessing."""
    if not city:
        return CITIES[DEFAULT_CITY]
    key = city.strip()
    if key in CITIES:
        return CITIES[key]
    lowered = {k.lower(): k for k in CITIES}
    if key.lower() in lowered:
        return CITIES[lowered[key.lower()]]
    return CITIES[DEFAULT_CITY]


def is_known(city: Optional[str]) -> bool:
    if not city:
        return False
    return city.strip().lower() in {k.lower() for k in CITIES}


def names() -> List[str]:
    return sorted(CITIES)


def summary() -> Dict[str, Any]:
    """What a buyer pays and what they must ask for, city by city."""
    rows = []
    for name in names():
        c = CITIES[name]
        top = c["stamp_duty_bands"][-1][1]
        total = (top * (1 + c["cess_on_duty"] + c["surcharge_on_duty"])
                 + c["registration_rate"])
        rows.append({
            "city": name,
            "state": c["state"],
            "stamp_duty_percent": round(top * 100, 2),
            "registration_percent": round(c["registration_rate"] * 100, 2),
            "total_percent": round(total * 100, 2),
            "women_concession_percent": round(c["women_concession"] * 100, 2),
            "title_document": c["title_document"],
            "title_authority": c["title_authority"],
            "latitude": c["latitude"],
        })
    rows.sort(key=lambda r: r["total_percent"], reverse=True)
    return {
        "cities": rows,
        "rates_as_of": RATES_AS_OF,
        "note": ("Acquisition cost is not a national number. The spread between "
                 "the cheapest and dearest state here is close to double, and "
                 "the document a buyer must demand has a different name in "
                 "every one of them."),
    }
