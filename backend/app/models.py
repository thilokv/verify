"""SQLAlchemy models — Pin 1 of the blueprint.

Corrected against the source draft (see blueprint §8.1):
  * datetime.utcnow() is deprecated from Python 3.12 and returns a naive
    value; replaced with an aware UTC helper and timezone-aware columns.
  * The embedding column adapts to the backend: a real pgvector Vector in
    production, JSON in the SQLite dev mode.
"""

import datetime as dt
import json
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import declarative_base, relationship

from .config import settings

Base = declarative_base()


def utcnow() -> dt.datetime:
    """Aware UTC timestamp. Never use datetime.utcnow()."""
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Make a value read back from the database safe to compare.

    SQLite has no native timestamp type, so DateTime(timezone=True) is stored
    as text and comes back NAIVE — subtracting it from an aware utcnow() raises
    TypeError. Postgres returns it aware. Anything doing datetime arithmetic in
    Python on a stored column must pass it through here first.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


class JSONVector(TypeDecorator):
    """Store a float vector as JSON text.

    Only used by the SQLite dev backend. Similarity is computed in Python
    (see search.py) because SQLite has no vector operators.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps([float(x) for x in value])

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)


def _embedding_column():
    """Vector(1536) under pgvector, JSON text under SQLite."""
    if settings.is_pgvector:
        from pgvector.sqlalchemy import Vector  # imported only when needed

        return Column(Vector(settings.EMBEDDING_DIM))
    return Column(JSONVector)


class Developer(Base):
    __tablename__ = "developers"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False, index=True)
    contact_person = Column(String)
    phone_number = Column(String, unique=True, index=True)
    email = Column(String, unique=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    properties = relationship("Property", back_populates="developer")
    leads = relationship("Lead", back_populates="developer")


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable: an owner can list directly, with no developer behind the stock.
    developer_id = Column(Integer, ForeignKey("developers.id"), nullable=True)

    title = Column(String, nullable=False)
    property_type = Column(String, index=True)  # Plot | Flat | Villa | Commercial
    location_name = Column(String, index=True)
    city = Column(String, index=True, default="Bengaluru")
    price_inr = Column(Float, index=True)
    area_sqft = Column(Float)
    config = Column(String)  # 2BHK, 3BHK, plot
    possession = Column(String)  # Ready to move | Under construction
    facing = Column(String)

    rera_id = Column(String, index=True)
    khata = Column(String)  # A-Khata | B-Khata | n/a

    # SALE | RENT. A property offered for rent is a different market from a
    # property offered for sale: it has no asking price, and it must never
    # surface in buyer search, which sells things. Search filters on this.
    listing_type = Column(String, default="SALE", index=True)

    # Verification is a first-class field, not a footnote. FR-5 gates search
    # on this being true.
    is_verified = Column(Boolean, default=False, index=True)
    verification_note = Column(Text)

    description = Column(Text)
    amenities = Column(JSON)  # ["Gated", "East facing", ...]

    # Seller self-declaration — see verification.py. Never a clearance.
    disclosures = Column(JSON, default=dict)
    documents_held = Column(JSON, default=list)
    risk_status = Column(String, default="NOT_STARTED", index=True)

    embedding = _embedding_column()
    created_at = Column(DateTime(timezone=True), default=utcnow)

    developer = relationship("Developer", back_populates="properties")
    documents = relationship("Document", back_populates="property")

    # ---- helpers -------------------------------------------------------

    def embedding_text(self) -> str:
        """The text that gets embedded. Keep this stable — changing it
        invalidates every stored vector."""
        parts: List[str] = [
            self.title or "",
            self.property_type or "",
            self.config or "",
            self.location_name or "",
            self.city or "",
            self.possession or "",
            self.facing or "",
            self.description or "",
        ]
        if self.amenities:
            parts.extend(str(a) for a in self.amenities)
        if self.price_inr:
            parts.append(f"{self.price_inr / 100000:.0f} lakh")
        return " ".join(p for p in parts if p)

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "property_type": self.property_type,
            "config": self.config,
            "location_name": self.location_name,
            "city": self.city,
            "price_inr": self.price_inr,
            "area_sqft": self.area_sqft,
            "possession": self.possession,
            "facing": self.facing,
            "rera_id": self.rera_id,
            "khata": self.khata,
            "listing_type": self.listing_type or "SALE",
            "is_verified": bool(self.is_verified),
            "verification_note": self.verification_note,
            "risk_status": self.risk_status,
            "description": self.description,
            "amenities": self.amenities or [],
        }


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    developer_id = Column(Integer, ForeignKey("developers.id"), nullable=True)

    buyer_name = Column(String)
    buyer_phone = Column(String, index=True)
    budget_max = Column(Float)
    preferred_location = Column(String)
    property_type = Column(String)
    timeline = Column(String)

    intent_score = Column(Integer, default=50)  # 0–100
    status = Column(String, default="NEW", index=True)
    # NEW | QUALIFIED | VISIT_SCHEDULED | CLOSED | UNMATCHED

    stage = Column(String, default="GREETING")  # WhatsApp state machine cursor
    chat_history = Column(JSON, default=list)
    notes = Column(Text)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    developer = relationship("Developer", back_populates="leads")
    site_visits = relationship("SiteVisit", back_populates="lead")


class SiteVisit(Base):
    __tablename__ = "site_visits"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)

    scheduled_for = Column(String)  # free text as captured from the buyer
    status = Column(String, default="REQUESTED")  # REQUESTED | CONFIRMED | DONE
    created_at = Column(DateTime(timezone=True), default=utcnow)

    lead = relationship("Lead", back_populates="site_visits")


class Asset(Base):
    """An uploaded photo or document. Bytes live on disk; this is the index."""

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    kind = Column(String)             # photo | document
    doc_code = Column(String)         # SALE_DEED, EC, KHATA ... for documents
    label = Column(String)            # sanitised original filename, display only
    stored_name = Column(String, nullable=False)   # random name on disk
    media_type = Column(String)
    bytes = Column(Integer)
    triage_status = Column(String)    # PASSED | PENDING | RED_FLAGGED (documents)

    # Chain of custody. A byte changed on disk no longer matches this.
    sha256 = Column(String, index=True)
    encrypted = Column(Boolean, default=False)
    pdf_signals = Column(JSON, default=dict)
    extracted_fields = Column(JSON, default=dict)
    extracted_text = Column(Text)

    created_at = Column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    """A buyer reporting a listing. Read by staff, never shown publicly."""

    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, index=True)
    reason = Column(String)
    detail = Column(Text)
    contact = Column(String)
    red_flags = Column(JSON, default=list)
    status = Column(String, default="OPEN", index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Append-only trail. Nothing in the app updates or deletes these rows."""

    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, index=True)
    action = Column(String, index=True)   # UPLOAD | VERIFY | DISCLOSE | DOWNLOAD
    actor = Column(String)                # "seller" | "staff"
    detail = Column(JSON, default=dict)
    sha256 = Column(String)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)

    document_type = Column(String)  # Sale Deed | Pahani | RERA | EC | Khata
    file_url = Column(String, nullable=False)
    verification_status = Column(String, default="PENDING")
    # PENDING | PASSED | RED_FLAGGED
    extracted_text = Column(Text)
    flagged_issues = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    property = relationship("Property", back_populates="documents")


class Agent(Base):
    """Real estate agent/broker registered for lead generation (Premier Agent model)."""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    company = Column(String)
    specialization = Column(String)  # residential|commercial|farmland|all
    service_areas = Column(JSON, default=list)  # ["Whitefield", "Sarjapur"]

    # Premier Agent pricing
    lead_fee_inr = Column(Float, default=500.0)  # fee per qualified lead sent
    commission_percent = Column(Float, default=0.5)  # % of transaction value (optional)

    # Compliance
    rera_id = Column(String, index=True)
    rera_state = Column(String)
    verified_broker = Column(Boolean, default=False)

    # Status
    status = Column(String, default="ACTIVE", index=True)  # ACTIVE|INACTIVE|SUSPENDED
    leads_received = Column(Integer, default=0)
    total_earnings_inr = Column(Float, default=0.0)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lead_assignments = relationship("LeadAssignment", back_populates="agent")
    payments = relationship("Payment", back_populates="agent")


class LeadAssignment(Base):
    """Track which leads were sent to which agents (lead generation revenue)."""

    __tablename__ = "lead_assignments"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=True)

    # Revenue tracking
    fee_charged_inr = Column(Float)
    status = Column(String, default="SENT", index=True)  # SENT|CONTACTED|INTERESTED|CONVERTED|ABANDONED

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lead = relationship("Lead")
    agent = relationship("Agent", back_populates="lead_assignments")


class Payment(Base):
    """Track payments from agents for leads (Premier Agent revenue)."""

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False, index=True)
    lead_assignment_id = Column(Integer, ForeignKey("lead_assignments.id"), nullable=True)

    amount_inr = Column(Float, nullable=False)
    # PENDING  fee raised when the lead was sent, not yet owed
    # EARNED   the lead converted, so the fee is now collectable
    # PAID     settled by the agent
    payment_status = Column(String, default="PENDING", index=True)  # PENDING|EARNED|PAID|FAILED|REFUNDED
    payment_date = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), default=utcnow)

    agent = relationship("Agent", back_populates="payments")


class BeachheadMarket(Base):
    """Current beachhead market for launch (one city + property type)."""

    __tablename__ = "beachhead_market"

    id = Column(Integer, primary_key=True, index=True)
    city = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False)
    property_types = Column(JSON, default=list)  # ["Flat", "Plot", "Villa"]

    status = Column(String, default="ACTIVE", index=True)  # PLANNING|ACTIVE|SCALED|ABANDONED

    # Validation progress (Section 11 of playbook)
    interviews_sellers = Column(Integer, default=0)
    interviews_buyers = Column(Integer, default=0)
    interviews_competitors = Column(Integer, default=0)
    target_interviews = Column(Integer, default=15)

    manual_pilot_listings = Column(Integer, default=0)
    target_listings = Column(Integer, default=50)

    manual_pilot_closed_deals = Column(Integer, default=0)
    target_deals = Column(Integer, default=5)

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Interview(Base):
    """Interview tracker (Section 11 validation checklist)."""

    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)

    # Interview type: seller, buyer, or competitor's-platform user
    interview_type = Column(String, nullable=False, index=True)

    name = Column(String)
    phone = Column(String)
    city = Column(String, index=True)

    # Key questions answered (boolean flags)
    validated_desire = Column(Boolean, default=False)  # Do they actually want this?
    identified_pain = Column(Boolean, default=False)   # What's their problem?
    alternative_used = Column(Boolean, default=False)  # Have they tried competitors?

    notes = Column(Text)
    key_insights = Column(JSON, default=list)

    created_at = Column(DateTime(timezone=True), default=utcnow)


class RERACompliance(Base):
    """RERA state-level registration and compliance status (Section 7.5)."""

    __tablename__ = "rera_compliance"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String, nullable=False, unique=True, index=True)

    # Registration status
    platform_registered = Column(Boolean, default=False)
    registration_number = Column(String)
    registration_date = Column(DateTime(timezone=True))

    # Requirements
    requires_agent_registration = Column(Boolean, default=True)
    requires_project_registration = Column(Boolean, default=True)
    requires_buyer_agreement = Column(Boolean, default=True)

    # Authority contact
    authority_name = Column(String)
    authority_email = Column(String)
    authority_website = Column(String)

    compliance_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class RentalListing(Base):
    """Post-sale feature: rental management (Phase 4)."""

    __tablename__ = "rental_listings"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    monthly_rent_inr = Column(Float, nullable=False)
    # The deposit is the tenant's real exposure and the thing the Model Tenancy
    # Act caps, so it is stored rather than only checked at intake — the tenant
    # brief cannot warn about a number that was thrown away.
    deposit_inr = Column(Float)
    lease_terms = Column(String)  # e.g., "11 months", "2 years"
    furnishing = Column(String)  # Furnished|Semi-furnished|Unfurnished

    available_from = Column(DateTime(timezone=True))
    status = Column(String, default="ACTIVE", index=True)  # ACTIVE|LEASED|PAUSED

    # Tenant info (if leased)
    tenant_name = Column(String)
    tenant_phone = Column(String)
    lease_start = Column(DateTime(timezone=True))
    lease_end = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), default=utcnow)


class FarmlandGuidance(Base):
    """Post-sale feature: seasonal crop guidance for farmland (Phase 4)."""

    __tablename__ = "farmland_guidance"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    soil_type = Column(String)  # Black soil, Red soil, Laterite, etc.
    region = Column(String)  # State/district for regional guidance
    size_acres = Column(Float)

    current_season = Column(String)  # Kharif, Rabi, Summer
    recommended_crops = Column(JSON, default=list)  # [{"crop": "sugarcane", "yield_per_acre": 65}]

    irrigation_type = Column(String)  # Rainfall, Well, Bore, Drip, etc.
    water_availability = Column(String)  # Scarce, Moderate, Abundant

    last_guidance_sent = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Consent(Base):
    """Permission to contact someone on a channel.

    The agent sends nothing without a row here. DPDP Act 2023 requires the
    purpose to be stated and withdrawal to be as easy as granting — so consent
    is per channel, carries its purpose, and revocation is a timestamp rather
    than a delete, because we must be able to prove when it was withdrawn.
    """

    __tablename__ = "consents"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False, index=True)   # phone or email
    channel = Column(String, nullable=False, index=True)   # whatsapp|email|inapp
    purpose = Column(String, nullable=False)

    granted = Column(Boolean, default=True, index=True)
    granted_at = Column(DateTime(timezone=True), default=utcnow)
    revoked_at = Column(DateTime(timezone=True))

    # Local time, IST. Nobody wants a property alert at 03:00.
    quiet_from_hour = Column(Integer, default=21)
    quiet_to_hour = Column(Integer, default=8)
    max_per_day = Column(Integer, default=6)

    created_at = Column(DateTime(timezone=True), default=utcnow)


class Watch(Base):
    """A standing instruction the agent evaluates on its own.

    This is the whole point of the agent: a person states what would matter to
    them once, and the platform keeps looking after they have closed the tab.
    """

    __tablename__ = "watches"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String, nullable=False, index=True)
    channel = Column(String, default="inapp", index=True)

    # NEW_MATCH | PRICE_DROP | VERIFICATION_CHANGE | DEPOSIT_RISK | LEAD_IDLE
    kind = Column(String, nullable=False, index=True)
    label = Column(String)
    criteria = Column(JSON, default=dict)

    # Snapshot of what the world looked like last tick, so a change can be
    # detected without re-notifying about a state that has not moved.
    seen_state = Column(JSON, default=dict)

    status = Column(String, default="ACTIVE", index=True)   # ACTIVE|PAUSED|REVOKED
    last_checked_at = Column(DateTime(timezone=True))
    fired_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """One message the agent decided to send, and what happened to it."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    watch_id = Column(Integer, ForeignKey("watches.id"), index=True)
    subject = Column(String, index=True)
    channel = Column(String)

    title = Column(String)
    body = Column(Text)
    severity = Column(String, default="info")     # info | warning | urgent
    property_id = Column(Integer, index=True)

    # Identity of the underlying event. Unique, so the same event can never be
    # delivered twice however many times the agent ticks.
    fingerprint = Column(String, unique=True, index=True)

    # QUEUED | SENT | FAILED | SUPPRESSED
    status = Column(String, default="QUEUED", index=True)
    suppressed_reason = Column(String)
    read_at = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


class AgentRun(Base):
    """One tick of the agent loop. Its own audit trail."""

    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at = Column(DateTime(timezone=True))
    watches_checked = Column(Integer, default=0)
    events_found = Column(Integer, default=0)
    notifications_sent = Column(Integer, default=0)
    notifications_suppressed = Column(Integer, default=0)
    error = Column(Text)
