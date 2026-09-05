"""Revenue model: Premier Agent lead generation (Section 4.1 of playbook)."""

from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from .models import Agent, LeadAssignment, Payment, utcnow, Lead, Property

# Which listing types each agent specialization is allowed to receive.
# An agent registers as "residential"; a listing is a "Villa". Without this
# map the two never compare equal and every specialised agent is skipped.
SPECIALIZATION_COVERS = {
    "residential": {"flat", "villa", "apartment", "plot", "house"},
    "commercial": {"commercial", "office", "retail", "plot"},
    "farmland": {"agricultural", "farmland", "farm land", "plot"},
}


def register_agent(session: Session, name: str, phone: str, email: str,
                   company: str = "", specialization: str = "residential",
                   service_areas: List[str] = None,
                   rera_id: str = "", rera_state: str = "",
                   lead_fee_inr: float = 500.0) -> Dict[str, object]:
    """Register a real estate agent/broker for lead generation (Premier Agent)."""
    existing = session.query(Agent).filter_by(phone=phone).first()
    if existing:
        raise ValueError(f"Agent with phone {phone} already registered")

    agent = Agent(
        name=name,
        phone=phone,
        email=email,
        company=company,
        specialization=specialization,
        service_areas=service_areas or [],
        rera_id=rera_id,
        rera_state=rera_state,
        lead_fee_inr=lead_fee_inr,
        status="ACTIVE",
    )
    session.add(agent)
    session.commit()
    return agent_to_dict(agent)


def agent_to_dict(agent: Agent) -> Dict[str, object]:
    """Serialize agent profile."""
    return {
        "id": agent.id,
        "name": agent.name,
        "phone": agent.phone,
        "email": agent.email,
        "company": agent.company,
        "specialization": agent.specialization,
        "service_areas": agent.service_areas,
        "rera_id": agent.rera_id,
        "rera_state": agent.rera_state,
        "verified_broker": agent.verified_broker,
        "status": agent.status,
        "lead_fee_inr": agent.lead_fee_inr,
        "commission_percent": agent.commission_percent,
        "leads_received": agent.leads_received,
        "total_earnings_inr": agent.total_earnings_inr,
    }


def assign_lead_to_agent(session: Session, lead_id: int, property_id: Optional[int] = None) -> Dict[str, object]:
    """Find best-matched agent(s) for a qualified lead and assign it (earn lead fee)."""
    lead = session.query(Lead).filter_by(id=lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    agents = session.query(Agent).filter_by(status="ACTIVE").all()
    if not agents:
        return {"status": "no_agents_available", "lead_id": lead_id}

    # An agent is eligible only if they actually cover the buyer's locality.
    # There is deliberately NO fallback to "any active agent": a fee is raised
    # against the agent by this call, and billing a broker for a locality they
    # do not work is how you lose the broker.
    locality = (lead.preferred_location or "").strip().lower()
    wanted_type = (lead.property_type or "").strip().lower()

    def covers_area(agent: Agent) -> bool:
        areas = [str(a).strip().lower() for a in (agent.service_areas or [])]
        return bool(locality) and locality in areas

    def covers_type(agent: Agent) -> bool:
        # specialization and property_type are different vocabularies: an agent
        # is "residential", a listing is a "Villa". Map one onto the other, and
        # treat anything unmapped as compatible rather than silently excluding.
        spec = (agent.specialization or "all").strip().lower()
        if spec in ("", "all") or not wanted_type:
            return True
        return wanted_type in SPECIALIZATION_COVERS.get(spec, set())

    eligible = [a for a in agents if covers_area(a) and covers_type(a)]
    if not eligible:
        return {
            "status": "no_agent_covers_locality",
            "lead_id": lead_id,
            "locality": lead.preferred_location,
            "fee_charged_inr": 0.0,
            "detail": ("No active agent lists this locality in their service "
                       "areas. Recruit an agent there, or widen an existing "
                       "agent's service areas. No fee has been raised."),
        }

    # Spread the leads: among eligible agents give it to whoever has received
    # the fewest so far, so the first row in the table does not take everything.
    best_agent = min(eligible, key=lambda a: (a.leads_received or 0, a.id))

    # Create assignment and charge lead fee
    assignment = LeadAssignment(
        lead_id=lead_id,
        agent_id=best_agent.id,
        property_id=property_id,
        fee_charged_inr=best_agent.lead_fee_inr,
        status="SENT",
    )
    session.add(assignment)
    # Flush so the assignment gets its primary key. Without this the payment
    # below is written with lead_assignment_id=None and update_lead_status()
    # can never find it again — the fee silently never becomes collectable.
    session.flush()

    # Record payment due
    payment = Payment(
        agent_id=best_agent.id,
        lead_assignment_id=assignment.id,
        amount_inr=best_agent.lead_fee_inr,
        payment_status="PENDING",
    )
    session.add(payment)

    # Update agent stats
    best_agent.leads_received += 1

    session.commit()

    return {
        "assignment_id": assignment.id,
        "lead_id": lead_id,
        "agent_id": best_agent.id,
        "agent_name": best_agent.name,
        "agent_phone": best_agent.phone,
        "fee_charged_inr": best_agent.lead_fee_inr,
        "status": "sent_to_agent",
    }


def update_lead_status(session: Session, assignment_id: int, new_status: str) -> Dict[str, object]:
    """Update lead conversion status (CONTACTED|INTERESTED|CONVERTED|ABANDONED)."""
    assignment = session.query(LeadAssignment).filter_by(id=assignment_id).first()
    if not assignment:
        raise ValueError(f"Assignment {assignment_id} not found")

    assignment.status = new_status
    assignment.updated_at = utcnow()

    # If converted, mark payment as earned
    if new_status == "CONVERTED":
        payment = session.query(Payment).filter_by(lead_assignment_id=assignment_id).first()
        if payment:
            payment.payment_status = "EARNED"

    session.commit()
    return {
        "assignment_id": assignment_id,
        "lead_id": assignment.lead_id,
        "agent_id": assignment.agent_id,
        "status": new_status,
    }


def process_payment(session: Session, agent_id: int,
                    payment_ids: List[int] = None) -> Dict[str, object]:
    """Settle earned lead fees for an agent.

    Settles the named payments, or every EARNED fee for this agent when no ids
    are given. The amount is derived from the rows actually settled — a caller
    cannot assert an amount that the ledger does not support.
    """
    agent = session.query(Agent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent {agent_id} not found")

    if payment_ids:
        rows = session.query(Payment).filter(
            Payment.agent_id == agent_id,
            Payment.id.in_(payment_ids),
            Payment.payment_status == "EARNED",
        ).all()
    else:
        rows = session.query(Payment).filter_by(
            agent_id=agent_id, payment_status="EARNED"
        ).all()

    settled_now = 0.0
    for payment in rows:
        payment.payment_status = "PAID"
        payment.payment_date = utcnow()
        settled_now += float(payment.amount_inr or 0.0)

    # Update agent total earnings.
    # The session runs with autoflush=False, so the PAID writes above are still
    # only in memory; without this flush the aggregate re-reads stale rows and
    # reports earnings of zero right after a successful settlement.
    session.flush()

    from sqlalchemy import func

    total_paid = session.query(func.sum(Payment.amount_inr)).filter(
        Payment.agent_id == agent_id,
        Payment.payment_status == "PAID",
    ).scalar() or 0.0

    agent.total_earnings_inr = float(total_paid)
    session.commit()

    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "settled_count": len(rows),
        "amount_settled_inr": settled_now,
        "total_earnings_inr": agent.total_earnings_inr,
        "status": "payment_processed" if rows else "nothing_to_settle",
    }


def revenue_dashboard(session: Session) -> Dict[str, object]:
    """Real-time revenue model metrics."""
    from sqlalchemy import func

    # Agent stats
    total_agents = session.query(Agent).filter_by(status="ACTIVE").count()
    leads_sent = session.query(LeadAssignment).count()

    # Revenue by status
    pending_revenue = session.query(func.sum(Payment.amount_inr)).filter_by(
        payment_status="PENDING"
    ).scalar() or 0.0

    earned_revenue = session.query(func.sum(Payment.amount_inr)).filter_by(
        payment_status="EARNED"
    ).scalar() or 0.0

    paid_revenue = session.query(func.sum(Payment.amount_inr)).filter_by(
        payment_status="PAID"
    ).scalar() or 0.0

    # Conversion rates
    conversions = session.query(LeadAssignment).filter_by(status="CONVERTED").count()
    conversion_rate = conversions / leads_sent if leads_sent > 0 else 0.0

    return {
        "period": "all_time",
        "agents": {
            "active": total_agents,
        },
        "leads": {
            "sent": leads_sent,
            "converted": conversions,
            "conversion_rate": f"{conversion_rate*100:.1f}%",
        },
        "revenue_inr": {
            "pending": float(pending_revenue),
            "earned": float(earned_revenue),
            "paid": float(paid_revenue),
            "total": float(pending_revenue + earned_revenue + paid_revenue),
        },
        "model": "Premier Agent (charge professionals, not buyers/sellers)",
    }
