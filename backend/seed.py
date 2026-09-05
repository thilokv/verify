"""Seed the pilot catalogue.

Localities and price bands are realistic for Bengaluru; the properties
themselves are invented. Verification status is deliberately mixed so the
FR-5 gate is observable: unverified and flagged stock must not appear in
buyer-facing search results.

    python seed.py            # create + embed
    python seed.py --reset    # drop everything first
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal, engine, init_db          # noqa: E402
from app.models import Base, Developer, Property           # noqa: E402
from app.search import reindex                             # noqa: E402

DEVELOPERS = [
    {"company_name": "Prestige Habitat", "contact_person": "R. Menon",
     "phone_number": "+919800000001", "email": "sales@prestigehabitat.example"},
    {"company_name": "Sobha Greenfield", "contact_person": "A. Nair",
     "phone_number": "+919800000002", "email": "sales@sobhagreen.example"},
    {"company_name": "Brigade Land Co", "contact_person": "S. Rao",
     "phone_number": "+919800000003", "email": "plots@brigadeland.example"},
]

# (title, type, config, locality, price_lakh, sqft, possession, facing,
#  khata, rera, verified, note, description, amenities)
LISTINGS = [
    ("3BHK apartment, Lakeview Residences", "Flat", "3BHK", "Whitefield",
     124, 1580, "Ready to move", "East", "A-Khata",
     "PRM/KA/RERA/1251/446/PR/180214/002145", True,
     "Advocate title opinion on file; EC clear for 30 years.",
     "Corner unit in a gated community with clubhouse and lap pool. Walking "
     "distance to an IB school, 20 minutes to ITPL.",
     ["Gated community", "Clubhouse", "Swimming pool", "Covered parking", "Power backup"]),

    ("2BHK apartment, Sarjapur Central", "Flat", "2BHK", "Sarjapur",
     78, 1120, "Ready to move", "North", "A-Khata",
     "PRM/KA/RERA/1251/309/PR/171020/001199", True,
     "Advocate title opinion on file; khata extract verified.",
     "Compact, efficient layout on the 6th floor of a low-density block. Close "
     "to Wipro corporate office and the outer ring road.",
     ["Gated community", "Gym", "Children's play area", "Covered parking"]),

    ("4BHK villa, Hebbal Northgate", "Villa", "4BHK", "Hebbal",
     315, 3400, "Ready to move", "East", "A-Khata",
     "PRM/KA/RERA/1250/303/PR/160919/000876", True,
     "Advocate title opinion on file; parent documents traced to 1987.",
     "Independent villa with a private garden and a double-height living room. "
     "Fifteen minutes to the airport expressway.",
     ["Private garden", "Servant quarters", "Solar water", "Gated community", "Home office"]),

    ("Residential plot, Devanahalli Aerospace Corridor", "Plot", None, "Devanahalli",
     92, 2400, "Ready to move", "East", "A-Khata",
     "PRM/KA/RERA/1251/472/PR/190411/002673", True,
     "Advocate title opinion on file; conversion order verified.",
     "DTCP-approved plot on a 40-foot road in a developing corridor near the "
     "aerospace SEZ. Compound wall in place.",
     ["Corner plot", "40ft road", "Compound wall", "Water connection"]),

    ("3BHK apartment, Electronic City Phase 1", "Flat", "3BHK", "Electronic City",
     96, 1450, "Under construction", "West", "A-Khata",
     "PRM/KA/RERA/1251/446/PR/200128/003021", True,
     "Advocate title opinion on file; construction-linked payment plan.",
     "Under-construction tower with possession expected in 14 months. Direct "
     "access to the elevated expressway.",
     ["Gated community", "Clubhouse", "Jogging track", "Rainwater harvesting"]),

    ("2BHK apartment, Yelahanka New Town", "Flat", "2BHK", "Yelahanka",
     68, 1050, "Ready to move", "South", "B-Khata",
     None, False,
     "B-Khata declared at listing — regularisation status must be checked "
     "before any visit is arranged.",
     "Well-maintained resale flat in an established neighbourhood with mature "
     "tree cover.",
     ["Lift", "Borewell", "Two-wheeler parking"]),

    ("Farm land, Kanakapura Road", "Plot", None, "Kanakapura",
     145, 43560, "Ready to move", "North", None,
     None, False,
     "Agricultural land — conversion status and 79A/B compliance not yet "
     "confirmed. Do not proceed without an advocate opinion.",
     "One acre of agricultural land with an existing borewell and a small "
     "farmhouse structure, 12 km past the ring road junction.",
     ["Borewell", "Farmhouse structure", "Road frontage"]),

    ("3BHK apartment, Koramangala 5th Block", "Flat", "3BHK", "Koramangala",
     235, 1720, "Ready to move", "East", "A-Khata",
     "PRM/KA/RERA/1250/303/PR/151107/000512", True,
     "Advocate title opinion on file.",
     "Premium resale in a boutique building of twelve units, on a quiet "
     "residential street a short walk from the 80-foot road.",
     ["Covered parking", "Power backup", "Lift", "Security"]),

    ("Commercial floor, Indiranagar 100ft Road", "Commercial", None, "Indiranagar",
     480, 2800, "Ready to move", "West", "A-Khata",
     "PRM/KA/RERA/1251/446/PR/180830/002401", True,
     "Advocate title opinion on file; commercial conversion verified.",
     "Full floor with 2,800 sq ft of carpet area on the main road, suitable "
     "for a clinic, studio or office.",
     ["Lift", "Three-phase power", "Parking", "Main road frontage"]),

    ("2BHK apartment, Marathahalli", "Flat", "2BHK", "Marathahalli",
     72, 1080, "Ready to move", "North", "A-Khata",
     "PRM/KA/RERA/1251/309/PR/170622/001033", False,
     "Encumbrance certificate shows a subsisting mortgage. Flagged pending "
     "discharge confirmation.",
     "Resale flat close to the outer ring road tech corridor.",
     ["Gym", "Covered parking", "Power backup"]),
]


def run(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(bind=engine)
    init_db()

    db = SessionLocal()
    try:
        if db.query(Property).count() and not reset:
            print("Catalogue already seeded. Use --reset to rebuild.")
            return

        devs = []
        for spec in DEVELOPERS:
            dev = Developer(**spec)
            db.add(dev)
            devs.append(dev)
        db.flush()

        for i, row in enumerate(LISTINGS):
            (title, ptype, config, loc, lakh, sqft, possession, facing,
             khata, rera, verified, note, desc, amenities) = row
            db.add(Property(
                developer_id=devs[i % len(devs)].id,
                title=title,
                property_type=ptype,
                config=config,
                location_name=loc,
                city="Bengaluru",
                price_inr=lakh * 100000.0,
                area_sqft=sqft,
                possession=possession,
                facing=facing,
                khata=khata,
                rera_id=rera,
                is_verified=verified,
                verification_note=note,
                description=desc,
                amenities=amenities,
            ))
        db.commit()

        n = reindex(db)
        total = db.query(Property).count()
        verified = db.query(Property).filter(Property.is_verified.is_(True)).count()
        print(f"Seeded {total} listings ({verified} verified), embedded {n}.")
    finally:
        db.close()


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
