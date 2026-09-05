"""Playbook execution tracking — beachhead market, validation, go-to-market."""

from typing import Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from .models import BeachheadMarket, Interview, RERACompliance, utcnow


def init_beachhead(session: Session, city: str, state: str,
                   property_types: List[str]) -> Dict[str, object]:
    """Initialize or update the beachhead market (Section 11 checklist)."""
    existing = session.query(BeachheadMarket).filter_by(city=city, state=state).first()
    if existing:
        # Status is left alone: re-saving the property-type list is an edit,
        # not a launch decision, and must not flip PLANNING to ACTIVE.
        existing.property_types = property_types
        existing.updated_at = utcnow()
        session.commit()
        return existing_to_dict(existing)

    market = BeachheadMarket(
        city=city,
        state=state,
        property_types=property_types,
        status="PLANNING",
    )
    session.add(market)
    session.commit()
    return existing_to_dict(market)


def existing_to_dict(market: BeachheadMarket) -> Dict[str, object]:
    """Serialize beachhead market with validation progress."""
    return {
        "id": market.id,
        "city": market.city,
        "state": market.state,
        "property_types": market.property_types,
        "status": market.status,
        "validation": {
            "interviews": {
                "sellers": market.interviews_sellers,
                "buyers": market.interviews_buyers,
                "competitors": market.interviews_competitors,
                "total": market.interviews_sellers + market.interviews_buyers + market.interviews_competitors,
                "target": market.target_interviews,
                "progress": (market.interviews_sellers + market.interviews_buyers + market.interviews_competitors)
                           / market.target_interviews if market.target_interviews else 0,
            },
            "manual_pilot": {
                "listings": market.manual_pilot_listings,
                "target_listings": market.target_listings,
                "closed_deals": market.manual_pilot_closed_deals,
                "target_deals": market.target_deals,
                "progress": market.manual_pilot_closed_deals / market.target_deals if market.target_deals else 0,
            },
            "next_steps": validation_checklist(market),
        },
    }


def validation_checklist(market: BeachheadMarket) -> List[str]:
    """Section 11: ordered checklist to begin execution."""
    done = []
    pending = []

    if market.city:
        done.append("✓ Beachhead city chosen: " + market.city)
    else:
        pending.append("[ ] Choose beachhead city and property type")

    total_interviews = market.interviews_sellers + market.interviews_buyers + market.interviews_competitors
    if total_interviews >= market.target_interviews:
        done.append(f"✓ Validation interviews complete ({total_interviews}/{market.target_interviews})")
    else:
        pending.append(f"[ ] Complete {market.target_interviews - total_interviews} more interviews")

    if market.manual_pilot_listings >= market.target_listings:
        done.append(f"✓ Manual pilot listings loaded ({market.manual_pilot_listings}/{market.target_listings})")
    else:
        pending.append(f"[ ] Load {market.target_listings - market.manual_pilot_listings} listings via manual WhatsApp/spreadsheet")

    if market.manual_pilot_closed_deals >= market.target_deals:
        done.append(f"✓ Closed manual pilot deals ({market.manual_pilot_closed_deals}/{market.target_deals})")
        pending.append("[ ] Move to Phase 2: Add AI matching layer")
    else:
        pending.append(f"[ ] Close {market.target_deals - market.manual_pilot_closed_deals} more manual deals before AI layer")

    return done + pending


def log_interview(session: Session, interview_type: str, name: str, phone: str,
                  city: str, validated_desire: bool = False,
                  identified_pain: bool = False,
                  alternative_used: bool = False,
                  notes: str = "", key_insights: List[str] = None) -> Dict[str, object]:
    """Log a validation interview (sellers, buyers, or competitor platform users)."""
    interview = Interview(
        interview_type=interview_type,  # "seller" | "buyer" | "competitor_user"
        name=name,
        phone=phone,
        city=city,
        validated_desire=validated_desire,
        identified_pain=identified_pain,
        alternative_used=alternative_used,
        notes=notes,
        key_insights=key_insights or [],
    )
    session.add(interview)

    # Update beachhead progress.
    # Matched on city alone (case-insensitive) and NOT on status: a market is
    # created as PLANNING, and interviews are exactly the work that happens
    # while it is still in planning. Filtering on ACTIVE here silently dropped
    # every count until the market was saved a second time.
    market = session.query(BeachheadMarket).filter(
        func.lower(BeachheadMarket.city) == (city or "").strip().lower(),
        BeachheadMarket.status != "ABANDONED",
    ).first()
    if market:
        if interview_type == "seller":
            market.interviews_sellers += 1
        elif interview_type == "buyer":
            market.interviews_buyers += 1
        elif interview_type in ("competitor_user", "competitor"):
            market.interviews_competitors += 1

    session.commit()
    return {
        "id": interview.id,
        "type": interview_type,
        "name": name,
        "phone": phone,
        "city": city,
        "validated_desire": validated_desire,
        "identified_pain": identified_pain,
        "alternative_used": alternative_used,
        "notes": notes,
        "key_insights": key_insights or [],
        "created_at": interview.created_at.isoformat(),
    }


def init_rera_state(session: Session, state: str, authority_name: str = "",
                    authority_email: str = "", authority_website: str = "") -> Dict[str, object]:
    """Register RERA state compliance tracking (Section 7.5)."""
    existing = session.query(RERACompliance).filter_by(state=state).first()
    if existing:
        return rera_to_dict(existing)

    rera = RERACompliance(
        state=state,
        authority_name=authority_name,
        authority_email=authority_email,
        authority_website=authority_website,
    )
    session.add(rera)
    session.commit()
    return rera_to_dict(rera)


def rera_to_dict(rera: RERACompliance) -> Dict[str, object]:
    """Serialize RERA compliance status."""
    return {
        "id": rera.id,
        "state": rera.state,
        "platform_registered": rera.platform_registered,
        "registration_number": rera.registration_number,
        "requirements": {
            "agent_registration": rera.requires_agent_registration,
            "project_registration": rera.requires_project_registration,
            "buyer_agreement": rera.requires_buyer_agreement,
        },
        "authority": {
            "name": rera.authority_name,
            "email": rera.authority_email,
            "website": rera.authority_website,
        },
    }


def revenue_model_summary(session: Session) -> Dict[str, object]:
    """Section 4.1: Premier Agent revenue model status."""
    from .models import Agent, LeadAssignment, Payment

    from sqlalchemy import func

    total_agents = session.query(Agent).filter_by(status="ACTIVE").count()
    total_leads_sent = session.query(LeadAssignment).count()
    total_revenue = session.query(func.sum(Payment.amount_inr)).filter(
        Payment.payment_status == "PAID"
    ).scalar() or 0.0

    return {
        "model": "Premier Agent (Zillow model)",
        "description": "Charging agents and brokers for qualified buyer leads, not direct charges to buyers or sellers",
        "active_agents": total_agents,
        "leads_sent_total": total_leads_sent,
        "revenue_collected_inr": float(total_revenue),
        "next_step": "Register agents → Assign leads → Track conversions → Collect payments"
    }


# --------------------------------------------------------------- build phases
# Playbook §8 sequences the work so the expensive parts come last: prove demand
# by hand, then add matching, then build the product, then extend past the sale.
# The status below is derived from what is actually in the database, so it can
# contradict the person reading it — which is the point.

PHASES = (
    (1, "Manual pilot",
     "WhatsApp and a spreadsheet. Prove that real buyers and sellers want this "
     "before writing matching code.",
     "Interviews done and the first deals closed by hand."),
    (2, "AI matching layer",
     "Turn a plain-language brief into structured constraints and rank the "
     "stock against it.",
     "Listings in the catalogue and a brief that returns ranked matches."),
    (3, "The product",
     "Backend, database, verification gating, seller intake, document triage.",
     "Endpoints serving a real catalogue behind the verification gate."),
    (4, "Past the sale",
     "Rentals and farmland guidance — the India-specific reasons a buyer comes "
     "back after the transaction closes.",
     "Properties under management after their sale."),
)


def build_status(session: Session) -> Dict[str, object]:
    """Which phase the deployment is actually in, measured, not asserted."""
    from .models import Agent, FarmlandGuidance, Payment, Property, RentalListing
    from .config import settings

    market = session.query(BeachheadMarket).filter(
        BeachheadMarket.status != "ABANDONED").first()

    interviews = 0
    deals = 0
    target_interviews, target_deals = 15, 5
    if market:
        interviews = (market.interviews_sellers + market.interviews_buyers
                      + market.interviews_competitors)
        deals = market.manual_pilot_closed_deals
        target_interviews = market.target_interviews or 15
        target_deals = market.target_deals or 5

    listings = session.query(Property).count()
    verified = session.query(Property).filter(Property.is_verified.is_(True)).count()
    agents = session.query(Agent).filter_by(status="ACTIVE").count()
    managed = (session.query(RentalListing).count()
               + session.query(FarmlandGuidance).count())

    done = {
        1: interviews >= target_interviews and deals >= target_deals,
        2: listings > 0,
        3: verified > 0,
        4: managed > 0,
    }

    phases = []
    for number, name, what, complete_when in PHASES:
        if number == 1:
            evidence = (f"{interviews} of {target_interviews} interviews, "
                        f"{deals} of {target_deals} deals closed by hand")
        elif number == 2:
            evidence = (f"{listings} listings in the catalogue, matching is "
                        f"{'semantic' if settings.EMBEDDING_PROVIDER != 'hash' else 'lexical'}")
        elif number == 3:
            evidence = f"{verified} of {listings} listings past the verification gate"
        else:
            evidence = f"{managed} properties under management after sale"
        phases.append({
            "number": number,
            "name": name,
            "what": what,
            "complete_when": complete_when,
            "done": done[number],
            "evidence": evidence,
        })

    # The playbook's own warning: the product is meant to come AFTER the manual
    # proof, and a deployment can easily have it the other way round.
    out_of_order = done[3] and not done[1]

    return {
        "beachhead": (f"{market.city}, {market.state}" if market else None),
        "phases": phases,
        "out_of_order": out_of_order,
        "warning": (
            "The product is built but the manual pilot is not proven. Playbook "
            "§8 puts Phase 1 first on purpose: close five deals by hand before "
            "trusting the matching layer, or you are scaling something nobody "
            "has confirmed they want." if out_of_order else None),
        "revenue_model": {
            "chosen": "lead_generation",
            "detail": ("Agents and brokers pay for qualified buyer leads. "
                       "Playbook §7.6: this cannot be mixed with a "
                       "'skip the broker' pitch to buyers — the buyer-facing "
                       "promise is better-matched agents, not no agents."),
            "active_agents": agents,
        },
        "limits": [
            ("No money moves",
             "Settling a fee marks a ledger row PAID. There is no payment "
             "gateway behind it."),
            ("Matching is lexical" if settings.EMBEDDING_PROVIDER == "hash"
             else "Matching is semantic",
             "Word overlap, not meaning. Set EMBEDDING_PROVIDER before showing "
             "this to buyers." if settings.EMBEDDING_PROVIDER == "hash"
             else f"Embeddings from {settings.EMBEDDING_PROVIDER}."),
            ("Document checks triage, they do not clear title",
             "Machine checks narrow what an advocate looks at. Only the "
             "advocate's opinion moves a listing past the gate."),
            ("Crop guidance is a small curated table",
             "Nine soil and season combinations, hand-entered. Not agronomic "
             "advice for a specific plot."),
            ("RERA support is record-keeping",
             "It tracks whether you have registered. It does not register you "
             "and does not tell you whether selling leads makes you an "
             "intermediary in a given state."),
            ("Rate limits are per process",
             "Four workers means four times the written limit. Move the "
             "counters to Redis before scaling out."),
        ],
    }
