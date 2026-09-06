"""FastAPI application — the four backend pins wired together.

Corrected against the source draft (blueprint §8.1): startup work runs in a
lifespan handler, not the deprecated @app.on_event("startup").
"""

import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Query,
                     Request, Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import documents as docs
from . import whatsapp_agent as wa
from . import seller_agent as seller
from . import rental_agent as ultron
from . import buyer_safety
from . import tenant_safety
from . import affordability as afford
from . import cities as cities_mod
from . import compare as cmp_
from . import conveyance as conv
from . import valuation
from . import renovation
from . import watchtower
from . import consent
from . import evidence
from . import ratelimit
from . import uploads
from . import vault
from . import verification
from . import vision
from . import whatsapp_send as wa_send
from . import playbook as pb
from . import revenue as rev
from . import post_sale as ps
from .auth import bootstrap_keys, require_staff
from .config import settings
from .db import get_db, init_db
from .embeddings import embed_one
from .models import (Asset, AuditEvent, Document, Lead, Property,
                     Report, SiteVisit)
from .schemas import (ChatIn, DisclosureIn, DocumentCheck, MessageScan,
                      PropertyIn, ReportIn, SearchQuery, SearchResponse,
                      SellerTurn)
from .search import reindex, search

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
UPLOAD_ROOT = os.getenv(
    "UPLOAD_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_keys()
    yield


app = FastAPI(
    title="AI Proptech Platform",
    version="1.0.0",
    description="Intent search, title triage and WhatsApp lead qualification.",
    lifespan=lifespan,
)

@app.middleware("http")
async def throttle(request: Request, call_next):
    hit = ratelimit.check(ratelimit.client_key(request), request.url.path)
    if hit:
        return JSONResponse(
            status_code=429,
            content={"detail": (
                f"Too many requests. Limit is {hit['limit']} per "
                f"{hit['window'] // 60} minutes. Try again in "
                f"{hit['retry_after']}s.")},
            headers={"Retry-After": str(hit["retry_after"])},
        )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ meta

@app.get("/api/v1/health")
def health() -> Dict[str, Any]:
    cfg = settings.describe()
    cfg["status"] = "ok"
    cfg["whatsapp_outbound"] = "live" if wa_send.is_live() else "dry_run"
    if not cfg["auth_required"]:
        cfg["auth_warning"] = (
            "REQUIRE_AUTH=false — write endpoints and buyer contact data are open."
        )
    if not cfg["semantic_search"]:
        cfg["warning"] = (
            "Embedding provider is 'hash' — matching is lexical, not semantic. "
            "Set EMBEDDING_PROVIDER=openai (or voyage) before showing this to buyers."
        )
    return cfg


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"docs": "/docs", "health": "/api/v1/health"}


# ------------------------------------------------- Pin 2: buyer intent search

@app.post("/api/v1/search", response_model=SearchResponse)
def search_properties(q: SearchQuery, db: Session = Depends(get_db)):
    try:
        return search(
            db,
            prompt=q.user_prompt,
            limit=q.limit,
            max_price=q.max_price,
            property_type=q.property_type,
        )
    except RuntimeError as exc:          # misconfiguration — actionable
        raise HTTPException(status_code=503, detail=str(exc))


# ------------------------------------------------------------ inventory

@app.get("/api/v1/properties")
def list_properties(
    db: Session = Depends(get_db),
    verified_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    stmt = select(Property)
    if verified_only:
        stmt = stmt.where(Property.is_verified.is_(True))
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return {"count": len(rows), "properties": [r.to_public() for r in rows]}


@app.post("/api/v1/properties", status_code=201)
def create_property(body: PropertyIn, db: Session = Depends(get_db),
                    _staff: str = Depends(require_staff)):
    prop = Property(**body.model_dump())
    # New stock is never trusted on entry. It becomes visible to buyers only
    # after the title checks pass. Blueprint §2.1.
    prop.is_verified = False
    prop.verification_note = (
        "Encumbrance certificate and khata extract not yet pulled; no advocate "
        "opinion on file."
    )
    db.add(prop)
    db.flush()
    prop.embedding = embed_one(prop.embedding_text())
    db.commit()
    db.refresh(prop)
    return prop.to_public()


@app.post("/api/v1/properties/{property_id}/verify")
def verify_property(property_id: int, note: Optional[str] = None,
                    db: Session = Depends(get_db),
                    _staff: str = Depends(require_staff)):
    """Mark a listing verified — the advocate sign-off step. Staff only."""
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")
    prop.is_verified = True
    prop.verification_note = note or "Advocate title opinion on file."
    db.commit()
    return prop.to_public()


@app.post("/api/v1/reindex")
def reindex_all(db: Session = Depends(get_db), _staff: str = Depends(require_staff)):
    """Recompute every embedding. Required after changing provider — vectors
    from different providers are not comparable."""
    return {"reindexed": reindex(db)}


# ------------------------------------------- Pin 4: document / title triage

@app.post("/api/v1/documents/check")
def check_document(body: DocumentCheck):
    return docs.verify_document(
        body.text,
        expected_owner=body.expected_owner,
        expected_survey=body.expected_survey,
        expected_rera=body.expected_rera,
    )


@app.post("/api/v1/properties/{property_id}/documents")
def attach_document(property_id: int, body: DocumentCheck,
                    db: Session = Depends(get_db),
                    _staff: str = Depends(require_staff)):
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")

    result = docs.verify_document(
        body.text,
        expected_owner=body.expected_owner,
        expected_survey=body.expected_survey,
        expected_rera=prop.rera_id,
    )
    doc = Document(
        property_id=property_id,
        document_type=result["document_type"],
        file_url="inline:text",
        verification_status=result["verdict"],
        extracted_text=body.text[:20000],
        flagged_issues=result["issues"],
    )
    db.add(doc)
    db.commit()
    return {"document_id": doc.id, **result}


# ------------------------------------------------- Pin 3: WhatsApp agent

@app.get("/api/v1/whatsapp/webhook")
def whatsapp_verify(request: Request):
    """Meta's subscription handshake."""
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


def _valid_signature(raw: bytes, header: Optional[str]) -> bool:
    """Meta signs each webhook with the app secret. Unsigned payloads are
    forged until proven otherwise."""
    if not settings.WHATSAPP_APP_SECRET:
        return True  # dev mode: no secret configured
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@app.post("/api/v1/whatsapp/webhook")
async def whatsapp_inbound(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not _valid_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403, "Bad signature")

    payload = await request.json()
    handled: List[Dict[str, Any]] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in (change.get("value", {}) or {}).get("messages", []) or []:
                if msg.get("type") != "text":
                    continue
                phone = msg.get("from", "")
                text = (msg.get("text", {}) or {}).get("body", "")
                if not (phone and text):
                    continue

                # STOP is honoured before anything else reads the message.
                # It must not queue behind qualification logic, and it must
                # not depend on the conversational agent understanding it.
                opt = consent.handle_keyword(db, phone, text, "whatsapp")
                if opt is not None:
                    opt["delivery"] = wa_send.send_text(phone, opt["reply"])
                    handled.append(opt)
                    continue

                result = wa.handle_message(db, phone, text)
                # Best-effort: a send failure must not fail the webhook, or
                # Meta retries and the buyer gets the turn twice.
                result["delivery"] = wa_send.send_text(phone, result["reply"])
                handled.append(result)

    # Meta requires a fast 200 regardless; replies are sent via the Graph API.
    return {"handled": len(handled), "results": handled}


@app.post("/api/v1/chat")
def chat(body: ChatIn, db: Session = Depends(get_db)):
    """Local harness for the same agent, without Meta in the loop.

    Runs the same opt-out check as the webhook, so STOP can be exercised
    without a Meta account — and so the two paths cannot drift apart.
    """
    opt = consent.handle_keyword(db, body.phone, body.text, "whatsapp")
    if opt is not None:
        return opt
    return wa.handle_message(db, body.phone, body.text)


# --------------------------------------------------- seller intake (Jarvis)

@app.post("/api/v1/seller/chat")
def seller_chat(body: SellerTurn, db: Session = Depends(get_db)):
    """Conversational listing intake. State lives with the client, so an
    abandoned half-finished listing leaves nothing behind on the server."""
    return seller.handle(db, {"stage": body.stage, "draft": body.draft}, body.text)


@app.post("/api/v1/listings/submit", status_code=201)
def submit_listing(body: PropertyIn, db: Session = Depends(get_db)):
    """Public seller submission — the structured form alternative to Jarvis.

    Not staff-gated: a seller listing their own property is a public action.
    It lands unverified like every other route into the catalogue, so an open
    endpoint cannot put unchecked stock in front of a buyer.
    """
    prop = Property(**body.model_dump())
    prop.is_verified = False
    prop.risk_status = "NOT_SUBMITTED"
    prop.verification_note = (
        "Submitted by seller. No documents received; no advocate opinion on file."
    )
    db.add(prop)
    db.flush()
    prop.embedding = embed_one(prop.embedding_text())
    db.add(AuditEvent(property_id=prop.id, action="SUBMIT", actor="seller",
                      detail={"title": prop.title, "via": "form"}))
    db.commit()
    db.refresh(prop)
    return prop.to_public()


# ------------------------------------------ seller verification (anti-fraud)

@app.get("/api/v1/verification/checklist")
def verification_checklist(property_type: Optional[str] = Query(None)):
    """The documents and disclosure questions for this property type."""
    return {
        "property_type": property_type,
        "documents": verification.required_documents(property_type),
        "questions": verification.questionnaire(),
        "notice": (
            "We ask which documents you hold — never the numbers on them. "
            "Do not enter Aadhaar, PAN or account numbers anywhere in this "
            "application."
        ),
    }


@app.post("/api/v1/properties/{property_id}/disclosures")
def submit_disclosures(property_id: int, body: DisclosureIn,
                       db: Session = Depends(get_db)):
    """Record the seller's self-declaration and score it."""
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")

    result = verification.assess(
        prop.property_type, body.answers, body.documents_held)

    prop.disclosures = body.answers
    prop.documents_held = body.documents_held
    prop.risk_status = result["status"]
    # A declaration never verifies anything. Only an advocate flips is_verified.
    db.commit()

    return {"property_id": property_id, **result}


@app.get("/api/v1/properties/{property_id}/verification")
def get_verification(property_id: int, db: Session = Depends(get_db)):
    """Evidence-based status.

    Two independent tracks, both of which must be satisfied:
      declaration  what the seller says about ownership
      evidence     what the uploaded documents actually show

    Neither can flip is_verified. Only an advocate does that.
    """
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")

    assets = db.execute(
        select(Asset).where(Asset.property_id == property_id)
    ).scalars().all()
    documents = [a for a in assets if a.kind == "document"]

    # Which of these files also appear against some other property?
    dupe_map: Dict[str, List[int]] = {}
    for a in documents:
        if not a.sha256:
            continue
        others = db.execute(
            select(Asset.property_id).where(
                Asset.sha256 == a.sha256, Asset.property_id != property_id)
        ).scalars().all()
        if others:
            dupe_map[a.sha256] = sorted(set(others))

    required = verification.required_documents(prop.property_type)
    ev = evidence.assess_documents(
        required,
        [{"code": a.doc_code or "", "label": a.label, "sha256": a.sha256,
          "bytes": a.bytes, "media_type": a.media_type,
          "text": a.extracted_text, "pdf": a.pdf_signals}
         for a in documents],
        duplicate_hits=dupe_map,
    )

    decl = verification.assess(
        prop.property_type, prop.disclosures or {}, ev["submitted_codes"])

    if prop.is_verified:
        level = "ADVOCATE_VERIFIED"
    elif decl["high_count"] and ev["level"] in {"MACHINE_CHECKED", "SUBMITTED"}:
        level = "FLAGGED"
    else:
        level = ev["level"]

    prop.risk_status = level
    db.commit()

    return {
        "property_id": property_id,
        "is_verified": bool(prop.is_verified),
        "level": level,
        "level_label": dict(evidence.LEVELS)[level],
        "evidence": ev,
        "declaration": decl,
        # kept flat for the existing UI
        "status": decl["status"],
        "headline": decl["headline"],
        "documents_required": required,
        "documents_held": ev["submitted_codes"],
        "documents_missing": ev["missing_codes"],
        "unanswered": decl["unanswered"],
        "findings": ev["findings"] + decl["findings"],
        "high_count": ev["high_count"] + decl["high_count"],
        "disclaimer": decl["disclaimer"],
        "uploads": [
            {"id": a.id, "kind": a.kind, "doc_code": a.doc_code,
             "label": a.label, "media_type": a.media_type, "bytes": a.bytes,
             "sha256": (a.sha256 or "")[:16], "encrypted": bool(a.encrypted),
             "signed": bool((a.pdf_signals or {}).get("digitally_signed")),
             "text_read": bool(a.extracted_text),
             "triage_status": a.triage_status}
            for a in assets
        ],
    }


@app.get("/api/v1/properties/{property_id}/audit")
def property_audit(property_id: int, db: Session = Depends(get_db),
                   _staff: str = Depends(require_staff)):
    """Append-only trail of everything that happened to this listing."""
    rows = db.execute(
        select(AuditEvent).where(AuditEvent.property_id == property_id)
        .order_by(AuditEvent.created_at.desc()).limit(200)
    ).scalars().all()
    return {"count": len(rows), "events": [
        {"id": e.id, "action": e.action, "actor": e.actor,
         "sha256": (e.sha256 or "")[:16], "detail": e.detail,
         "at": e.created_at.isoformat() if e.created_at else None}
        for e in rows]}


# ------------------------------------------------------------------ uploads

@app.post("/api/v1/properties/{property_id}/uploads", status_code=201)
async def upload_asset(
    property_id: int,
    file: UploadFile = File(...),
    kind: str = Form("photo"),
    doc_code: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Seller uploads a property photo or a title document."""
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")

    data = await file.read()
    sha = evidence.digest(data)

    # Re-use of one scan across properties is the signature of a fraud ring.
    dupes = db.execute(
        select(Asset).where(Asset.sha256 == sha,
                            Asset.property_id != property_id)
    ).scalars().all()

    try:
        stored = uploads.store(UPLOAD_ROOT, property_id, kind,
                               file.filename or "", data,
                               seal=vault.seal)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    pdf = evidence.pdf_signals(data)
    text = evidence.read_text(data, stored["media_type"])
    fields = evidence.extract_fields(text) if text else {}
    triage = evidence.triage_text(text) if kind == "document" else None

    asset = Asset(
        property_id=property_id,
        kind=stored["kind"],
        doc_code=(doc_code or "").upper() or None,
        label=stored["label"],
        stored_name=stored["stored_name"],
        media_type=stored["media_type"],
        bytes=stored["bytes"],
        sha256=sha,
        encrypted=bool(stored.get("encrypted")),
        pdf_signals=pdf,
        extracted_fields=fields,
        extracted_text=(text or "")[:20000] or None,
        triage_status=(triage or {}).get("verdict"),
    )
    db.add(asset)

    if kind == "document" and doc_code:
        held = list(prop.documents_held or [])
        if doc_code.upper() not in held:
            held.append(doc_code.upper())
            prop.documents_held = held

    db.add(AuditEvent(
        property_id=property_id, action="UPLOAD", actor="seller", sha256=sha,
        detail={"kind": kind, "doc_code": (doc_code or "").upper() or None,
                "label": stored["label"], "bytes": stored["bytes"],
                "encrypted": bool(stored.get("encrypted")),
                "duplicate_of": [d.property_id for d in dupes]},
    ))
    db.commit()
    db.refresh(asset)

    warnings = []
    if dupes:
        warnings.append(
            "This exact file is already on file against property "
            + ", ".join("#" + str(d.property_id) for d in dupes)
            + ". Flagged for review.")
    if triage and triage.get("verdict") == "RED_FLAGGED":
        warnings.append("Automated checks flagged a problem in this document.")

    return {
        "id": asset.id, "kind": asset.kind, "doc_code": asset.doc_code,
        "label": asset.label, "media_type": asset.media_type,
        "bytes": asset.bytes,
        "sha256": sha,
        "encrypted": bool(stored.get("encrypted")),
        "text_read": bool(text),
        "extracted": fields,
        "warnings": warnings,
        "note": ("Received and sealed. Nothing is verified by uploading — an "
                 "advocate must examine the original."
                 if kind == "document" else "Photo stored."),
    }


@app.get("/api/v1/properties/{property_id}/uploads/{asset_id}")
def fetch_asset(property_id: int, asset_id: int,
                db: Session = Depends(get_db),
                _staff: str = Depends(require_staff)):
    """Staff-only: uploaded documents can contain a seller's personal data.

    Served as an attachment with a fixed type so nothing uploaded here can
    execute in the reviewer's browser.
    """
    asset = db.get(Asset, asset_id)
    if not asset or asset.property_id != property_id:
        raise HTTPException(404, "No such file")
    try:
        path = uploads.resolve(UPLOAD_ROOT, property_id, asset.stored_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with open(path, "rb") as fh:
        blob = fh.read()
    try:
        data = vault.unseal(blob)
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    # Chain of custody: the bytes must still hash to what we recorded.
    if asset.sha256 and evidence.digest(data) != asset.sha256:
        raise HTTPException(409, (
            "Integrity check FAILED — the stored file no longer matches the "
            "digest recorded at upload. Do not rely on this copy."))

    db.add(AuditEvent(property_id=property_id, action="DOWNLOAD", actor="staff",
                      sha256=asset.sha256, detail={"asset_id": asset.id}))
    db.commit()

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{asset.label or asset.stored_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------ buyer safety

@app.get("/api/v1/properties/{property_id}/safety")
def buyer_brief(property_id: int, db: Session = Depends(get_db)):
    """What a buyer should know before contacting the seller or paying."""
    prop = db.get(Property, property_id)
    if not prop:
        raise HTTPException(404, "No such property")
    state = get_verification(property_id, db)
    return buyer_safety.brief(prop.to_public(), state)


@app.get("/api/v1/safety/rules")
def safety_rules():
    """The rules a buyer should never break, and the site-visit checklist."""
    return {
        "golden_rules": [{"code": c, "rule": r, "why": w}
                         for c, r, w in buyer_safety.GOLDEN_RULES],
        "visit_checklist": buyer_safety.VISIT_CHECKLIST,
        "disclaimer": (
            "This platform never asks a buyer to pay it and never holds a "
            "buyer's money. Anyone claiming to collect a fee on our behalf is "
            "committing fraud."),
    }


@app.post("/api/v1/safety/scan")
def scan_message(body: MessageScan):
    """Check what a seller or agent told the buyer for known pressure tactics."""
    hits = buyer_safety.scan_message(body.text)
    return {
        "flags": hits,
        "verdict": "PRESSURE_DETECTED" if hits else "NOTHING_DETECTED",
        "advice": (
            "These phrases are the standard preamble to advance-fee fraud. "
            "Slow down, involve an advocate, and pay nothing."
            if hits else
            "Nothing recognised — this is a keyword check, not proof of safety."),
    }


@app.post("/api/v1/report", status_code=201)
def report_listing(body: ReportIn, db: Session = Depends(get_db)):
    """Buyer reports a suspicious listing or approach."""
    flags = buyer_safety.scan_message(body.detail)
    rep = Report(
        property_id=body.property_id,
        reason=body.reason,
        detail=body.detail,
        contact=body.contact,
        red_flags=flags,
    )
    db.add(rep)
    db.add(AuditEvent(property_id=body.property_id, action="REPORT",
                      actor="buyer", detail={"reason": body.reason}))
    db.commit()
    db.refresh(rep)
    return {"id": rep.id, "status": rep.status, "red_flags": flags,
            "message": "Reported. Our team reviews every report."}


@app.get("/api/v1/reports")
def list_reports(db: Session = Depends(get_db),
                 _staff: str = Depends(require_staff)):
    rows = db.execute(select(Report).order_by(Report.created_at.desc())
                      .limit(100)).scalars().all()
    return {"count": len(rows), "reports": [
        {"id": r.id, "property_id": r.property_id, "reason": r.reason,
         "detail": r.detail, "contact": r.contact, "red_flags": r.red_flags,
         "status": r.status} for r in rows]}


# ------------------------------------------------------ floor-plan matching

@app.get("/api/v1/plan/status")
def plan_status():
    return {"available": vision.available(),
            "reason": None if vision.available() else vision.unavailable_reason()}


@app.post("/api/v1/plan/match")
async def plan_match(
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    limit: int = Form(3),
    db: Session = Depends(get_db),
):
    """Read an uploaded floor plan, then search the catalogue against it."""
    data = await file.read()
    try:
        reading = vision.read_plan(data, file.content_type or "", note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not reading.get("available"):
        return {"reading": reading, "results": None}
    if reading.get("error") or reading.get("is_floor_plan") is False:
        return {"reading": reading, "results": None}

    brief = vision.brief_from(reading, note)
    return {"reading": reading, "brief": brief,
            "results": search(db, brief, limit=max(1, min(limit, 10)))}


# ------------------------------------------------------------------ CRM

@app.get("/api/v1/leads")
def list_leads(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200),
               _staff: str = Depends(require_staff)):
    """Buyer PII. Staff only — DPDP Act 2023 obligations attach to these rows."""
    rows = db.execute(
        select(Lead).order_by(Lead.updated_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "count": len(rows),
        "leads": [
            {
                "id": r.id,
                "phone": r.buyer_phone,
                "budget_max": r.budget_max,
                "preferred_location": r.preferred_location,
                "property_type": r.property_type,
                "timeline": r.timeline,
                "intent_score": r.intent_score,
                "status": r.status,
                "stage": r.stage,
                "turns": len(r.chat_history or []),
            }
            for r in rows
        ],
    }


@app.get("/api/v1/site-visits")
def list_visits(db: Session = Depends(get_db), _staff: str = Depends(require_staff)):
    rows = db.execute(select(SiteVisit)).scalars().all()
    return {
        "count": len(rows),
        "visits": [
            {
                "id": v.id,
                "lead_id": v.lead_id,
                "scheduled_for": v.scheduled_for,
                "status": v.status,
            }
            for v in rows
        ],
    }


# ---- PLAYBOOK EXECUTION (Go-to-Market & Validation) ----

@app.post("/api/v1/playbook/beachhead")
def init_beachhead(city: str = Query(...), state: str = Query(...),
                   property_types: List[str] = Query([]),
                   db: Session = Depends(get_db),
                   _staff: str = Depends(require_staff)):
    """Initialize or update beachhead market (Section 11 of playbook)."""
    return pb.init_beachhead(db, city, state, property_types or ["Flat", "Plot"])


@app.get("/api/v1/playbook/beachhead")
def get_beachhead_status(db: Session = Depends(get_db)):
    """Get current beachhead market and validation progress."""
    from sqlalchemy import select
    from .models import BeachheadMarket
    market = db.execute(select(BeachheadMarket).limit(1)).scalar()
    if not market:
        return {"status": "not_configured", "next_step": "POST /playbook/beachhead to set city"}
    return pb.existing_to_dict(market)


@app.post("/api/v1/interviews")
def log_interview(interview_type: str = Query(...), name: str = Query(...),
                 phone: str = Query(...), city: str = Query(...),
                 validated_desire: bool = Query(False),
                 identified_pain: bool = Query(False),
                 alternative_used: bool = Query(False),
                 notes: str = Query(""),
                 db: Session = Depends(get_db),
                 _staff: str = Depends(require_staff)):
    """Log a validation interview (Section 11 checklist)."""
    return pb.log_interview(db, interview_type, name, phone, city,
                           validated_desire, identified_pain, alternative_used, notes)


# --------------------------------------------------------------- the agent

@app.get("/api/v1/agent/status")
def agent_status(db: Session = Depends(get_db)):
    """Is the watch agent alive, and what has it been doing?"""
    return watchtower.agent_status(db)


@app.post("/api/v1/agent/tick")
def agent_tick(db: Session = Depends(get_db), _staff: str = Depends(require_staff)):
    """Run one pass by hand. The daemon does this on a loop; this is for
    testing and for a cron that would rather curl than run a process."""
    return watchtower.tick(db)


@app.post("/api/v1/watches", status_code=201)
def create_watch(subject: str = Query(...), kind: str = Query(...),
                 channel: str = Query("inapp"), label: str = Query(""),
                 property_id: Optional[int] = Query(None),
                 brief: Optional[str] = Query(None),
                 locality: Optional[str] = Query(None),
                 min_drop_percent: float = Query(0),
                 idle_hours: int = Query(48),
                 db: Session = Depends(get_db)):
    """Ask the agent to keep looking at something after you close the tab."""
    criteria: Dict[str, Any] = {}
    if property_id is not None:
        criteria["property_id"] = property_id
    if brief:
        criteria["brief"] = brief
    if locality:
        criteria["locality"] = locality
    if min_drop_percent:
        criteria["min_drop_percent"] = min_drop_percent
    if kind == "LEAD_IDLE":
        criteria["idle_hours"] = idle_hours
    try:
        watch = watchtower.create_watch(db, subject, kind, criteria, channel, label)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return watchtower.watch_to_dict(watch)


@app.get("/api/v1/watches")
def list_watches(subject: str = Query(...), db: Session = Depends(get_db)):
    from .models import Watch
    rows = db.execute(select(Watch).filter_by(subject=subject)).scalars().all()
    return {"count": len(rows),
            "watches": [watchtower.watch_to_dict(w) for w in rows]}


@app.delete("/api/v1/watches/{watch_id}")
def stop_watch(watch_id: int, db: Session = Depends(get_db)):
    from .models import Watch
    watch = db.get(Watch, watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="No such watch.")
    watch.status = "REVOKED"
    db.commit()
    return {"id": watch_id, "status": "REVOKED"}


@app.get("/api/v1/notifications")
def list_notifications(subject: str = Query(...), limit: int = Query(30, ge=1, le=200),
                       db: Session = Depends(get_db)):
    """What the agent has for this person, newest first."""
    from .models import Notification
    rows = db.execute(
        select(Notification)
        .filter(Notification.subject == subject,
                Notification.status.in_(("SENT", "QUEUED")))
        .order_by(Notification.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "count": len(rows),
        "unread": sum(1 for r in rows if r.read_at is None),
        "notifications": [watchtower.note_to_dict(n) for n in rows],
    }


@app.post("/api/v1/notifications/{note_id}/read")
def mark_read(note_id: int, db: Session = Depends(get_db)):
    from .models import Notification
    note = db.get(Notification, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="No such notification.")
    note.read_at = watchtower.utcnow()
    db.commit()
    return {"id": note_id, "read": True}


# ---- permissions ----

@app.post("/api/v1/consent/grant")
def consent_grant(subject: str = Query(...), channel: str = Query(...),
                  purpose: str = Query("Property alerts you asked for"),
                  quiet_from_hour: int = Query(21, ge=0, le=23),
                  quiet_to_hour: int = Query(8, ge=0, le=23),
                  max_per_day: int = Query(6, ge=1, le=50),
                  db: Session = Depends(get_db)):
    """Permit the agent to contact you on a channel."""
    try:
        row = consent.grant(db, subject, channel, purpose,
                            quiet_from_hour, quiet_to_hour, max_per_day)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"subject": row.subject, "channel": row.channel,
            "granted": True, "purpose": row.purpose}


@app.post("/api/v1/consent/revoke")
def consent_revoke(subject: str = Query(...),
                   channel: Optional[str] = Query(None),
                   db: Session = Depends(get_db)):
    """Withdraw it. Takes effect on the next evaluation, not eventually."""
    n = consent.revoke(db, subject, channel)
    return {"subject": subject, "revoked_channels": n}


@app.get("/api/v1/consent/status")
def consent_status(subject: str = Query(...), db: Session = Depends(get_db)):
    return consent.status(db, subject)


@app.get("/api/v1/properties/{property_id}/valuation")
def property_valuation(property_id: int, db: Session = Depends(get_db)):
    """Comps-anchored estimate with the working shown, plus a sunlight profile."""
    try:
        return valuation.value(db, property_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/properties/{property_id}/affordability")
def property_affordability(property_id: int,
                           monthly_income_inr: Optional[float] = Query(None, gt=0),
                           existing_emi_inr: float = Query(0.0, ge=0),
                           annual_rate_percent: float = Query(8.6, gt=0, le=30),
                           tenure_years: int = Query(20, ge=1, le=35),
                           city: Optional[str] = Query(None, max_length=40),
                           woman_purchaser: bool = Query(False),
                           db: Session = Depends(get_db)):
    """True cost of acquisition, and whether a bank will fund this.

    `city` prices the same property under another state's duty regime — the
    spread is close to double between the cheapest and dearest, so it is worth
    being able to ask.
    """
    try:
        return afford.affordability(db, property_id, monthly_income_inr,
                                    existing_emi_inr, annual_rate_percent / 100,
                                    tenure_years, city, woman_purchaser)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/properties/{property_id}/yield")
def property_yield(property_id: int, db: Session = Depends(get_db)):
    """Rental yield, computed on the all-in cost rather than the sticker price."""
    try:
        return afford.rental_yield(db, property_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/affordability/quote")
def affordability_quote(price_inr: float = Query(..., gt=0),
                        monthly_income_inr: Optional[float] = Query(None, gt=0),
                        possession: str = Query(""),
                        khata: str = Query(""),
                        existing_emi_inr: float = Query(0.0, ge=0),
                        annual_rate_percent: float = Query(8.6, gt=0, le=30),
                        tenure_years: int = Query(20, ge=1, le=35)):
    """The same maths for a price the buyer types, with no listing behind it."""
    try:
        out = {"acquisition": afford.acquisition_cost(price_inr, possession),
               "loan": None, "rates_as_of": afford.RATES_AS_OF}
        if monthly_income_inr:
            out["loan"] = afford.loan_eligibility(
                price_inr, monthly_income_inr, existing_emi_inr,
                annual_rate_percent / 100, tenure_years, khata, possession)
            cash = out["loan"]["down_payment_inr"] + out["acquisition"]["extras_inr"]
            out["cash_needed_inr"] = cash
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/compare")
def compare_properties(ids: List[int] = Query(..., min_length=2),
                       monthly_income_inr: Optional[float] = Query(None, gt=0),
                       db: Session = Depends(get_db)):
    """Line properties up on what actually costs money."""
    try:
        return cmp_.compare(db, ids, monthly_income_inr)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/conveyance")
def conveyance_timeline(property_id: Optional[int] = Query(None),
                        price_inr: Optional[float] = Query(None, gt=0),
                        db: Session = Depends(get_db)):
    """The stage-by-stage purchase sequence, with the money attached."""
    try:
        return conv.timeline(db, property_id, price_inr)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/cities")
def list_cities():
    """What a buyer pays, and what document they must demand, city by city."""
    return cities_mod.summary()


@app.get("/api/v1/sunlight")
def sunlight(facing: str = Query("", max_length=20),
             city: str = Query("", max_length=40)):
    """Annual direct-sun exposure for a facade, at this city's latitude."""
    return valuation.sunlight_profile(facing or None,
                                      cities_mod.get(city or None)["latitude"])


@app.post("/api/v1/renovation/estimate")
def renovation_estimate(scopes: List[str] = Query(...),
                        area_sqft: float = Query(..., gt=0),
                        grade: str = Query("standard")):
    """Cost a renovation, and say what actually comes back at resale."""
    try:
        return renovation.estimate(scopes, area_sqft, grade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/renovation/scopes")
def renovation_scopes():
    """The scopes of work that can be costed."""
    return {
        "scopes": [
            {"key": k, "label": v["label"],
             "rate_range_per_sqft": [v["rate_low"], v["rate_high"]],
             "recovery_percent": round(v["recovery"] * 100),
             "working_days": v["days"], "note": v["note"]}
            for k, v in renovation.SCOPES.items()
        ],
        "styles": list(renovation.STYLES),
        "rooms": list(renovation.ROOMS),
    }


@app.post("/api/v1/renovation/stage")
async def renovation_stage(room: str = Form(...), style: str = Form(...),
                           area_sqft: Optional[float] = Form(None),
                           file: Optional[UploadFile] = File(None)):
    """Plan (and, with a provider configured, render) a virtual staging pass."""
    data = await file.read() if file is not None else None
    try:
        return renovation.stage(room, style, data, area_sqft)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/rentals/{rental_id}/safety")
def rental_safety(rental_id: int, db: Session = Depends(get_db)):
    """What a tenant should know before paying a deposit on this letting."""
    from .models import RentalListing
    rental = db.get(RentalListing, rental_id)
    if rental is None:
        raise HTTPException(status_code=404, detail="No such rental listing.")
    prop = db.get(Property, rental.property_id)
    return tenant_safety.brief(ps.rental_to_dict(rental, prop),
                               prop.to_public() if prop else None)


@app.get("/api/v1/tenant/rules")
def tenant_rules():
    """The deposit rules, the viewing checklist, and what the law already gives."""
    return {
        "money_rules": [{"code": c, "rule": r, "why": w}
                        for c, r, w in tenant_safety.MONEY_RULES],
        "viewing_checklist": tenant_safety.VIEWING_CHECKLIST,
        "your_rights": [{"right": r, "detail": d}
                        for r, d in tenant_safety.TENANT_RIGHTS],
        "disclaimer": (
            "This platform never collects a tenant's deposit and never holds "
            "it. Anyone asking you to pay a deposit to us, or to a broker on "
            "our behalf, is committing fraud — report it."),
    }


@app.post("/api/v1/tenant/scan")
def tenant_scan(body: MessageScan, request: Request):
    """Check what a landlord or broker sent a tenant for pressure tactics."""
    hits = tenant_safety.scan_message(body.text)
    return {
        "flags": hits,
        "count": len(hits),
        "verdict": "PRESSURE_DETECTED" if hits else "NOTHING_DETECTED",
        "advice": (
            "These lines usually come before a deposit disappears. Do not pay "
            "anything, and ask for the ownership document and a written "
            "agreement." if hits else
            "Nothing recognised — this is a keyword check, not proof of safety."),
    }


@app.post("/api/v1/rental/chat")
def rental_chat(turn: SellerTurn, db: Session = Depends(get_db)):
    """Ultron — one turn of the rental intake. State is held by the client."""
    return ultron.handle(db, {"stage": turn.stage, "draft": turn.draft}, turn.text)


@app.get("/api/v1/playbook/status")
def playbook_status(db: Session = Depends(get_db)):
    """Which build phase this deployment is actually in, measured from data."""
    return pb.build_status(db)


@app.get("/api/v1/playbook/revenue-model")
def revenue_model_status(db: Session = Depends(get_db)):
    """Get revenue model summary (Section 4.1: Premier Agent model)."""
    return pb.revenue_model_summary(db)


# ---- PREMIER AGENT REVENUE MODEL ----

@app.post("/api/v1/agents/register")
def register_agent(name: str = Query(...), phone: str = Query(...),
                  email: str = Query(...), company: str = Query(""),
                  specialization: str = Query("residential"),
                  service_areas: List[str] = Query([]),
                  rera_id: str = Query(""), rera_state: str = Query(""),
                  lead_fee_inr: float = Query(500.0),
                  db: Session = Depends(get_db),
                  _staff: str = Depends(require_staff)):
    """Register real estate agent for lead generation (Premier Agent model)."""
    try:
        return rev.register_agent(db, name, phone, email, company, specialization,
                                 service_areas, rera_id, rera_state, lead_fee_inr)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/agents")
def list_agents(db: Session = Depends(get_db), _staff: str = Depends(require_staff)):
    """List all registered agents."""
    from sqlalchemy import select
    from .models import Agent
    rows = db.execute(select(Agent)).scalars().all()
    return {
        "count": len(rows),
        "agents": [rev.agent_to_dict(a) for a in rows]
    }


@app.post("/api/v1/leads/assign-to-agent")
def assign_lead(lead_id: int = Query(...), property_id: int = Query(None),
               db: Session = Depends(get_db),
               _staff: str = Depends(require_staff)):
    """Assign a qualified lead to an agent (Premier Agent revenue)."""
    try:
        return rev.assign_lead_to_agent(db, lead_id, property_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/leads/assignment/update-status")
def update_lead_assignment(assignment_id: int = Query(...),
                          status: str = Query(...),
                          db: Session = Depends(get_db),
                          _staff: str = Depends(require_staff)):
    """Update lead conversion status (CONTACTED|INTERESTED|CONVERTED|ABANDONED)."""
    try:
        return rev.update_lead_status(db, assignment_id, status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/revenue/dashboard")
def revenue_dashboard(db: Session = Depends(get_db),
                     _staff: str = Depends(require_staff)):
    """Real-time revenue metrics (Premier Agent model)."""
    return rev.revenue_dashboard(db)


@app.post("/api/v1/payments/process")
def process_payment(agent_id: int = Query(...),
                   db: Session = Depends(get_db),
                   _staff: str = Depends(require_staff)):
    """Settle every earned lead fee for this agent. Amount comes from the ledger."""
    try:
        return rev.process_payment(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- RERA COMPLIANCE (Section 7.5) ----

@app.post("/api/v1/rera/register-state")
def register_rera_state(state: str = Query(...),
                       authority_name: str = Query(""),
                       authority_email: str = Query(""),
                       authority_website: str = Query(""),
                       db: Session = Depends(get_db),
                       _staff: str = Depends(require_staff)):
    """Register RERA state compliance tracking."""
    return pb.init_rera_state(db, state, authority_name, authority_email, authority_website)


@app.get("/api/v1/rera/status")
def rera_status(state: str = Query(...), db: Session = Depends(get_db)):
    """Get RERA compliance status for a state."""
    from sqlalchemy import select
    from .models import RERACompliance
    rera = db.execute(select(RERACompliance).filter_by(state=state)).scalar()
    if not rera:
        return {"state": state, "status": "not_registered"}
    return pb.rera_to_dict(rera)


# ---- POST-SALE FEATURES (Phase 4) ----

@app.post("/api/v1/rentals/register")
def register_rental(property_id: int = Query(...), monthly_rent_inr: float = Query(...),
                   lease_terms: str = Query("11 months"),
                   furnishing: str = Query("Unfurnished"),
                   db: Session = Depends(get_db),
                   _staff: str = Depends(require_staff)):
    """Register property for rental management (Phase 4)."""
    try:
        return ps.register_rental(db, property_id, monthly_rent_inr, lease_terms, furnishing)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/rentals")
def list_rentals(db: Session = Depends(get_db)):
    """List all rental listings, each with the property it belongs to."""
    from sqlalchemy import select
    from .models import RentalListing
    rows = db.execute(select(RentalListing)).scalars().all()
    # Without the joined Property the list renders as bare row ids, which is
    # useless to a landlord looking for one of their own lettings.
    props = {p.id: p for p in db.execute(select(Property)).scalars().all()}
    return {
        "count": len(rows),
        "rentals": [ps.rental_to_dict(r, props.get(r.property_id)) for r in rows],
    }


@app.post("/api/v1/rentals/match-tenant")
def match_tenant(rental_id: int = Query(...), tenant_name: str = Query(...),
                tenant_phone: str = Query(...),
                lease_start_days: int = Query(0),  # days from now
                lease_end_days: int = Query(330),  # 11 months ~= 330 days
                db: Session = Depends(get_db),
                _staff: str = Depends(require_staff)):
    """Record tenant match for a rental."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    try:
        return ps.match_tenant(db, rental_id, tenant_name, tenant_phone,
                              now + timedelta(days=lease_start_days),
                              now + timedelta(days=lease_end_days))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/farmland/register")
def register_farmland(property_id: int = Query(...), soil_type: str = Query(...),
                     region: str = Query(...), size_acres: float = Query(...),
                     irrigation_type: str = Query(...),
                     water_availability: str = Query("Moderate"),
                     db: Session = Depends(get_db),
                     _staff: str = Depends(require_staff)):
    """Register farmland for seasonal crop guidance (Phase 4)."""
    try:
        return ps.register_farmland(db, property_id, soil_type, region, size_acres,
                                   irrigation_type, water_availability)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/farmland/guidance")
def get_farmland_guidance(guidance_id: int = Query(...), db: Session = Depends(get_db)):
    """Get current seasonal guidance for farmland."""
    try:
        return ps.get_seasonal_guidance(db, guidance_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/post-sale/summary")
def post_sale_summary(db: Session = Depends(get_db)):
    """Phase 4: Post-sale features summary."""
    return ps.post_sale_summary(db)
