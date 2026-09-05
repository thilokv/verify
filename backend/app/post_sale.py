"""Post-sale features: rental management and farmland guidance (Phase 4)."""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .models import RentalListing, FarmlandGuidance, Property, utcnow


# Seasonal crop recommendations by region and soil type
CROP_RECOMMENDATIONS = {
    "Kharif": {  # June-October (monsoon season)
        "Black soil": [
            {"crop": "Cotton", "yield_per_acre": 8, "water_req": "medium"},
            {"crop": "Jowar", "yield_per_acre": 15, "water_req": "low"},
            {"crop": "Groundnut", "yield_per_acre": 20, "water_req": "low"},
        ],
        "Red soil": [
            {"crop": "Sugarcane", "yield_per_acre": 50, "water_req": "high"},
            {"crop": "Maize", "yield_per_acre": 25, "water_req": "medium"},
            {"crop": "Groundnut", "yield_per_acre": 18, "water_req": "low"},
        ],
        "Laterite": [
            {"crop": "Paddy", "yield_per_acre": 30, "water_req": "high"},
            {"crop": "Coconut", "yield_per_acre": 20, "water_req": "medium"},
        ],
    },
    "Rabi": {  # October-March (winter season)
        "Black soil": [
            {"crop": "Wheat", "yield_per_acre": 20, "water_req": "medium"},
            {"crop": "Chickpea", "yield_per_acre": 10, "water_req": "low"},
            {"crop": "Mustard", "yield_per_acre": 8, "water_req": "low"},
        ],
        "Red soil": [
            {"crop": "Tomato", "yield_per_acre": 100, "water_req": "medium"},
            {"crop": "Onion", "yield_per_acre": 120, "water_req": "low"},
        ],
        "Laterite": [
            {"crop": "Cardamom", "yield_per_acre": 4, "water_req": "high"},
            {"crop": "Pepper", "yield_per_acre": 3, "water_req": "high"},
        ],
    },
    "Summer": {  # March-May (dry season)
        "Black soil": [
            {"crop": "Pulses", "yield_per_acre": 12, "water_req": "low"},
        ],
        "Red soil": [
            {"crop": "Mango", "yield_per_acre": 8, "water_req": "medium"},
        ],
    },
}


def register_rental(session: Session, property_id: int, monthly_rent_inr: float,
                   lease_terms: str = "11 months", furnishing: str = "Unfurnished",
                   available_from: Optional[datetime] = None) -> Dict[str, object]:
    """Register a property for rental (Phase 4: post-sale management)."""
    prop = session.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise ValueError(f"Property {property_id} not found")

    existing = session.query(RentalListing).filter_by(property_id=property_id).first()
    if existing:
        raise ValueError(f"Property {property_id} already registered for rental")

    rental = RentalListing(
        property_id=property_id,
        monthly_rent_inr=monthly_rent_inr,
        lease_terms=lease_terms,
        furnishing=furnishing,
        available_from=available_from or utcnow(),
        status="ACTIVE",
    )
    session.add(rental)
    session.commit()

    return rental_to_dict(rental, prop)


def rental_to_dict(rental: RentalListing, prop: Optional[Property] = None) -> Dict[str, object]:
    """Serialize rental listing."""
    return {
        "id": rental.id,
        "property_id": rental.property_id,
        "property_title": prop.title if prop else "",
        "monthly_rent_inr": rental.monthly_rent_inr,
        "deposit_inr": rental.deposit_inr,
        "lease_terms": rental.lease_terms,
        "furnishing": rental.furnishing,
        "available_from": rental.available_from.isoformat() if rental.available_from else None,
        "status": rental.status,
        "tenant_name": rental.tenant_name,
        "lease_start": rental.lease_start.isoformat() if rental.lease_start else None,
        "lease_end": rental.lease_end.isoformat() if rental.lease_end else None,
    }


def match_tenant(session: Session, rental_id: int, tenant_name: str,
                tenant_phone: str, lease_start: datetime, lease_end: datetime) -> Dict[str, object]:
    """Record tenant match for a rental (Phase 4)."""
    rental = session.query(RentalListing).filter_by(id=rental_id).first()
    if not rental:
        raise ValueError(f"Rental {rental_id} not found")

    rental.tenant_name = tenant_name
    rental.tenant_phone = tenant_phone
    rental.lease_start = lease_start
    rental.lease_end = lease_end
    rental.status = "LEASED"

    session.commit()

    prop = session.query(Property).filter_by(id=rental.property_id).first()
    return rental_to_dict(rental, prop)


def register_farmland(session: Session, property_id: int, soil_type: str,
                     region: str, size_acres: float, irrigation_type: str,
                     water_availability: str = "Moderate") -> Dict[str, object]:
    """Register farmland for seasonal guidance (Phase 4: India-specific feature)."""
    prop = session.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise ValueError(f"Property {property_id} not found")

    existing = session.query(FarmlandGuidance).filter_by(property_id=property_id).first()
    if existing:
        raise ValueError(f"Property {property_id} already registered for guidance")

    current_season = get_current_season()
    recommended_crops = CROP_RECOMMENDATIONS.get(current_season, {}).get(soil_type, [])

    guidance = FarmlandGuidance(
        property_id=property_id,
        soil_type=soil_type,
        region=region,
        size_acres=size_acres,
        current_season=current_season,
        recommended_crops=recommended_crops,
        irrigation_type=irrigation_type,
        water_availability=water_availability,
    )
    session.add(guidance)
    session.commit()

    return farmland_to_dict(guidance, prop)


def farmland_to_dict(guidance: FarmlandGuidance, prop: Optional[Property] = None) -> Dict[str, object]:
    """Serialize farmland guidance."""
    return {
        "id": guidance.id,
        "property_id": guidance.property_id,
        "property_title": prop.title if prop else "",
        "soil_type": guidance.soil_type,
        "region": guidance.region,
        "size_acres": guidance.size_acres,
        "current_season": guidance.current_season,
        "recommended_crops": guidance.recommended_crops,
        "irrigation_type": guidance.irrigation_type,
        "water_availability": guidance.water_availability,
        "last_guidance_sent": guidance.last_guidance_sent.isoformat() if guidance.last_guidance_sent else None,
    }


def get_current_season() -> str:
    """Current Indian cropping season. Ranges must not overlap — October
    belongs to Rabi (sowing), not to the closing Kharif harvest."""
    month = datetime.now().month
    if month in (6, 7, 8, 9):          # June-September, monsoon
        return "Kharif"
    if month in (10, 11, 12, 1, 2):    # October-February, post-monsoon
        return "Rabi"
    return "Summer"                    # March-May


def get_seasonal_guidance(session: Session, guidance_id: int) -> Dict[str, object]:
    """Get current seasonal guidance for farmland."""
    guidance = session.query(FarmlandGuidance).filter_by(id=guidance_id).first()
    if not guidance:
        raise ValueError(f"Guidance {guidance_id} not found")

    current_season = get_current_season()
    prop = session.query(Property).filter_by(id=guidance.property_id).first()

    # Update season if changed
    if guidance.current_season != current_season:
        guidance.current_season = current_season
        guidance.recommended_crops = CROP_RECOMMENDATIONS.get(current_season, {}).get(guidance.soil_type, [])
        guidance.last_guidance_sent = utcnow()
        session.commit()

    result = farmland_to_dict(guidance, prop)
    result["guidance"] = {
        "season": current_season,
        "irrigation_note": f"Water availability: {guidance.water_availability}",
        "soil_note": f"Recommended for {guidance.soil_type}",
        "size_note": f"{guidance.size_acres} acres — yields estimated at recommended crop rates",
    }
    return result


def post_sale_summary(session: Session) -> Dict[str, object]:
    """Phase 4: post-sale features summary."""
    rental_count = session.query(RentalListing).filter_by(status="ACTIVE").count()
    leased_count = session.query(RentalListing).filter_by(status="LEASED").count()
    farmland_count = session.query(FarmlandGuidance).count()

    return {
        "phase": 4,
        "description": "Post-sale and ongoing relationship management",
        "features": {
            "rental_management": {
                "active_listings": rental_count,
                "leased_properties": leased_count,
                # A leased property is still a property under management, so it
                # counts towards "available" — checking active alone reported
                # "no_properties" while a tenant was sitting in one.
                "status": "available" if (rental_count + leased_count) > 0 else "no_properties",
            },
            "farmland_guidance": {
                "properties_registered": farmland_count,
                "current_season": get_current_season(),
                "status": "available" if farmland_count > 0 else "no_properties",
            },
        },
        "next_steps": [
            "Expand rental tenant matching",
            "Add tenant rent reminders",
            "Expand farmland guidance to more crops",
            "Add land-use regulations by region",
        ],
    }
