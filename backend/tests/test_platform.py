"""End-to-end tests against a temporary SQLite catalogue.

Run:  pytest -q
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point at a throwaway database BEFORE the app modules import settings.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name}"
os.environ["VECTOR_BACKEND"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["REQUIRE_AUTH"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app import documents as docs  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditEvent, Property  # noqa: E402
from app.search import parse_constraints, search  # noqa: E402
from app.whatsapp_agent import handle_message  # noqa: E402

import seed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def catalogue():
    init_db()
    seed.run(reset=True)
    yield
    try:
        os.unlink(_TMP.name)
    except OSError:
        pass


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


client = TestClient(app)


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text,expected", [
    ("3BHK flat in Whitefield under 1.3 crore", 13_000_000.0),
    ("plot below 90 lakhs", 9_000_000.0),
    ("budget 1.5 cr", 15_000_000.0),
    ("villa upto 2 crore", 20_000_000.0),
    ("apartment under ₹75 lakh", 7_500_000.0),
])
def test_budget_parsing(text, expected):
    assert parse_constraints(text)["max_price"] == expected


def test_parses_locality_config_and_type():
    c = parse_constraints("ready to move 3 BHK apartment in Whitefield under 1.3 crore")
    assert c["locality"] == "Whitefield"
    assert c["config"] == "3BHK"
    assert c["property_type"] == "Flat"
    assert c["possession"] == "Ready to move"


def test_longest_locality_wins():
    # "electronic city" must not be shadowed by a shorter partial match
    assert parse_constraints("2bhk in Electronic City")["locality"] == "Electronic City"


# ----------------------------------------------------------------- search

def test_requested_locality_is_respected(db):
    """The bug this guards: locality left to lexical similarity ranked
    Electronic City above the Whitefield property the buyer asked for."""
    r = search(db, "3BHK flat in Whitefield under 1.3 crore", limit=3)
    assert r["count"] >= 1
    assert all(m["location_name"] == "Whitefield" for m in r["matches"])


def test_budget_is_never_exceeded(db):
    r = search(db, "flat under 80 lakh", limit=10)
    assert r["matches"]
    assert all(m["price_inr"] <= 8_000_000 for m in r["matches"])


def test_verification_gate_blocks_unverified(db):
    """FR-5: unverified or flagged stock must never reach a buyer."""
    for q in ["flat", "plot", "villa", "yelahanka", "marathahalli", "kanakapura farm land"]:
        for m in search(db, q, limit=20)["matches"]:
            assert m["is_verified"] is True, f"{m['title']} leaked for query {q!r}"


def test_empty_locality_widens_and_says_so(db):
    r = search(db, "2BHK flat in Jayanagar under 90 lakh")
    assert r["relaxed_locality"] is True
    assert r["notice"] and "Jayanagar" in r["notice"]


def test_rationale_is_derived_not_echoed(db):
    """The source draft returned the user's own prompt as the match reason."""
    prompt = "3BHK flat in Whitefield under 1.3 crore"
    r = search(db, prompt, limit=1)
    why = r["matches"][0]["why"]
    assert prompt.lower() not in why.lower()
    assert "Whitefield" in why and "Cr" in why  # real fields, correct casing


def test_unverified_listing_carries_a_concern(db):
    """Turn the gate off and the warning must still be attached."""
    from app.config import settings
    settings.ENFORCE_VERIFICATION_GATE = False
    try:
        r = search(db, "2BHK Yelahanka", limit=5)
        flagged = [m for m in r["matches"] if not m["is_verified"]]
        assert flagged, "expected unverified stock once the gate is off"
        assert all(m["concern"] for m in flagged)
    finally:
        settings.ENFORCE_VERIFICATION_GATE = True


# -------------------------------------------------------------- documents

def test_encumbrance_is_flagged():
    r = docs.verify_document(
        "ENCUMBRANCE CERTIFICATE. A mortgage was created in favour of the bank "
        "on the schedule property. Survey No: 42/1")
    assert r["verdict"] == "RED_FLAGGED"
    assert any(i["code"] == "ENCUMBRANCE_FOUND" for i in r["issues"])


def test_clean_ec_passes():
    r = docs.verify_document(
        "ENCUMBRANCE CERTIFICATE Form 15. The property is free from all "
        "encumbrance for the period 1994 to 2026. Survey No: 42/1")
    assert r["verdict"] == "PASSED"
    assert r["document_type"] == "EC"


def test_b_khata_flagged():
    r = docs.verify_document("This is a B-Khata property under the BBMP limits.")
    assert any(i["code"] == "B_KHATA" for i in r["issues"])


def test_owner_mismatch_detected():
    r = docs.verify_document(
        "SALE DEED executed by Ramesh Kumar. Signature of witness attached.",
        expected_owner="Sunita Reddy")
    assert any(i["code"] == "OWNER_MISMATCH" for i in r["issues"])


def test_empty_document_never_passes():
    assert docs.verify_document("")["verdict"] == "RED_FLAGGED"


def test_result_always_carries_the_disclaimer():
    r = docs.verify_document("Encumbrance certificate, free from all encumbrance.")
    assert "not a title opinion" in r["disclaimer"]


# --------------------------------------------------------------- WhatsApp

def test_full_qualification_to_booking(db):
    phone = "+919800001111"
    r1 = handle_message(db, phone, "hi")
    assert r1["stage"] == "QUALIFYING"

    handle_message(db, phone, "looking for a flat in Whitefield")
    r3 = handle_message(db, phone, "budget 1.3 crore, buying immediately")
    assert r3["stage"] == "PRESENTING"
    assert r3["matches"], "should have presented at least one match"
    assert all(m["location_name"] == "Whitefield" for m in r3["matches"])

    r4 = handle_message(db, phone, "yes please")
    assert r4["stage"] == "BOOKING"

    r5 = handle_message(db, phone, "Saturday morning")
    assert r5["status"] == "VISIT_SCHEDULED"
    assert r5["intent_score"] == 95


def test_declining_keeps_the_lead_qualified(db):
    phone = "+919800002222"
    handle_message(db, phone, "2bhk in Sarjapur")
    handle_message(db, phone, "under 90 lakh")
    r = handle_message(db, phone, "no thanks")
    assert r["status"] == "QUALIFIED"


def test_agent_never_quotes_unverified_stock(db):
    phone = "+919800003333"
    handle_message(db, phone, "2bhk flat in Yelahanka")
    r = handle_message(db, phone, "under 80 lakh")
    for m in r["matches"]:
        assert m["is_verified"] is True


# ------------------------------------------------------------------- HTTP

def test_health_reports_dev_mode_honestly():
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["semantic_search"] is False
    assert "warning" in body


def test_search_endpoint():
    r = client.post("/api/v1/search", json={
        "user_prompt": "3BHK flat in Whitefield under 1.3 crore", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert body["matches"][0]["location_name"] == "Whitefield"


def test_new_listing_enters_unverified_and_is_invisible():
    created = client.post("/api/v1/properties", json={
        "title": "Test 3BHK, Whitefield", "property_type": "Flat",
        "location_name": "Whitefield", "price_inr": 9_000_000, "config": "3BHK",
    }).json()
    assert created["is_verified"] is False

    found = client.post("/api/v1/search", json={
        "user_prompt": "Test 3BHK Whitefield", "limit": 10}).json()
    assert all(m["id"] != created["id"] for m in found["matches"])

    # A bare listing has nothing on file, so the plain sign-off is refused —
    # that guard is what keeps an unexamined title away from buyers. The
    # advocate's override, with a reason, is the honest route through.
    refused = client.post(f"/api/v1/properties/{created['id']}/verify")
    assert refused.status_code == 409

    client.post(f"/api/v1/properties/{created['id']}/verify",
                params={"override": "true",
                        "reason": "Test fixture — originals examined."})
    found2 = client.post("/api/v1/search", json={
        "user_prompt": "Test 3BHK Whitefield", "limit": 10}).json()
    assert any(m["id"] == created["id"] for m in found2["matches"])


def test_document_endpoint():
    r = client.post("/api/v1/documents/check", json={
        "text": "Sale deed. A mortgage and lien subsist over the property."})
    assert r.json()["verdict"] == "RED_FLAGGED"


def test_whatsapp_verify_handshake():
    ok = client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "dev-verify-token",
        "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"

    bad = client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"})
    assert bad.status_code == 403


def test_whatsapp_inbound_payload():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "+919800009999", "type": "text",
         "text": {"body": "hi there"}}]}}]}]}
    r = client.post("/api/v1/whatsapp/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["handled"] == 1


# ------------------------------------------------------------------- auth

STAFF = {"Authorization": "Bearer test-staff-key"}


@pytest.fixture()
def auth_on():
    """Turn auth on for one test (the suite runs with it off by default)."""
    from app import auth
    from app.config import settings
    settings.REQUIRE_AUTH = True
    settings.STAFF_API_KEYS = "test-staff-key"
    try:
        yield
    finally:
        settings.REQUIRE_AUTH = False
        settings.STAFF_API_KEYS = ""


def test_buyer_pii_requires_staff_auth(auth_on):
    """Lead rows carry phone numbers — never public."""
    assert client.get("/api/v1/leads").status_code == 401
    assert client.get("/api/v1/leads", headers=STAFF).status_code == 200


def test_write_endpoints_require_staff_auth(auth_on):
    body = {"title": "X", "property_type": "Flat",
            "location_name": "Whitefield", "price_inr": 5_000_000}
    assert client.post("/api/v1/properties", json=body).status_code == 401
    assert client.post("/api/v1/properties/1/verify").status_code == 401
    assert client.post("/api/v1/reindex").status_code == 401
    assert client.get("/api/v1/site-visits").status_code == 401


def test_wrong_key_is_forbidden_not_unauthorized(auth_on):
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/api/v1/leads", headers=bad).status_code == 403


def test_x_api_key_header_also_works(auth_on):
    r = client.get("/api/v1/leads", headers={"X-API-Key": "test-staff-key"})
    assert r.status_code == 200


def test_public_endpoints_stay_open(auth_on):
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/search",
                       json={"user_prompt": "flat in Whitefield"}).status_code == 200
    assert client.post("/api/v1/documents/check",
                       json={"text": "Sale deed."}).status_code == 200
    assert client.get("/api/v1/properties").status_code == 200


def test_health_flags_disabled_auth():
    body = client.get("/api/v1/health").json()
    assert body["auth_required"] is False
    assert "auth_warning" in body


# -------------------------------------------------------- whatsapp outbound

def test_outbound_is_dry_run_without_credentials():
    from app import whatsapp_send
    assert whatsapp_send.is_live() is False
    rec = whatsapp_send.send_text("+919800000000", "hello")
    assert rec["status"] == "dry_run"


def test_empty_body_is_not_sent():
    from app import whatsapp_send
    assert whatsapp_send.send_text("+919800000000", "")["status"] == "skipped"


def test_long_body_is_truncated_to_whatsapp_limit():
    from app import whatsapp_send
    rec = whatsapp_send.send_text("+919800000000", "x" * 5000)
    assert rec["chars"] == 4096


def test_webhook_reports_delivery():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "+919800007777", "type": "text",
         "text": {"body": "hello"}}]}}]}]}
    body = client.post("/api/v1/whatsapp/webhook", json=payload).json()
    assert body["handled"] == 1
    assert body["results"][0]["delivery"]["status"] == "dry_run"


# ----------------------------------------------------------- seller (Jarvis)

def test_seller_intake_collects_then_creates(db):
    from app import seller_agent as sa
    state = {"stage": None, "draft": {}}

    for msg in ["apartment", "Whitefield", "3BHK", "1580 sqft",
                "1.2 crore", "ready to move", "A-Khata"]:
        out = sa.handle(db, state, msg)
        state = {"stage": out["stage"], "draft": out["draft"]}

    assert out["stage"] == "CONFIRM"
    assert out["draft"]["locality"] == "Whitefield"
    assert out["draft"]["price_inr"] == 12_000_000
    assert out["draft"]["config"] == "3BHK"

    done = sa.handle(db, state, "yes")
    # Creation hands off to the fraud screen, it does not end the flow.
    assert done["stage"] == "VERIFY"
    assert done["created"] is not None
    # Never trusted on entry — same rule as the REST path.
    assert done["created"]["is_verified"] is False


def test_seller_created_listing_is_invisible_until_verified(db):
    from app import seller_agent as sa
    state = {"stage": None, "draft": {}}
    for msg in ["villa", "Hebbal", "4BHK", "3000 sqft", "4 crore", "ready", "A-Khata"]:
        out = sa.handle(db, state, msg)
        state = {"stage": out["stage"], "draft": out["draft"]}
    created = sa.handle(db, state, "yes")["created"]

    hits = search(db, "villa in Hebbal", limit=20)["matches"]
    assert all(m["id"] != created["id"] for m in hits)


def test_seller_extracts_several_fields_from_one_message(db):
    from app import seller_agent as sa
    out = sa.handle(db, {"stage": None, "draft": {}},
                    "3BHK apartment in Sarjapur, 1450 sqft, asking 95 lakh, ready to move")
    d = out["draft"]
    assert d["property_type"] == "Flat" and d["locality"] == "Sarjapur"
    assert d["config"] == "3BHK" and d["area_sqft"] == 1450
    assert d["price_inr"] == 9_500_000 and d["possession"] == "Ready to move"


def test_seller_skip_on_khata(db):
    from app import seller_agent as sa
    state = {"stage": "ASK_TITLE", "draft": {
        "property_type": "Flat", "locality": "Hebbal", "config": "2BHK",
        "area_sqft": 1000, "price_inr": 7_000_000, "possession": "Ready to move"}}
    out = sa.handle(db, state, "skip")
    assert out["draft"]["khata"] == "Not stated"
    assert out["stage"] == "CONFIRM"


def test_seller_endpoint():
    r = client.post("/api/v1/seller/chat", json={"text": "plot in Devanahalli", "draft": {}})
    assert r.status_code == 200
    assert r.json()["draft"]["locality"] == "Devanahalli"


# ------------------------------------------------------------------ vision

def test_plan_status_reports_unavailable_on_stub():
    body = client.get("/api/v1/plan/status").json()
    assert body["available"] is False
    assert "vision model" in body["reason"]


def test_plan_rejects_bad_media_type():
    from app import vision
    with pytest.raises(ValueError, match="Unsupported image type"):
        vision.read_plan(b"x", "application/pdf")


def test_plan_rejects_oversized_image():
    from app import vision
    with pytest.raises(ValueError, match="limit is"):
        vision.read_plan(b"x" * (9 * 1024 * 1024), "image/png")


def test_plan_endpoint_degrades_without_vision():
    files = {"file": ("plan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, "image/png")}
    body = client.post("/api/v1/plan/match", files=files).json()
    assert body["reading"]["available"] is False
    assert body["results"] is None


def test_generated_title_keeps_bhk_uppercase(db):
    """.capitalize() lowercases the rest — '3BHK' must not become '3bhk'."""
    from app.seller_agent import _title_for
    assert _title_for({"config": "3BHK", "property_type": "Flat",
                       "locality": "Koramangala"}) == "3BHK flat, Koramangala"
    assert _title_for({"property_type": "Plot", "locality": "Devanahalli"}) == "Plot, Devanahalli"


# -------------------------------------------------- verification (anti-fraud)

def test_checklist_varies_by_property_type():
    from app import verification as v
    flat = {d["code"] for d in v.required_documents("Flat")}
    plot = {d["code"] for d in v.required_documents("Plot")}
    agri = {d["code"] for d in v.required_documents("Agricultural")}
    assert "OC" in flat and "OC" not in plot
    assert "LAYOUT_APPROVAL" in plot
    assert "RTC" in agri and "SALE_DEED" not in agri


def test_gpa_sale_is_high_risk():
    """Suraj Lamp (2011): GPA transfers do not convey title."""
    from app import verification as v
    r = v.assess("Flat", {"GPA_SALE": True}, [])
    gpa = [f for f in r["findings"] if f["code"] == "GPA_SALE"]
    assert gpa and gpa[0]["severity"] == "high"
    assert "Suraj Lamp" in gpa[0]["detail"]


def test_all_safe_answers_and_all_docs_is_ready():
    from app import verification as v
    answers = {qid: safe for qid, _, safe, _, _ in v.QUESTIONS}
    docs = [d["code"] for d in v.required_documents("Flat")]
    r = v.assess("Flat", answers, docs)
    assert r["status"] == "READY_FOR_ADVOCATE"
    assert r["high_count"] == 0


def test_missing_core_document_is_high_risk():
    from app import verification as v
    answers = {qid: safe for qid, _, safe, _, _ in v.QUESTIONS}
    docs = [d["code"] for d in v.required_documents("Flat") if d["code"] != "SALE_DEED"]
    r = v.assess("Flat", answers, docs)
    assert r["status"] == "HIGH_RISK"
    assert any(f["code"] == "MISSING_SALE_DEED" for f in r["findings"])


def test_unanswered_questions_block_readiness():
    from app import verification as v
    r = v.assess("Flat", {}, [d["code"] for d in v.required_documents("Flat")])
    assert r["status"] == "INCOMPLETE"
    assert len(r["unanswered"]) == len(v.QUESTIONS)


def test_assessment_always_carries_the_disclaimer():
    from app import verification as v
    r = v.assess("Flat", {}, [])
    assert "not a verification" in r["disclaimer"]
    assert "advocate" in r["disclaimer"]


def test_disclosures_never_flip_is_verified():
    """A self-declaration must never be able to verify a listing."""
    created = client.post("/api/v1/properties", json={
        "title": "Fraud test", "property_type": "Flat",
        "location_name": "Hebbal", "price_inr": 8_000_000}).json()
    from app import verification as v
    answers = {qid: safe for qid, _, safe, _, _ in v.QUESTIONS}
    docs = [d["code"] for d in v.required_documents("Flat")]
    r = client.post(f"/api/v1/properties/{created['id']}/disclosures",
                    json={"answers": answers, "documents_held": docs}).json()
    assert r["status"] == "READY_FOR_ADVOCATE"
    after = client.get(f"/api/v1/properties/{created['id']}/verification").json()
    assert after["is_verified"] is False


# ------------------------------------------------------------------ uploads

def test_upload_rejects_disguised_executable():
    """Content-Type is client-controlled; magic bytes are checked instead."""
    from app import uploads
    with pytest.raises(ValueError, match="Unrecognised file"):
        uploads.store("/tmp/x", 1, "photo", "evil.png", b"MZ\x90\x00 not a png")


def test_upload_rejects_svg():
    from app import uploads
    with pytest.raises(ValueError, match="Unrecognised file"):
        uploads.store("/tmp/x", 1, "photo", "x.svg",
                      b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")


def test_upload_filename_cannot_traverse(tmp_path):
    from app import uploads
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    rec = uploads.store(str(tmp_path), 7, "photo", "../../../../etc/passwd", png)
    assert "/" not in rec["stored_name"] and ".." not in rec["stored_name"]
    assert os.path.realpath(rec["path"]).startswith(os.path.realpath(str(tmp_path)))
    # basename() strips the traversal outright, so only the leaf survives
    assert rec["label"] == "passwd"


def test_resolve_refuses_escape(tmp_path):
    from app import uploads
    with pytest.raises(ValueError, match="Bad file reference"):
        uploads.resolve(str(tmp_path), 1, "../../../etc/passwd")


def test_upload_size_cap(tmp_path):
    from app import uploads
    with pytest.raises(ValueError, match="limit is"):
        uploads.store(str(tmp_path), 1, "photo", "big.png",
                      b"\x89PNG\r\n\x1a\n" + b"0" * (11 * 1024 * 1024))


def test_upload_endpoint_records_document_held(tmp_path):
    created = client.post("/api/v1/properties", json={
        "title": "Upload test", "property_type": "Flat",
        "location_name": "Sarjapur", "price_inr": 7_000_000}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = client.post(f"/api/v1/properties/{created['id']}/uploads",
                    files={"file": ("deed.png", png, "image/png")},
                    data={"kind": "document", "doc_code": "SALE_DEED"})
    assert r.status_code == 201
    assert r.json()["doc_code"] == "SALE_DEED"
    state = client.get(f"/api/v1/properties/{created['id']}/verification").json()
    assert "SALE_DEED" in state["documents_held"]


def test_uploaded_file_download_is_staff_only(auth_on):
    r = client.get("/api/v1/properties/1/uploads/1")
    assert r.status_code == 401


# ----------------------------------------------------- evidence engine

def _pdf(body: bytes = b"", *, signed=False, revisions=1, text="") -> bytes:
    out = b"%PDF-1.7\n"
    if text:
        out += b"BT /Font " + " ".join(f"({w}) Tj" for w in text.split()).encode() + b" ET\n"
    if signed:
        out += b"/Sig /ByteRange [0 100 200 300]\n"
    out += body
    out += b"%%EOF\n" * revisions
    return out


def test_hash_is_stable_and_distinct():
    from app import evidence as ev
    assert ev.digest(b"abc") == ev.digest(b"abc")
    assert ev.digest(b"abc") != ev.digest(b"abd")
    assert len(ev.digest(b"abc")) == 64


def test_detects_pdf_signature_and_revisions():
    from app import evidence as ev
    signed = ev.pdf_signals(_pdf(signed=True))
    assert signed["is_pdf"] and signed["digitally_signed"]
    edited = ev.pdf_signals(_pdf(revisions=3))
    assert edited["digitally_signed"] is False and edited["revisions"] == 3


def test_cross_document_survey_mismatch_is_high():
    """The check a tick-box can never do."""
    from app import evidence as ev
    findings = ev.cross_check([
        {"code": "SALE_DEED", "fields": {"survey_no": "42/1"}},
        {"code": "EC", "fields": {"survey_no": "88/3"}},
    ])
    codes = [f["code"] for f in findings]
    assert "MISMATCH_SURVEY_NO" in codes
    assert findings[0]["severity"] == "high"


def test_consistent_documents_produce_no_mismatch():
    from app import evidence as ev
    assert ev.cross_check([
        {"code": "SALE_DEED", "fields": {"survey_no": "42/1"}},
        {"code": "EC", "fields": {"survey_no": "42 / 1"}},   # normalised
    ]) == []


def test_duplicate_file_across_properties_is_high():
    from app import evidence as ev
    r = ev.assess_documents(
        [{"code": "SALE_DEED", "label": "Sale deed", "why": "x"}],
        [{"code": "SALE_DEED", "label": "deed.pdf", "sha256": "abc",
          "bytes": 200_000, "media_type": "application/pdf"}],
        duplicate_hits={"abc": [7, 9]})
    dup = [f for f in r["findings"] if f["code"] == "DUPLICATE_ACROSS_PROPERTIES"]
    assert dup and dup[0]["severity"] == "high"
    assert "#7" in dup[0]["detail"]


def test_thin_file_flagged():
    from app import evidence as ev
    r = ev.assess_documents(
        [{"code": "EC", "label": "EC", "why": "x"}],
        [{"code": "EC", "label": "ec.pdf", "sha256": "h", "bytes": 3000,
          "media_type": "application/pdf"}])
    assert any(f["code"] == "THIN_FILE" for f in r["findings"])


def test_unsigned_official_pdf_flagged():
    from app import evidence as ev
    r = ev.assess_documents(
        [{"code": "EC", "label": "EC", "why": "x"}],
        [{"code": "EC", "label": "ec.pdf", "sha256": "h", "bytes": 200_000,
          "media_type": "application/pdf",
          "pdf": {"is_pdf": True, "digitally_signed": False,
                  "revisions": 1, "has_text_layer": True}}])
    assert any(f["code"] == "UNSIGNED_OFFICIAL_PDF" for f in r["findings"])


def test_levels_progress_with_evidence():
    from app import evidence as ev
    req = [{"code": "SALE_DEED", "label": "Deed", "why": "x"},
           {"code": "EC", "label": "EC", "why": "x"}]
    assert ev.assess_documents(req, [])["level"] == "NOT_SUBMITTED"

    partial = ev.assess_documents(req, [
        {"code": "SALE_DEED", "label": "d", "sha256": "a", "bytes": 200_000,
         "media_type": "application/pdf",
         "pdf": {"is_pdf": True, "digitally_signed": True, "revisions": 1,
                 "has_text_layer": True}}])
    assert partial["level"] == "PARTIAL"

    full = ev.assess_documents(req, [
        {"code": c, "label": c, "sha256": c, "bytes": 200_000,
         "media_type": "application/pdf",
         "pdf": {"is_pdf": True, "digitally_signed": True, "revisions": 1,
                 "has_text_layer": True}}
        for c in ("SALE_DEED", "EC")])
    assert full["level"] == "MACHINE_CHECKED"


# ---------------------------------------------------------------- vault

def test_vault_roundtrip_and_tamper_detection(monkeypatch):
    from app.config import settings
    from app import vault
    monkeypatch.setattr(settings, "DOCUMENT_KEY", "a-test-passphrase")
    monkeypatch.setattr(vault, "_fernet", None)
    monkeypatch.setattr(vault, "_checked", False)

    plain = b"ENCUMBRANCE CERTIFICATE Form 15"
    sealed = vault.seal(plain)
    assert sealed != plain and sealed.startswith(b"VLT1")
    assert vault.unseal(sealed) == plain

    with pytest.raises(ValueError, match="tampered|different key"):
        vault.unseal(b"VLT1" + sealed[10:])


def test_vault_passthrough_without_key(monkeypatch):
    from app.config import settings
    from app import vault
    monkeypatch.setattr(settings, "DOCUMENT_KEY", "")
    monkeypatch.setattr(vault, "_fernet", None)
    monkeypatch.setattr(vault, "_checked", False)
    assert vault.enabled() is False
    assert vault.seal(b"x") == b"x"      # honest passthrough, not fake crypto


# --------------------------------------------------- evidence, end to end

def test_upload_records_hash_and_audit_trail():
    created = client.post("/api/v1/properties", json={
        "title": "Evidence test", "property_type": "Flat",
        "location_name": "Hebbal", "price_inr": 9_000_000}).json()
    pdf = _pdf(b"0" * 70_000, signed=True, text="Survey No: 42/1")

    r = client.post(f"/api/v1/properties/{created['id']}/uploads",
                    files={"file": ("deed.pdf", pdf, "application/pdf")},
                    data={"kind": "document", "doc_code": "SALE_DEED"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["sha256"]) == 64
    assert body["text_read"] is True
    assert body["extracted"]["survey_no"] == "42/1"

    audit = client.get(f"/api/v1/properties/{created['id']}/audit").json()
    assert audit["count"] >= 1
    assert audit["events"][0]["action"] == "UPLOAD"


def test_same_file_on_two_properties_is_flagged():
    a = client.post("/api/v1/properties", json={
        "title": "Ring A", "property_type": "Flat",
        "location_name": "Hebbal", "price_inr": 5_000_000}).json()
    b = client.post("/api/v1/properties", json={
        "title": "Ring B", "property_type": "Flat",
        "location_name": "Hebbal", "price_inr": 6_000_000}).json()
    pdf = _pdf(b"1" * 70_000, signed=True, text="Survey No: 99/9")
    files = {"file": ("deed.pdf", pdf, "application/pdf")}
    data = {"kind": "document", "doc_code": "SALE_DEED"}

    client.post(f"/api/v1/properties/{a['id']}/uploads", files=files, data=data)
    second = client.post(f"/api/v1/properties/{b['id']}/uploads",
                         files=files, data=data).json()
    assert second["warnings"], "re-used document must warn at upload time"

    state = client.get(f"/api/v1/properties/{b['id']}/verification").json()
    assert any(f["code"] == "DUPLICATE_ACROSS_PROPERTIES"
               for f in state["evidence"]["findings"])


def test_documents_that_disagree_are_caught_end_to_end():
    prop = client.post("/api/v1/properties", json={
        "title": "Mismatch test", "property_type": "Flat",
        "location_name": "Sarjapur", "price_inr": 7_000_000}).json()
    for code, survey in (("SALE_DEED", "42/1"), ("EC", "88/3")):
        client.post(f"/api/v1/properties/{prop['id']}/uploads",
                    files={"file": (f"{code}.pdf",
                                    _pdf(b"2" * 70_000, signed=True,
                                         text=f"Survey No: {survey}"),
                                    "application/pdf")},
                    data={"kind": "document", "doc_code": code})
    state = client.get(f"/api/v1/properties/{prop['id']}/verification").json()
    assert any(f["code"] == "MISMATCH_SURVEY_NO"
               for f in state["evidence"]["findings"])
    assert state["level"] in {"PARTIAL", "FLAGGED"}


def test_uploading_everything_still_does_not_verify():
    """The whole point: evidence never self-certifies."""
    prop = client.post("/api/v1/properties", json={
        "title": "No self cert", "property_type": "Flat",
        "location_name": "Whitefield", "price_inr": 9_000_000}).json()
    from app import verification as v
    for d in v.required_documents("Flat"):
        client.post(f"/api/v1/properties/{prop['id']}/uploads",
                    files={"file": (f"{d['code']}.pdf",
                                    _pdf(b"3" * 70_000, signed=True,
                                         text="Survey No: 42/1"),
                                    "application/pdf")},
                    data={"kind": "document", "doc_code": d["code"]})
    state = client.get(f"/api/v1/properties/{prop['id']}/verification").json()
    assert state["evidence"]["level"] == "MACHINE_CHECKED"
    assert state["is_verified"] is False
    assert state["level"] != "ADVOCATE_VERIFIED"


# ------------------------------------------------------------ buyer safety

def test_unverified_listing_says_do_not_pay():
    from app import buyer_safety as bs
    b = bs.brief({"is_verified": False, "risk_status": "NOT_SUBMITTED"})
    assert b["stance"] == "DO_NOT_PAY"
    assert any(w["severity"] == "high" for w in b["warnings"])


def test_verified_listing_still_carries_payment_rules():
    from app import buyer_safety as bs
    b = bs.brief({"is_verified": True}, {"level": "ADVOCATE_VERIFIED"})
    assert b["stance"] == "PROCEED_WITH_NORMAL_CARE"
    codes = {r["code"] for r in b["golden_rules"]}
    assert "NEVER_PAY_BEFORE_TITLE" in codes and "NO_GPA_PURCHASE" in codes


def test_b_khata_warns_the_buyer():
    from app import buyer_safety as bs
    b = bs.brief({"is_verified": True, "khata": "B-Khata"},
                 {"level": "ADVOCATE_VERIFIED"})
    assert any("B-Khata" in w["title"] for w in b["warnings"])


def test_flagged_listing_is_do_not_pay():
    from app import buyer_safety as bs
    b = bs.brief({"is_verified": False}, {"level": "FLAGGED"})
    assert b["stance"] == "DO_NOT_PAY"


@pytest.mark.parametrize("text,expected", [
    ("Sir please pay token today to block the unit", True),
    ("Cash only, no need for advocate", True),
    ("Send advance to hold it, my personal account", True),
    ("The flat faces east and has two balconies", False),
])
def test_pressure_phrase_detection(text, expected):
    from app import buyer_safety as bs
    assert bool(bs.scan_message(text)) is expected


def test_safety_endpoints():
    r = client.get("/api/v1/safety/rules").json()
    assert len(r["golden_rules"]) >= 6 and r["visit_checklist"]

    scan = client.post("/api/v1/safety/scan",
                       json={"text": "pay token today, cash only"}).json()
    assert scan["verdict"] == "PRESSURE_DETECTED"
    assert len(scan["flags"]) >= 2

    clean = client.post("/api/v1/safety/scan",
                        json={"text": "It is a 3BHK facing east"}).json()
    assert clean["verdict"] == "NOTHING_DETECTED"


def test_safety_brief_endpoint_for_a_real_listing():
    listing = client.get("/api/v1/properties").json()["properties"][0]
    b = client.get(f"/api/v1/properties/{listing['id']}/safety").json()
    assert b["stance"] in {"PROCEED_WITH_NORMAL_CARE", "PROCEED_WITH_CAUTION",
                           "DO_NOT_PAY"}
    assert b["golden_rules"]


def test_report_records_and_is_staff_only(auth_on):
    r = client.post("/api/v1/report", json={
        "reason": "Asked for token in cash",
        "detail": "He said pay token today, cash only, no need for advocate"})
    assert r.status_code == 201
    assert len(r.json()["red_flags"]) >= 2
    assert client.get("/api/v1/reports").status_code == 401
    assert client.get("/api/v1/reports", headers=STAFF).status_code == 200


# ----------------------------------------------------------- rate limiting

def test_rate_limit_blocks_and_reports_retry_after():
    from app import ratelimit
    ratelimit.reset()
    try:
        hit = None
        for _ in range(7):
            hit = ratelimit.check("1.2.3.4", "/api/v1/listings/submit")
        assert hit is not None
        assert hit["limit"] == 5 and hit["retry_after"] > 0
    finally:
        ratelimit.reset()


def test_rate_limit_is_per_client():
    from app import ratelimit
    ratelimit.reset()
    try:
        for _ in range(6):
            ratelimit.check("a", "/api/v1/listings/submit")
        assert ratelimit.check("a", "/api/v1/listings/submit") is not None
        assert ratelimit.check("b", "/api/v1/listings/submit") is None
    finally:
        ratelimit.reset()


def test_unlimited_paths_pass_through():
    from app import ratelimit
    ratelimit.reset()
    assert ratelimit.check("x", "/api/v1/health") is None


def test_middleware_returns_429_with_header():
    from app import ratelimit
    ratelimit.reset()
    try:
        last = None
        for _ in range(7):
            last = client.post("/api/v1/listings/submit", json={
                "title": "Spam", "property_type": "Flat",
                "location_name": "Hebbal", "price_inr": 5_000_000})
        assert last.status_code == 429
        assert int(last.headers["Retry-After"]) > 0
    finally:
        ratelimit.reset()


def test_security_headers_present():
    r = client.get("/api/v1/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_public_submission_enters_unverified():
    from app import ratelimit
    ratelimit.reset()
    try:
        d = client.post("/api/v1/listings/submit", json={
            "title": "Public submit", "property_type": "Flat",
            "location_name": "Sarjapur", "price_inr": 6_000_000}).json()
        assert d["is_verified"] is False
        assert d["risk_status"] == "NOT_SUBMITTED"
    finally:
        ratelimit.reset()


# ------------------------------------------------- playbook / revenue / post-sale
# Every test below pins a bug that shipped in the first cut of these modules.

from app import playbook as pb          # noqa: E402
from app import post_sale as ps         # noqa: E402
from app import revenue as rev          # noqa: E402
from app.models import (Agent, BeachheadMarket, LeadAssignment,  # noqa: E402
                        Lead, Payment)


@pytest.fixture()
def clean_revenue(db):
    """Revenue tables emptied around each test so counts are deterministic."""
    for model in (Payment, LeadAssignment, Agent):
        db.query(model).delete()
    db.commit()
    yield db
    for model in (Payment, LeadAssignment, Agent):
        db.query(model).delete()
    db.commit()


def _agent(db, **kw):
    kw.setdefault("name", "Test Agent")
    kw.setdefault("phone", "+919" + str(abs(hash(kw["name"])) % 10**9).zfill(9))
    kw.setdefault("email", kw["name"].replace(" ", "").lower() + "@example.com")
    return rev.register_agent(db, **kw)


def _lead(db, location="Whitefield", ptype="Flat"):
    lead = Lead(buyer_phone="+919700000000", preferred_location=location,
                property_type=ptype, budget_max=12_000_000, status="QUALIFIED")
    db.add(lead)
    db.commit()
    return lead


# ---- beachhead + interviews ----

def test_interview_counts_against_a_planning_beachhead(db):
    """Regression: interviews were matched on status=ACTIVE, so a freshly
    created (PLANNING) market silently counted nothing."""
    db.query(BeachheadMarket).delete()
    db.commit()
    pb.init_beachhead(db, "Mysuru", "Karnataka", ["Flat"])
    pb.log_interview(db, "seller", "A", "+911", "Mysuru")
    pb.log_interview(db, "buyer", "B", "+912", "Mysuru")

    market = db.query(BeachheadMarket).filter_by(city="Mysuru").one()
    assert market.status == "PLANNING"
    assert (market.interviews_sellers, market.interviews_buyers) == (1, 1)


def test_interview_city_match_is_case_insensitive(db):
    db.query(BeachheadMarket).delete()
    db.commit()
    pb.init_beachhead(db, "Mysuru", "Karnataka", ["Flat"])
    pb.log_interview(db, "seller", "A", "+911", "  mysuru ")
    assert db.query(BeachheadMarket).filter_by(city="Mysuru").one().interviews_sellers == 1


def test_resaving_a_beachhead_does_not_flip_it_to_active(db):
    db.query(BeachheadMarket).delete()
    db.commit()
    pb.init_beachhead(db, "Mysuru", "Karnataka", ["Flat"])
    pb.init_beachhead(db, "Mysuru", "Karnataka", ["Flat", "Plot"])
    market = db.query(BeachheadMarket).filter_by(city="Mysuru").one()
    assert market.status == "PLANNING"
    assert market.property_types == ["Flat", "Plot"]


def test_revenue_model_summary_executes(clean_revenue):
    """Regression: built its total with with_entities(lambda ...), which is
    not valid SQLAlchemy and raised on every call."""
    out = pb.revenue_model_summary(clean_revenue)
    assert out["revenue_collected_inr"] == 0.0
    assert out["active_agents"] == 0


# ---- lead assignment ----

def test_lead_is_not_billed_to_an_agent_outside_their_area(clean_revenue):
    """Regression: fell back to agents[0], invoicing a broker for a locality
    they do not work."""
    db = clean_revenue
    _agent(db, name="Whitefield Only", service_areas=["Whitefield"])
    lead = _lead(db, location="Hebbal")

    out = rev.assign_lead_to_agent(db, lead.id)
    assert out["status"] == "no_agent_covers_locality"
    assert out["fee_charged_inr"] == 0.0
    assert db.query(LeadAssignment).count() == 0
    assert db.query(Payment).count() == 0


def test_residential_agent_receives_a_villa_lead(clean_revenue):
    """Regression: specialization ('residential') was compared directly to
    property_type ('Villa'), excluding every specialised agent."""
    db = clean_revenue
    _agent(db, name="Hebbal Homes", service_areas=["Hebbal"],
           specialization="residential")
    lead = _lead(db, location="Hebbal", ptype="Villa")

    out = rev.assign_lead_to_agent(db, lead.id)
    assert out["status"] == "sent_to_agent"
    assert out["agent_name"] == "Hebbal Homes"


def test_leads_spread_across_eligible_agents(clean_revenue):
    db = clean_revenue
    a = _agent(db, name="Agent One", service_areas=["Whitefield"])
    b = _agent(db, name="Agent Two", service_areas=["Whitefield"])
    first = rev.assign_lead_to_agent(db, _lead(db).id)
    second = rev.assign_lead_to_agent(db, _lead(db).id)
    assert {first["agent_id"], second["agent_id"]} == {a["id"], b["id"]}


def test_payment_row_is_linked_to_its_assignment(clean_revenue):
    """Regression: Payment was built before the assignment was flushed, so
    lead_assignment_id was NULL and the fee could never be collected."""
    db = clean_revenue
    _agent(db, name="Linked", service_areas=["Whitefield"], lead_fee_inr=750)
    out = rev.assign_lead_to_agent(db, _lead(db).id)

    payment = db.query(Payment).one()
    assert payment.lead_assignment_id == out["assignment_id"]


# ---- money lifecycle ----

def test_fee_moves_pending_to_earned_to_paid(clean_revenue):
    db = clean_revenue
    agent = _agent(db, name="Cycle", service_areas=["Whitefield"], lead_fee_inr=750)
    out = rev.assign_lead_to_agent(db, _lead(db).id)

    assert db.query(Payment).one().payment_status == "PENDING"
    rev.update_lead_status(db, out["assignment_id"], "CONVERTED")
    assert db.query(Payment).one().payment_status == "EARNED"

    settled = rev.process_payment(db, agent["id"])
    assert settled["amount_settled_inr"] == 750.0
    # Regression: the session runs autoflush=False, so this read reported 0.0.
    assert settled["total_earnings_inr"] == 750.0
    assert db.query(Payment).one().payment_status == "PAID"


def test_settling_twice_is_a_no_op(clean_revenue):
    db = clean_revenue
    agent = _agent(db, name="Twice", service_areas=["Whitefield"], lead_fee_inr=750)
    out = rev.assign_lead_to_agent(db, _lead(db).id)
    rev.update_lead_status(db, out["assignment_id"], "CONVERTED")
    rev.process_payment(db, agent["id"])

    again = rev.process_payment(db, agent["id"])
    assert again["status"] == "nothing_to_settle"
    assert again["settled_count"] == 0
    assert again["total_earnings_inr"] == 750.0   # not double counted


def test_unconverted_lead_is_never_settled(clean_revenue):
    db = clean_revenue
    agent = _agent(db, name="Unconverted", service_areas=["Whitefield"])
    rev.assign_lead_to_agent(db, _lead(db).id)
    out = rev.process_payment(db, agent["id"])
    assert out["status"] == "nothing_to_settle"
    assert db.query(Payment).one().payment_status == "PENDING"


# ---- post-sale ----

def test_season_ranges_do_not_overlap():
    """Regression: October matched both Kharif and Rabi."""
    import datetime as _dt
    from unittest import mock
    seen = {}
    for month in range(1, 13):
        with mock.patch("app.post_sale.datetime") as m:
            m.now.return_value = _dt.datetime(2026, month, 15)
            seen[month] = ps.get_current_season()
    assert seen[10] == "Rabi"
    assert seen[7] == "Kharif"
    assert seen[4] == "Summer"
    assert set(seen.values()) == {"Kharif", "Rabi", "Summer"}


def test_leased_property_still_counts_as_under_management(db):
    """Regression: status read 'no_properties' while a tenant was in place."""
    from app.models import RentalListing
    db.query(RentalListing).delete()
    db.commit()
    prop = db.query(Property).first()
    rental = ps.register_rental(db, prop.id, 45000)
    ps.match_tenant(db, rental["id"], "Meena", "+919700001111",
                    utcnow_dt(), utcnow_dt())

    summary = ps.post_sale_summary(db)
    rentals = summary["features"]["rental_management"]
    assert rentals["leased_properties"] == 1
    assert rentals["status"] == "available"
    db.query(RentalListing).delete()
    db.commit()


def utcnow_dt():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc)


# ---- build plan (playbook §8, measured from data) ----

def test_build_plan_flags_a_product_built_before_it_was_validated(db):
    """The playbook's core warning: Phase 3 done while Phase 1 is not."""
    db.query(BeachheadMarket).delete()
    db.commit()
    pb.init_beachhead(db, "Bengaluru", "Karnataka", ["Flat"])

    status = pb.build_status(db)
    phase1 = next(p for p in status["phases"] if p["number"] == 1)
    phase3 = next(p for p in status["phases"] if p["number"] == 3)

    assert phase1["done"] is False          # no interviews, no manual deals
    assert phase3["done"] is True           # seeded catalogue is past the gate
    assert status["out_of_order"] is True
    assert "Phase 1 first" in status["warning"]


def test_build_plan_clears_the_warning_once_phase_one_is_proven(db):
    db.query(BeachheadMarket).delete()
    db.commit()
    pb.init_beachhead(db, "Bengaluru", "Karnataka", ["Flat"])
    market = db.query(BeachheadMarket).one()
    market.interviews_sellers = market.target_interviews
    market.manual_pilot_closed_deals = market.target_deals
    db.commit()

    status = pb.build_status(db)
    assert next(p for p in status["phases"] if p["number"] == 1)["done"] is True
    assert status["out_of_order"] is False
    assert status["warning"] is None


def test_build_plan_states_the_revenue_model_choice(db):
    """Playbook §7.6 forces a choice; the app must name which side it took."""
    status = pb.build_status(db)
    assert status["revenue_model"]["chosen"] == "lead_generation"
    assert "better-matched agents" in status["revenue_model"]["detail"]


def test_build_plan_admits_lexical_matching_in_the_default_config(db):
    """The limits list must reflect the running config, not a fixed string."""
    titles = [t for t, _ in pb.build_status(db)["limits"]]
    assert "Matching is lexical" in titles
    assert "No money moves" in titles


# ------------------------------------------------------------ ultron (rentals)

from app import rental_agent as ultron  # noqa: E402


@pytest.mark.parametrize("said,expected", [
    ("unfurnished", "Unfurnished"),
    ("un furnished", "Unfurnished"),
    ("no furniture", "Unfurnished"),
    ("bare shell", "Unfurnished"),
    ("semi furnished", "Semi-furnished"),
    ("semi-furnished", "Semi-furnished"),
    ("fully furnished", "Furnished"),
    ("furnished", "Furnished"),
])
def test_furnishing_is_read_specific_first(said, expected):
    """Regression: 'unfurnished' and 'semi furnished' both CONTAIN 'furnished',
    so a substring scan in the wrong order listed them as Furnished."""
    assert ultron.extract(said, {}).get("furnishing") == expected


@pytest.mark.parametrize("said,months", [
    ("ten months", 10), ("two months", 2), ("2 months", 2),
    ("eleven months", 11), ("1 year", 12), ("2 years", 24),
])
def test_month_counts_parse_as_words_and_digits(said, months):
    """Regression: the regex only accepted digits, so 'ten months' fell
    through and the NEXT answer was eaten as the deposit."""
    draft = ultron.extract(said, {"monthly_rent_inr": 10000.0}, stage="ASK_TERM")
    assert draft.get("lease_months") == months


def test_deposit_quoted_in_months_becomes_an_amount():
    draft = ultron.extract("ten months", {"monthly_rent_inr": 45000.0},
                           stage="ASK_DEPOSIT")
    assert draft["deposit_inr"] == 450000.0
    assert draft["deposit_quoted_months"] == 10


def test_deposit_over_the_statutory_cap_is_flagged():
    notes = ultron.compliance({"monthly_rent_inr": 45000.0,
                               "deposit_inr": 450000.0})
    codes = [n["code"] for n in notes]
    assert "DEPOSIT_OVER_CAP" in codes
    assert next(n for n in notes if n["code"] == "DEPOSIT_OVER_CAP")["severity"] == "high"


def test_deposit_within_the_cap_is_not_flagged():
    notes = ultron.compliance({"monthly_rent_inr": 45000.0, "deposit_inr": 90000.0})
    assert [n["code"] for n in notes] == []


def test_commercial_lettings_get_the_higher_deposit_cap():
    """Residential is capped at two months, commercial at six."""
    terms = {"monthly_rent_inr": 100000.0, "deposit_inr": 500000.0}
    assert ultron.compliance({**terms, "property_type": "Commercial"}) == []
    assert ultron.compliance({**terms, "property_type": "Flat"})


def test_lease_of_twelve_months_or_more_is_flagged_for_registration():
    assert [n["code"] for n in ultron.compliance({"lease_months": 11})] == []
    assert "LEASE_NEEDS_REGISTRATION" in [
        n["code"] for n in ultron.compliance({"lease_months": 12})]


def test_rental_intake_creates_a_letting_not_a_sale_listing(db):
    from app.models import RentalListing
    draft = {"property_type": "Flat", "config": "2BHK", "locality": "Indiranagar",
             "area_sqft": 1200.0, "monthly_rent_inr": 45000.0,
             "deposit_inr": 90000.0, "furnishing": "Semi-furnished",
             "lease_months": 11}
    out = ultron.handle(db, {"stage": "CONFIRM", "draft": draft}, "yes")

    assert out["stage"] == "DONE"
    prop = db.query(Property).filter_by(id=out["created"]["property"]["id"]).one()
    assert prop.listing_type == "RENT"
    assert prop.price_inr is None          # a letting has no asking price
    assert prop.is_verified is False
    rental = db.query(RentalListing).filter_by(id=out["created"]["rental_id"]).one()
    assert rental.monthly_rent_inr == 45000.0
    assert rental.lease_terms == "11 months"


def test_a_letting_never_appears_in_buyer_search(db):
    """Buyer search sells things. A rental must not surface there even if it
    were somehow verified."""
    draft = {"property_type": "Flat", "config": "3BHK", "locality": "Whitefield",
             "area_sqft": 1500.0, "monthly_rent_inr": 60000.0,
             "deposit_inr": 120000.0, "furnishing": "Furnished", "lease_months": 11}
    out = ultron.handle(db, {"stage": "CONFIRM", "draft": draft}, "yes")
    prop = db.query(Property).filter_by(id=out["created"]["property"]["id"]).one()
    prop.is_verified = True                # force the worst case
    db.commit()

    found = search(db, "3BHK flat in Whitefield", limit=25)
    assert all(m["id"] != prop.id for m in found["matches"])


def test_ultron_asks_for_the_first_missing_field_only(db):
    out = ultron.handle(db, {"stage": None, "draft": {}}, "hello")
    assert out["stage"] == "ASK_WHAT"
    out = ultron.handle(db, {"stage": out["stage"], "draft": out["draft"]},
                        "a 2bhk apartment")
    assert out["stage"] == "ASK_WHERE"
    assert out["draft"]["property_type"] == "Flat"
    assert out["draft"]["config"] == "2BHK"


# ------------------------------------------------------------- tenant safety

from app import tenant_safety as ts  # noqa: E402


def test_deposit_over_cap_names_the_contestable_excess():
    note = ts.deposit_note(45000.0, 450000.0)
    assert note["severity"] == "high"
    assert "90,000" in note["detail"]      # the lawful two months
    assert "360,000" in note["detail"]     # the excess a tenant can contest


def test_deposit_within_cap_is_silent():
    assert ts.deposit_note(45000.0, 90000.0) is None


def test_commercial_deposit_uses_the_six_month_cap():
    assert ts.deposit_note(100000.0, 500000.0, commercial=True) is None
    assert ts.deposit_note(100000.0, 500000.0, commercial=False) is not None


def test_deposit_note_needs_both_numbers():
    assert ts.deposit_note(None, 450000.0) is None
    assert ts.deposit_note(45000.0, None) is None


@pytest.mark.parametrize("line,concern", [
    ("owner is abroad so send the deposit", "Absent-owner story"),
    ("pay to block it today", "block"),
    ("cash only please", "Cash-only"),
    ("we can do agreement later", "Deferring"),
    ("10 months deposit standard here", "statutory cap"),
])
def test_rental_pressure_lines_are_flagged(line, concern):
    hits = ts.scan_message(line)
    assert hits, f"nothing flagged in {line!r}"
    assert any(concern.lower() in h["concern"].lower() for h in hits)


def test_a_reasonable_landlord_message_is_not_flagged():
    clean = ("Hello, the flat is free from the 1st. Happy to share the khata "
             "copy and sign a registered agreement.")
    assert ts.scan_message(clean) == []


def test_unverified_landlord_means_do_not_pay():
    brief = ts.brief({"monthly_rent_inr": 45000.0, "deposit_inr": 90000.0,
                      "lease_terms": "11 months"},
                     {"is_verified": False})
    assert brief["stance"] == "DO_NOT_PAY"
    assert any("owns the property" in w["title"] for w in brief["warnings"])


def test_clean_letting_still_carries_the_deposit_rules():
    """A tenant with nothing flagged must still be told the money rules."""
    brief = ts.brief({"monthly_rent_inr": 45000.0, "deposit_inr": 90000.0,
                      "lease_terms": "11 months", "furnishing": "Unfurnished"},
                     {"is_verified": True})
    assert brief["stance"] == "PROCEED_WITH_NORMAL_CARE"
    assert brief["warnings"] == []
    assert len(brief["money_rules"]) == 6
    assert len(brief["your_rights"]) == 7


def test_long_lease_is_raised_to_the_tenant_too():
    brief = ts.brief({"monthly_rent_inr": 45000.0, "deposit_inr": 90000.0,
                      "lease_terms": "24 months"}, {"is_verified": True})
    assert brief["stance"] == "PROCEED_WITH_CAUTION"
    assert any("registered" in w["title"] for w in brief["warnings"])


def test_deposit_is_persisted_so_the_tenant_brief_can_check_it(db):
    """Regression: Ultron collected and flagged the deposit, then dropped it —
    RentalListing had no column, so the tenant brief had nothing to warn on."""
    from app.models import RentalListing
    draft = {"property_type": "Flat", "config": "2BHK", "locality": "Indiranagar",
             "area_sqft": 1200.0, "monthly_rent_inr": 45000.0,
             "deposit_inr": 450000.0, "furnishing": "Semi-furnished",
             "lease_months": 11}
    out = ultron.handle(db, {"stage": "CONFIRM", "draft": draft}, "yes")
    rental = db.query(RentalListing).filter_by(id=out["created"]["rental_id"]).one()
    assert rental.deposit_inr == 450000.0

    brief = ts.brief({"monthly_rent_inr": rental.monthly_rent_inr,
                      "deposit_inr": rental.deposit_inr,
                      "lease_terms": rental.lease_terms}, {"is_verified": False})
    assert any(w["title"].startswith("Deposit is") for w in brief["warnings"])


def test_tenant_safety_endpoints_are_public():
    """A tenant must not need a staff key to be warned."""
    assert client.get("/api/v1/tenant/rules").status_code == 200
    assert client.post("/api/v1/tenant/scan",
                       json={"text": "cash only"}).status_code == 200


# ------------------------------------------------- valuation & renovation

from app import renovation as reno  # noqa: E402
from app import valuation as val    # noqa: E402


def test_b_khata_is_discounted_because_banks_will_not_lend(db):
    b = db.query(Property).filter(Property.khata.like("B%")).first()
    assert b is not None, "seed should carry a B-Khata listing"
    out = val.value(db, b.id)
    khata = [a for a in out["adjustments"] if "Khata" in a["factor"]]
    assert khata, "B-Khata must be priced, not just labelled"
    assert khata[0]["percent"] < 0
    assert khata[0]["amount_inr"] < 0


def test_a_khata_carries_no_title_discount(db):
    a = db.query(Property).filter(Property.khata == "A-Khata",
                                  Property.is_verified.is_(True)).first()
    out = val.value(db, a.id)
    assert not [x for x in out["adjustments"] if "Khata" in x["factor"]]


def test_under_construction_is_discounted(db):
    u = db.query(Property).filter(Property.possession == "Under construction").first()
    out = val.value(db, u.id)
    assert [x for x in out["adjustments"] if "construction" in x["factor"].lower()]


def test_confidence_follows_the_evidence_not_the_arithmetic(db):
    """A price from one distant comparable must not present as high confidence."""
    for prop in db.query(Property).filter(Property.listing_type != "RENT").all():
        out = val.value(db, prop.id)
        if out.get("estimate_inr") is None:
            continue
        if out["same_locality_count"] == 0:
            assert out["confidence"] == "low"
        elif out["same_locality_count"] >= 3:
            assert out["confidence"] == "high"


def test_valuation_always_declares_itself_an_estimate(db):
    out = val.value(db, db.query(Property).first().id)
    assert out["is_estimate"] is True
    assert "not a valuation report" in out["basis"] or "unadjusted" in out["basis"]


def test_valuation_of_a_missing_property_raises(db):
    with pytest.raises(ValueError):
        val.value(db, 999999)


def test_sunlight_is_computed_across_the_whole_year():
    east = val.sunlight_profile("East", 12.97)
    assert east["annual_direct_sun_hours"] > 0
    # Bengaluru at 13°N: day length varies, but never wildly.
    assert 11.0 < east["summer_daylight_hours"] < 13.5
    assert 10.5 < east["winter_daylight_hours"] < 12.5
    assert east["summer_daylight_hours"] > east["winter_daylight_hours"]


def test_east_and_west_receive_the_same_sun_because_the_sky_is_symmetric():
    """The earlier model asserted west > east, which is physically false — the
    sun's path is symmetric about noon, so the two facades get the same hours.
    What differs is comfort: west takes it as afternoon heat. That belongs in
    the note, not in the hour count."""
    h = {f: val.sunlight_profile(f)["annual_direct_sun_hours"]
         for f in ("North", "East", "West", "South")}
    assert abs(h["East"] - h["West"]) / h["East"] < 0.02
    assert h["North"] < h["East"]
    assert h["South"] > h["East"]
    assert "afternoon" in val.sunlight_profile("West")["note"].lower()


def test_latitude_actually_changes_the_answer():
    """Regression: annual hours were mean-day-length × a fixed per-facing
    fraction. Mean day length over a year is ~12h at EVERY latitude, so that
    cancelled latitude out and reported identical sun for Delhi and Chennai."""
    chennai_n = val.sunlight_profile("North", 13.08)["annual_direct_sun_hours"]
    delhi_n = val.sunlight_profile("North", 28.61)["annual_direct_sun_hours"]
    chennai_s = val.sunlight_profile("South", 13.08)["annual_direct_sun_hours"]
    delhi_s = val.sunlight_profile("South", 28.61)["annual_direct_sun_hours"]

    # Further north: the sun sits south for more of the year.
    assert delhi_s > chennai_s * 1.2
    # And a north facade near the tropic catches sun Delhi's never does.
    assert chennai_n > delhi_n * 1.5


def test_sunlight_handles_an_unstated_facing():
    out = val.sunlight_profile(None)
    assert out["facing"] == "Not stated"
    assert out["annual_direct_sun_hours"] > 0


# ---- renovation ----

def test_cosmetic_work_is_not_pretended_to_pay_for_itself():
    out = reno.estimate(["false_ceiling"], 1500)
    assert out["pays_for_itself"] is False
    assert out["net_inr"] < 0
    assert "not" in out["recommendation"].lower() or "live with" in out["recommendation"]


def test_cleaning_is_reported_as_the_best_return():
    out = reno.estimate(["deep_clean", "kitchen", "false_ceiling"], 1500)
    assert out["lines"][0]["scope"] == "deep_clean"   # sorted by net gain
    assert out["lines"][0]["net_inr"] > 0


def test_kitchens_and_bathrooms_cost_on_their_own_footprint():
    """Costing a modular kitchen across the whole flat's area would be absurd."""
    out = reno.estimate(["kitchen"], 1500)
    assert out["lines"][0]["treated_sqft"] < 1500 * 0.2


def test_grade_moves_the_number_in_the_right_direction():
    low = reno.estimate(["kitchen"], 1500, "budget")["total_cost_inr"]
    mid = reno.estimate(["kitchen"], 1500, "standard")["total_cost_inr"]
    high = reno.estimate(["kitchen"], 1500, "premium")["total_cost_inr"]
    assert low < mid < high


def test_schedule_overlaps_trades_rather_than_summing_them():
    one = reno.estimate(["kitchen"], 1500)["estimated_weeks"]
    many = reno.estimate(["kitchen", "paint", "deep_clean", "flooring"], 1500)["estimated_weeks"]
    assert many < one * 4


@pytest.mark.parametrize("bad,kwargs", [
    ("nonsense_scope", {"area_sqft": 1000}),
])
def test_unknown_scope_is_refused_with_the_valid_list(bad, kwargs):
    with pytest.raises(ValueError) as e:
        reno.estimate([bad], **kwargs)
    assert "kitchen" in str(e.value)


def test_zero_area_is_refused():
    with pytest.raises(ValueError):
        reno.estimate(["paint"], 0)


def test_empty_scope_list_is_refused():
    with pytest.raises(ValueError):
        reno.estimate([], 1000)


def test_staging_never_claims_an_image_it_did_not_make(monkeypatch):
    """Returning the input photo as a 'staged' result would mislead a seller."""
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = reno.stage("living", "minimal", b"\xff\xd8\xffnot-really-a-jpeg")
    assert out["rendered"] is False
    assert out["image"] is None
    assert "nothing was generated" in out["status"]


def test_staging_always_carries_the_disclosure_duty():
    out = reno.stage("bedroom", "scandinavian")
    assert "virtually staged" in out["honesty_note"]


def test_staging_rejects_an_unknown_room_or_style():
    with pytest.raises(ValueError):
        reno.stage("dungeon", "minimal")
    with pytest.raises(ValueError):
        reno.stage("living", "baroque")


def test_staging_costs_the_work_when_given_an_area():
    out = reno.stage("kitchen", "modern" if False else "minimal", area_sqft=1200)
    assert out["costing"] is not None
    assert out["costing"]["total_cost_inr"] > 0


def test_valuation_and_renovation_endpoints_are_public():
    assert client.get("/api/v1/properties/1/valuation").status_code == 200
    assert client.get("/api/v1/sunlight?facing=East").status_code == 200
    assert client.get("/api/v1/renovation/scopes").status_code == 200
    r = client.post("/api/v1/renovation/estimate"
                    "?scopes=paint&area_sqft=1000&grade=standard")
    assert r.status_code == 200


def test_staging_prompt_names_the_room_not_a_person():
    """Regression: 'an Indian living in a contemporary style' read as a person."""
    out = reno.stage("living", "contemporary")
    assert "Indian living room" in out["prompt"]
    for room in reno.ROOMS:
        assert " in a " in reno.stage(room, "minimal")["prompt"]


# ---------------------------------------------------- the autonomous agent

import datetime as _dtm  # noqa: E402

from app import consent as consent_mod   # noqa: E402
from app import watchtower as wt         # noqa: E402
from app.models import (AgentRun, Consent, Notification,  # noqa: E402
                        Watch, as_utc)

IST = consent_mod.IST

# 11:00 IST on a fixed date. Consent tests that are not ABOUT quiet hours must
# pin the moment, or the suite passes by day and fails at night — which is how
# a test suite loses its authority.
DAYTIME = _dtm.datetime(2026, 9, 5, 11, 0, tzinfo=IST)



@pytest.fixture()
def agent_db(db):
    """Agent tables emptied around each test so counts are deterministic."""
    for model in (Notification, Watch, Consent, AgentRun):
        db.query(model).delete()
    db.commit()
    yield db
    for model in (Notification, Watch, Consent, AgentRun):
        db.query(model).delete()
    db.commit()


def _prop(db, **kw):
    p = db.query(Property).filter(Property.listing_type != "RENT").first()
    for k, v in kw.items():
        setattr(p, k, v)
    db.commit()
    return p


# ---- permission: every one of these must fail closed ----

def test_no_consent_row_means_no_send(agent_db):
    allowed, reason = consent_mod.allow(agent_db, "+919000000001", "whatsapp")
    assert allowed is False
    assert "no consent" in reason


def test_revoked_consent_stops_sending(agent_db):
    consent_mod.grant(agent_db, "+919000000002", "whatsapp", "alerts")
    assert consent_mod.allow(agent_db, "+919000000002", "whatsapp", DAYTIME)[0] is True
    consent_mod.revoke(agent_db, "+919000000002")
    allowed, reason = consent_mod.allow(agent_db, "+919000000002", "whatsapp", DAYTIME)
    assert allowed is False
    assert reason == "consent withdrawn"


def test_revocation_is_recorded_not_deleted(agent_db):
    """We must be able to prove WHEN someone opted out."""
    consent_mod.grant(agent_db, "+919000000003", "whatsapp", "alerts")
    consent_mod.revoke(agent_db, "+919000000003")
    row = agent_db.query(Consent).filter_by(subject="+919000000003").one()
    assert row.granted is False
    assert row.revoked_at is not None


def test_quiet_hours_block_the_middle_of_the_night(agent_db):
    consent_mod.grant(agent_db, "+919000000004", "whatsapp", "alerts",
                      quiet_from_hour=21, quiet_to_hour=8)
    night = _dtm.datetime(2026, 9, 5, 3, 0, tzinfo=IST)
    day = _dtm.datetime(2026, 9, 5, 11, 0, tzinfo=IST)
    assert consent_mod.allow(agent_db, "+919000000004", "whatsapp", night)[0] is False
    assert consent_mod.allow(agent_db, "+919000000004", "whatsapp", day)[0] is True


def test_quiet_window_wraps_past_midnight(agent_db):
    """21:00–08:00 spans midnight; a naive start<=h<end test gets this wrong."""
    row = consent_mod.grant(agent_db, "+919000000005", "whatsapp", "alerts",
                            quiet_from_hour=21, quiet_to_hour=8)
    for hour, quiet in ((22, True), (2, True), (7, True), (9, False), (20, False)):
        at = _dtm.datetime(2026, 9, 5, hour, tzinfo=IST)
        assert consent_mod.in_quiet_hours(row, at) is quiet, f"hour {hour}"


def test_daily_cap_stops_a_runaway_rule(agent_db):
    consent_mod.grant(agent_db, "+919000000006", "whatsapp", "alerts", max_per_day=2)
    for i in range(2):
        agent_db.add(Notification(subject="+919000000006", channel="whatsapp",
                                  status="SENT", fingerprint=f"cap-test-{i}"))
    agent_db.commit()
    allowed, reason = consent_mod.allow(agent_db, "+919000000006", "whatsapp", DAYTIME)
    assert allowed is False
    assert "daily cap" in reason


def test_in_app_needs_no_consent_because_it_interrupts_nobody(agent_db):
    assert consent_mod.allow(agent_db, "+919000000007", "inapp")[0] is True


# ---- the loop ----

def test_first_sight_is_a_baseline_not_an_alert(agent_db):
    """Creating a watch must not immediately fire on the current state."""
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    wt.create_watch(agent_db, "+919000000010", "VERIFICATION_CHANGE",
                    {"property_id": prop.id})
    out = wt.tick(agent_db)
    assert out["events_found"] == 0


def test_a_failed_title_check_reaches_the_buyer_as_urgent(agent_db):
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    wt.create_watch(agent_db, "+919000000011", "VERIFICATION_CHANGE",
                    {"property_id": prop.id})
    wt.tick(agent_db)                                   # baseline
    _prop(agent_db, is_verified=False, risk_status="FLAGGED")

    out = wt.tick(agent_db)
    assert out["events_found"] == 1
    note = agent_db.query(Notification).filter_by(subject="+919000000011").one()
    assert note.severity == "urgent"
    assert "Do not pay" in note.body


def test_the_same_event_is_never_delivered_twice(agent_db):
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    wt.create_watch(agent_db, "+919000000012", "VERIFICATION_CHANGE",
                    {"property_id": prop.id})
    wt.tick(agent_db)
    _prop(agent_db, is_verified=False, risk_status="FLAGGED")
    assert wt.tick(agent_db)["events_found"] == 1
    for _ in range(5):
        assert wt.tick(agent_db)["events_found"] == 0
    assert agent_db.query(Notification).filter_by(subject="+919000000012").count() == 1


def test_a_suppressed_alert_records_why(agent_db):
    """Six months later, 'why did this not send' must be answerable."""
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    wt.create_watch(agent_db, "+919000000013", "VERIFICATION_CHANGE",
                    {"property_id": prop.id}, channel="whatsapp")
    wt.tick(agent_db)
    _prop(agent_db, is_verified=False, risk_status="FLAGGED")
    wt.tick(agent_db)

    note = agent_db.query(Notification).filter_by(subject="+919000000013").one()
    assert note.status == "SUPPRESSED"
    assert note.suppressed_reason == "no consent on file for this channel"


def test_one_broken_watch_cannot_stop_everyone_elses(agent_db):
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    broken = wt.create_watch(agent_db, "+919000000014", "VERIFICATION_CHANGE",
                             {"property_id": prop.id})
    broken.kind = "NOT_A_REAL_KIND"          # corrupted after creation
    good = wt.create_watch(agent_db, "+919000000015", "VERIFICATION_CHANGE",
                           {"property_id": prop.id})
    agent_db.commit()

    wt.tick(agent_db)
    _prop(agent_db, is_verified=False, risk_status="FLAGGED")
    out = wt.tick(agent_db)

    assert out["errors"], "the broken watch should be reported"
    assert out["events_found"] == 1, "the healthy watch must still fire"
    assert agent_db.query(Notification).filter_by(subject="+919000000015").count() == 1


def test_a_watch_created_for_an_unknown_kind_is_refused(agent_db):
    with pytest.raises(ValueError):
        wt.create_watch(agent_db, "+919000000016", "TELEPATHY", {})


def test_watches_needing_a_property_say_so(agent_db):
    with pytest.raises(ValueError) as e:
        wt.create_watch(agent_db, "+919000000017", "PRICE_DROP", {})
    assert "property_id" in str(e.value)


def test_price_drop_only_fires_downward(agent_db):
    prop = _prop(agent_db, price_inr=10_000_000)
    wt.create_watch(agent_db, "+919000000018", "PRICE_DROP",
                    {"property_id": prop.id})
    wt.tick(agent_db)                                   # baseline
    _prop(agent_db, price_inr=12_000_000)               # a RISE
    assert wt.tick(agent_db)["events_found"] == 0
    _prop(agent_db, price_inr=9_000_000)                # now a drop
    assert wt.tick(agent_db)["events_found"] == 1


def test_deposit_watch_flags_an_over_cap_letting(agent_db):
    from app.models import RentalListing
    agent_db.query(RentalListing).delete()
    agent_db.commit()
    prop = agent_db.query(Property).first()
    agent_db.add(RentalListing(property_id=prop.id, monthly_rent_inr=45000.0,
                               deposit_inr=450000.0, status="ACTIVE",
                               lease_terms="11 months"))
    agent_db.commit()

    wt.create_watch(agent_db, "+919000000019", "DEPOSIT_RISK", {})
    out = wt.tick(agent_db)
    assert out["events_found"] == 1
    note = agent_db.query(Notification).filter_by(subject="+919000000019").one()
    assert "contestable" in note.body
    agent_db.query(RentalListing).delete()
    agent_db.commit()


def test_agent_status_survives_a_naive_timestamp_from_sqlite(agent_db):
    """Regression: SQLite hands back naive datetimes even for
    DateTime(timezone=True), and subtracting one from an aware utcnow()
    raised TypeError — which broke the whole status endpoint."""
    wt.tick(agent_db)
    status = wt.agent_status(agent_db)          # must not raise
    assert status["last_run"] is not None
    assert isinstance(status["running"], bool)


def test_as_utc_makes_a_naive_stamp_comparable():
    naive = _dtm.datetime(2026, 9, 5, 12, 0)
    assert as_utc(naive).tzinfo is not None
    assert (utcnow_dt() - as_utc(naive)).total_seconds() != 0    # no raise
    aware = _dtm.datetime(2026, 9, 5, 12, 0, tzinfo=_dtm.timezone.utc)
    assert as_utc(aware) == aware
    assert as_utc(None) is None


def test_agent_endpoints_respect_staff_boundary():
    """Status is public; forcing a tick is not."""
    assert client.get("/api/v1/agent/status").status_code == 200
    assert client.post("/api/v1/agent/tick").status_code in (200, 401, 403)


# ------------------------------------------------------- STOP / START keywords

@pytest.mark.parametrize("said", [
    "STOP", "stop", "stop.", "  Stop!  ", "STOP ALL", "stopall",
    "unsubscribe", "unsub", "cancel", "quit", "opt out", "optout",
    "remove me", "delete me", "band karo", "roko", "hatao", "no more",
])
def test_opt_out_keywords_are_recognised(said):
    assert consent_mod.keyword_intent(said) == "OPT_OUT"


@pytest.mark.parametrize("said", [
    "start", "START", "resume", "subscribe", "unstop", "chalu karo", "shuru",
])
def test_opt_in_keywords_are_recognised(said):
    assert consent_mod.keyword_intent(said) == "OPT_IN"


@pytest.mark.parametrize("said", [
    "a flat near the bus stop",
    "stop by the office tomorrow",
    "non-stop water supply in this block",
    "I want to start looking for a 3BHK",
    "can you stop sending after 9pm",
    "is there a bus stop nearby",
    "when does construction start",
    "",
    "   ",
])
def test_a_sentence_containing_stop_does_not_unsubscribe_anyone(said):
    """The failure this prevents is silent and unrecoverable: someone asks
    about a flat near a bus stop, is unsubscribed, and never hears from you
    again without ever knowing why."""
    assert consent_mod.keyword_intent(said) is None


def test_stop_revokes_only_the_channel_it_was_sent_on(agent_db):
    """In-app alerts wait to be read and interrupt nobody, so STOP on
    WhatsApp must not silently take those away too."""
    consent_mod.grant(agent_db, "+919000000030", "whatsapp", "alerts")
    consent_mod.grant(agent_db, "+919000000030", "email", "alerts")

    out = consent_mod.handle_keyword(agent_db, "+919000000030", "STOP", "whatsapp")
    assert out["intent"] == "OPT_OUT"
    assert out["revoked"] == 1

    assert consent_mod.allow(agent_db, "+919000000030", "whatsapp", DAYTIME)[0] is False
    assert consent_mod.allow(agent_db, "+919000000030", "email", DAYTIME)[0] is True
    assert consent_mod.allow(agent_db, "+919000000030", "inapp", DAYTIME)[0] is True


def test_stop_is_confirmed_so_the_person_knows_it_worked(agent_db):
    consent_mod.grant(agent_db, "+919000000031", "whatsapp", "alerts")
    out = consent_mod.handle_keyword(agent_db, "+919000000031", "stop", "whatsapp")
    assert "unsubscribed" in out["reply"].lower()
    assert "START" in out["reply"]          # tells them how to come back


def test_the_agent_stops_sending_the_moment_stop_arrives(agent_db):
    """The whole point: revocation must reach the autonomous loop."""
    prop = _prop(agent_db, is_verified=True, risk_status="ADVOCATE_VERIFIED")
    consent_mod.grant(agent_db, "+919000000032", "whatsapp", "alerts")
    wt.create_watch(agent_db, "+919000000032", "VERIFICATION_CHANGE",
                    {"property_id": prop.id}, channel="whatsapp")
    wt.tick(agent_db)                                     # baseline

    consent_mod.handle_keyword(agent_db, "+919000000032", "STOP", "whatsapp")
    _prop(agent_db, is_verified=False, risk_status="FLAGGED")
    out = wt.tick(agent_db)

    assert out["notifications_sent"] == 0
    assert out["notifications_suppressed"] == 1
    note = (agent_db.query(Notification)
            .filter_by(subject="+919000000032").one())
    assert note.status == "SUPPRESSED"
    assert note.suppressed_reason == "consent withdrawn"


def test_start_from_someone_who_never_opted_out_is_left_alone(agent_db):
    """'start' is an ordinary English word. Hijacking it would break the
    conversation for anyone who opens with 'start looking for a flat'."""
    assert consent_mod.handle_keyword(agent_db, "+919000000033", "start",
                                      "whatsapp") is None

    consent_mod.grant(agent_db, "+919000000034", "whatsapp", "alerts")
    assert consent_mod.handle_keyword(agent_db, "+919000000034", "start",
                                      "whatsapp") is None


def test_start_restores_someone_who_did_opt_out(agent_db):
    consent_mod.grant(agent_db, "+919000000035", "whatsapp", "alerts",
                      quiet_from_hour=22, quiet_to_hour=7, max_per_day=3)
    consent_mod.handle_keyword(agent_db, "+919000000035", "STOP", "whatsapp")
    assert consent_mod.allow(agent_db, "+919000000035", "whatsapp", DAYTIME)[0] is False

    out = consent_mod.handle_keyword(agent_db, "+919000000035", "START", "whatsapp")
    assert out["intent"] == "OPT_IN"
    assert consent_mod.allow(agent_db, "+919000000035", "whatsapp", DAYTIME)[0] is True

    # Their original preferences survive the round trip.
    row = agent_db.query(Consent).filter_by(subject="+919000000035").one()
    assert (row.quiet_from_hour, row.quiet_to_hour, row.max_per_day) == (22, 7, 3)


def test_ordinary_traffic_falls_through_to_the_conversational_agent(agent_db):
    assert consent_mod.handle_keyword(
        agent_db, "+919000000036", "3BHK in Whitefield under 1.3 crore",
        "whatsapp") is None


def test_chat_endpoint_honours_stop_without_meta_in_the_loop():
    """The webhook and the local harness must not drift apart."""
    client.post("/api/v1/consent/grant",
                params={"subject": "+919000000037", "channel": "whatsapp",
                        "purpose": "alerts"})
    d = client.post("/api/v1/chat",
                    json={"phone": "+919000000037", "text": "STOP"}).json()
    assert d["intent"] == "OPT_OUT"

    status = client.get("/api/v1/consent/status",
                        params={"subject": "+919000000037"}).json()
    wa_row = [c for c in status["channels"] if c["channel"] == "whatsapp"][0]
    assert wa_row["granted"] is False


# ------------------------------------------- affordability & lending reality

from app import affordability as afford  # noqa: E402


def test_the_price_is_never_the_price():
    """Stamp duty, cess, surcharge and registration are unavoidable and are
    the thing buyers fail to budget for."""
    out = afford.acquisition_cost(12_400_000, "Ready to move")
    assert out["extras_inr"] > 700_000
    assert 6.0 < out["extras_percent"] < 9.0
    assert out["total_inr"] > out["price_inr"]
    items = " ".join(l["item"] for l in out["lines"])
    for expected in ("Stamp duty", "Cess", "Surcharge", "Registration"):
        assert expected in items


def test_cess_and_surcharge_are_charged_on_the_duty_not_the_property():
    """Charging them on the property value overstates the bill several times
    over — a mistake that is easy to make and hard to spot."""
    price = 10_000_000
    out = afford.acquisition_cost(price, "Ready to move")
    duty = next(l for l in out["lines"] if l["item"].startswith("Stamp duty"))
    cess = next(l for l in out["lines"] if l["item"].startswith("Cess"))
    assert cess["amount_inr"] == pytest.approx(duty["amount_inr"] * 0.10, rel=0.01)


@pytest.mark.parametrize("price,rate", [
    (1_500_000, 0.02), (3_000_000, 0.03), (9_000_000, 0.05),
])
def test_stamp_duty_is_banded(price, rate):
    assert afford.stamp_duty_rate(price) == rate


def test_gst_applies_only_while_under_construction():
    ready = afford.acquisition_cost(9_000_000, "Ready to move")
    building = afford.acquisition_cost(9_000_000, "Under construction")
    assert not [l for l in ready["lines"] if "GST" in l["item"]]
    assert [l for l in building["lines"] if "GST" in l["item"]]
    assert building["extras_inr"] > ready["extras_inr"]


def test_b_khata_is_reported_as_a_refusal_not_a_worse_rate(agent_db):
    """The most expensive surprise in the Indian market: a buyer arranges 80%
    financing and finds out at sanction that no bank will touch B-Khata."""
    out = afford.loan_eligibility(6_000_000, 200_000, khata="B-Khata")
    assert out["fundable"] is False
    assert out["max_loan_inr"] == 0
    codes = [b["code"] for b in out["blockers"]]
    assert "B_KHATA_NOT_FUNDABLE" in codes
    assert "cash" in out["blockers"][0]["detail"].lower()


def test_a_khata_is_fundable():
    out = afford.loan_eligibility(6_000_000, 200_000, khata="A-Khata")
    assert out["fundable"] is True
    assert out["max_loan_inr"] > 0


def test_the_binding_constraint_is_named(agent_db):
    """A buyer needs to know WHICH limit stopped them — earn more, or look
    cheaper — and those are different actions."""
    poor = afford.loan_eligibility(20_000_000, 60_000)
    assert poor["binding_constraint"] == "your income"
    rich = afford.loan_eligibility(2_000_000, 500_000)
    assert rich["binding_constraint"] == "the property value"


def test_existing_emi_reduces_what_you_can_borrow():
    clear = afford.loan_eligibility(10_000_000, 150_000, existing_emi_inr=0)
    loaded = afford.loan_eligibility(10_000_000, 150_000, existing_emi_inr=40_000)
    assert loaded["max_loan_inr"] < clear["max_loan_inr"]


def test_emi_matches_the_standard_amortisation_formula():
    # ₹50L at 8.6% over 20 years is about ₹43,700 a month.
    assert afford.emi(5_000_000, 0.086, 20) == pytest.approx(43_700, rel=0.02)
    assert afford.emi(0, 0.086, 20) == 0.0


def test_a_zero_interest_loan_does_not_divide_by_zero():
    assert afford.emi(1_200_000, 0.0, 10) == pytest.approx(10_000, rel=0.001)


def test_under_construction_warns_about_paying_rent_and_pre_emi_together():
    out = afford.loan_eligibility(9_000_000, 200_000, possession="Under construction")
    assert "PRE_EMI_WHILE_BUILDING" in [b["code"] for b in out["blockers"]]


def test_no_bank_lends_against_stamp_duty(agent_db):
    """The gap that catches people: the loan covers the price, never the duties."""
    out = afford.affordability(agent_db, 1, monthly_income_inr=200_000)
    assert out["cash_needed_inr"] == (out["loan"]["down_payment_inr"]
                                      + out["acquisition"]["extras_inr"])
    assert "No bank lends against stamp duty" in out["cash_note"]


def test_affordability_refuses_a_listing_with_no_price(agent_db):
    letting = (agent_db.query(Property)
               .filter(Property.listing_type == "RENT").first())
    if letting is None:
        pytest.skip("no letting in the catalogue")
    with pytest.raises(ValueError) as e:
        afford.affordability(agent_db, letting.id)
    assert "no asking price" in str(e.value).lower()


def test_rates_carry_the_date_they_were_set(agent_db):
    """A stale rate presented as fact is worse than no rate."""
    out = afford.acquisition_cost(9_000_000)
    assert out["rates_as_of"] == afford.RATES_AS_OF
    assert "confirm current rates" in out["disclaimer"].lower()


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError):
        afford.acquisition_cost(0)
    with pytest.raises(ValueError):
        afford.loan_eligibility(5_000_000, 0)
    with pytest.raises(ValueError):
        afford.loan_eligibility(5_000_000, 100_000, years=0)


# ---- yield ----

def test_yield_is_computed_on_the_all_in_cost_not_the_sticker(agent_db):
    """Duties are real money you never get back, so a yield that ignores them
    flatters the investment."""
    from app.models import RentalListing
    prop = (agent_db.query(Property)
            .filter(Property.listing_type != "RENT",
                    Property.price_inr.isnot(None)).first())
    agent_db.query(RentalListing).filter_by(property_id=prop.id).delete()
    agent_db.add(RentalListing(property_id=prop.id, monthly_rent_inr=50_000,
                               status="ACTIVE", lease_terms="11 months"))
    agent_db.commit()

    out = afford.rental_yield(agent_db, prop.id)
    assert out["net_yield_percent"] < out["gross_yield_percent"]
    assert out["all_in_cost_inr"] > out["price_inr"]
    assert out["rent_is_estimate"] is False
    agent_db.query(RentalListing).filter_by(property_id=prop.id).delete()
    agent_db.commit()


def test_yield_estimates_rent_from_comparables_for_a_sale_listing(agent_db):
    """The realistic case: an investor looks at a SALE listing, which has no
    letting of its own. Without this the feature could never fire."""
    from app.models import RentalListing
    sale = (agent_db.query(Property)
            .filter(Property.listing_type != "RENT",
                    Property.price_inr.isnot(None),
                    Property.area_sqft.isnot(None)).first())
    other = (agent_db.query(Property)
             .filter(Property.id != sale.id,
                     Property.area_sqft.isnot(None)).first())
    agent_db.query(RentalListing).delete()
    agent_db.add(RentalListing(property_id=other.id, monthly_rent_inr=45_000,
                               status="ACTIVE", lease_terms="11 months"))
    agent_db.commit()

    out = afford.rental_yield(agent_db, sale.id)
    assert out["gross_yield_percent"] is not None
    assert out["rent_is_estimate"] is True
    assert "comparable" in out["rent_source"]
    assert "1 comparable lettings" not in out["rent_source"]   # grammar
    agent_db.query(RentalListing).delete()
    agent_db.commit()


def test_a_letting_has_no_purchase_yield(agent_db):
    letting = (agent_db.query(Property)
               .filter(Property.listing_type == "RENT").first())
    if letting is None:
        pytest.skip("no letting in the catalogue")
    out = afford.rental_yield(agent_db, letting.id)
    assert out["gross_yield_percent"] is None
    assert "letting, not a sale listing" in out["basis"]


def test_affordability_endpoints_are_public():
    assert client.get("/api/v1/properties/1/affordability").status_code == 200
    assert client.get("/api/v1/properties/1/yield").status_code == 200
    r = client.get("/api/v1/affordability/quote",
                   params={"price_inr": 9_000_000, "monthly_income_inr": 150_000})
    assert r.status_code == 200
    assert r.json()["loan"]["emi_inr"] > 0


def test_consent_tests_do_not_depend_on_the_wall_clock():
    """Regression: four consent tests called allow() with no time, so once real
    time crossed 21:00 IST the quiet-hours branch short-circuited the check each
    was actually asserting. They passed all day and failed at night."""
    night = _dtm.datetime(2026, 9, 5, 22, 53, tzinfo=IST)
    assert consent_mod.in_quiet_hours(
        consent_mod.Consent(subject="x", channel="whatsapp", granted=True,
                            quiet_from_hour=21, quiet_to_hour=8), night) is True
    assert consent_mod.in_quiet_hours(
        consent_mod.Consent(subject="x", channel="whatsapp", granted=True,
                            quiet_from_hour=21, quiet_to_hour=8), DAYTIME) is False


# ------------------------------------------------- compare & conveyance

from app import compare as cmp_mod       # noqa: E402
from app import conveyance as conv_mod   # noqa: E402


def _sale(db, **kw):
    p = Property(title=kw.pop("title", "Test flat"), property_type="Flat",
                 config="3BHK", location_name=kw.pop("locality", "Whitefield"),
                 city="Bengaluru", area_sqft=kw.pop("area", 1500.0),
                 listing_type="SALE", amenities=[], **kw)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_the_cheapest_listing_is_not_always_the_cheapest_purchase(agent_db):
    """The finding the whole feature exists for: GST on an under-construction
    unit can invert the ranking a buyer sees on the listing page."""
    ready = _sale(agent_db, title="Ready flat", price_inr=10_000_000,
                  possession="Ready to move", khata="A-Khata", is_verified=True)
    building = _sale(agent_db, title="Under-construction flat", price_inr=9_700_000,
                     possession="Under construction", khata="A-Khata", is_verified=True)
    try:
        out = cmp_mod.compare(agent_db, [ready.id, building.id], 200_000)
        # listed cheaper …
        assert building.price_inr < ready.price_inr
        # … but costs more all-in
        col = {c["property_id"]: c for c in out["columns"]}
        assert col[building.id]["all_in_inr"] > col[ready.id]["all_in_inr"]
        titles = [f["title"] for f in out["findings"]]
        assert "The cheapest listing is not the cheapest purchase" in titles
    finally:
        agent_db.delete(ready); agent_db.delete(building); agent_db.commit()


def test_compare_flags_an_unfundable_property(agent_db):
    a = _sale(agent_db, title="A flat", price_inr=8_000_000, khata="A-Khata",
              possession="Ready to move", is_verified=True)
    b = _sale(agent_db, title="B flat", price_inr=8_000_000, khata="B-Khata",
              possession="Ready to move", is_verified=True)
    try:
        out = cmp_mod.compare(agent_db, [a.id, b.id], 200_000)
        col = {c["property_id"]: c for c in out["columns"]}
        assert col[a.id]["fundable"] is True
        assert col[b.id]["fundable"] is False
        assert any("cannot be financed" in f["title"] for f in out["findings"])
    finally:
        agent_db.delete(a); agent_db.delete(b); agent_db.commit()


def test_compare_refuses_fewer_than_two(agent_db):
    with pytest.raises(ValueError) as e:
        cmp_mod.compare(agent_db, [1])
    assert "at least two" in str(e.value)


def test_compare_refuses_more_than_it_can_render(agent_db):
    with pytest.raises(ValueError) as e:
        cmp_mod.compare(agent_db, [1, 2, 3, 4, 5])
    assert "readable" in str(e.value)


def test_compare_de_duplicates_ids(agent_db):
    """Asking to compare a property with itself is a slip, not a request."""
    with pytest.raises(ValueError):
        cmp_mod.compare(agent_db, [1, 1])


def test_compare_refuses_a_letting(agent_db):
    letting = (agent_db.query(Property)
               .filter(Property.listing_type == "RENT").first())
    if letting is None:
        pytest.skip("no letting in the catalogue")
    with pytest.raises(ValueError) as e:
        cmp_mod.compare(agent_db, [1, letting.id])
    assert "letting" in str(e.value)


def test_compare_says_when_nothing_separates_them(agent_db):
    a = _sale(agent_db, title="Twin A", price_inr=9_000_000, khata="A-Khata",
              possession="Ready to move", is_verified=True, facing="East")
    b = _sale(agent_db, title="Twin B", price_inr=9_050_000, khata="A-Khata",
              possession="Ready to move", is_verified=True, facing="East")
    try:
        out = cmp_mod.compare(agent_db, [a.id, b.id], 200_000)
        assert any(f["severity"] == "info" for f in out["findings"])
    finally:
        agent_db.delete(a); agent_db.delete(b); agent_db.commit()


# ---- conveyance ----

def test_the_sequence_puts_diligence_before_the_token(agent_db):
    """The rule the module exists to enforce: money follows the check."""
    out = conv_mod.timeline(agent_db, price_inr=10_000_000)
    keys = [s["key"] for s in out["stages"]]
    assert keys.index("diligence") < keys.index("agreement")
    assert "before the token" in out["rule"]


def test_khata_transfer_is_a_stage_of_its_own(agent_db):
    """Registration is not the end — the most missed step in Bengaluru."""
    out = conv_mod.timeline(agent_db, price_inr=10_000_000)
    keys = [s["key"] for s in out["stages"]]
    assert "khata" in keys
    assert keys.index("registration") < keys.index("khata")
    assert "khata" in out["most_missed"].lower()


def test_every_stage_says_what_goes_wrong(agent_db):
    out = conv_mod.timeline(agent_db, price_inr=10_000_000)
    for s in out["stages"]:
        assert s["goes_wrong"], f"{s['stage']} has no failure mode"
        assert s["documents"], f"{s['stage']} lists no documents"


def test_registration_money_matches_the_acquisition_engine(agent_db):
    """The sequence must not invent its own duty figure."""
    price = 12_400_000
    out = conv_mod.timeline(agent_db, price_inr=price)
    cost = afford.acquisition_cost(price, None)
    reg = next(s for s in out["stages"] if s["key"] == "registration")
    duty_lines = sum(l["amount_inr"] for l in cost["lines"]
                     if l["item"].startswith(("Stamp duty", "Cess", "Surcharge",
                                              "Registration")))
    assert reg["money_inr"] == pytest.approx(duty_lines, rel=0.01)


def test_conveyance_refuses_a_letting(agent_db):
    letting = (agent_db.query(Property)
               .filter(Property.listing_type == "RENT").first())
    if letting is None:
        pytest.skip("no letting in the catalogue")
    with pytest.raises(ValueError) as e:
        conv_mod.timeline(agent_db, property_id=letting.id)
    assert "letting" in str(e.value)


def test_conveyance_needs_something_to_work_against(agent_db):
    with pytest.raises(ValueError):
        conv_mod.timeline(agent_db)


def test_compare_and_conveyance_endpoints_are_public():
    assert client.get("/api/v1/compare",
                      params=[("ids", 1), ("ids", 2)]).status_code == 200
    assert client.get("/api/v1/conveyance",
                      params={"property_id": 1}).status_code == 200
    # one id is a user error, not a server error
    assert client.get("/api/v1/compare", params=[("ids", 1)]).status_code == 422


# ------------------------------------------------- multi-city (state law)

from app import cities as cities_mod  # noqa: E402


def test_duty_is_state_law_not_a_national_number():
    """Chennai is close to double Ahmedabad on the same purchase."""
    price = 10_000_000
    chennai = afford.acquisition_cost(price, "Ready to move", city="Chennai")
    gujarat = afford.acquisition_cost(price, "Ready to move", city="Ahmedabad")
    assert chennai["extras_inr"] > gujarat["extras_inr"] * 1.7
    assert chennai["state"] == "Tamil Nadu"
    assert gujarat["state"] == "Gujarat"


def test_maharashtra_registration_fee_is_capped():
    """1% uncapped on ₹5 Cr would be ₹5 L; Maharashtra caps it at ₹30,000."""
    out = afford.acquisition_cost(50_000_000, "Ready to move", city="Mumbai")
    reg = next(l for l in out["lines"] if l["item"].startswith("Registration"))
    assert reg["amount_inr"] == 30_000


def test_the_womens_concession_is_applied_where_a_state_offers_it():
    plain = afford.acquisition_cost(10_000_000, "Ready to move", city="Delhi NCR")
    woman = afford.acquisition_cost(10_000_000, "Ready to move",
                                    city="Delhi NCR", woman_purchaser=True)
    assert woman["extras_inr"] < plain["extras_inr"]
    # Karnataka offers none, so the flag must change nothing there.
    a = afford.acquisition_cost(10_000_000, "Ready to move", city="Bengaluru")
    b = afford.acquisition_cost(10_000_000, "Ready to move",
                                city="Bengaluru", woman_purchaser=True)
    assert a["extras_inr"] == b["extras_inr"]


def test_an_unknown_city_falls_back_rather_than_guessing():
    out = afford.acquisition_cost(10_000_000, "Ready to move", city="Atlantis")
    assert out["state"] == "Karnataka"
    assert cities_mod.is_known("Atlantis") is False
    assert cities_mod.is_known("mumbai") is True     # case-insensitive


def test_the_title_document_is_named_per_state():
    """'Khata' is Karnataka. Asking for one in Mumbai marks you as a tourist."""
    assert "Khata" in cities_mod.get("Bengaluru")["title_document"]
    assert "Property Card" in cities_mod.get("Mumbai")["title_document"]
    assert "Patta" in cities_mod.get("Chennai")["title_document"]
    assert "khata" not in cities_mod.get("Mumbai")["title_document"].lower()


def test_the_conveyance_transfer_step_follows_the_state(agent_db):
    out = conv_mod.timeline(agent_db, price_inr=10_000_000)
    assert "khata" in out["most_missed"].lower()      # default city
    stage = next(s for s in out["stages"] if s["key"] == "khata")
    assert "BBMP" in stage["stage"]


def test_amenities_are_priced_but_capped(agent_db):
    """Stored on every listing and, until now, never used. Capped as a bundle
    so a long amenity list cannot manufacture value."""
    prop = (agent_db.query(Property)
            .filter(Property.listing_type != "RENT",
                    Property.is_verified.is_(True),
                    Property.price_inr.isnot(None)).first())
    before = prop.amenities
    try:
        prop.amenities = ["Swimming pool", "Clubhouse", "Gym", "Power backup",
                          "Covered parking", "24x7 security", "Kids play area"]
        agent_db.commit()
        out = val.value(agent_db, prop.id)
        amen = [a for a in out["adjustments"] if a["factor"].startswith("Amenities")]
        assert amen, "amenities should be priced"
        assert amen[0]["percent"] <= val.AMENITY_CAP * 100 + 0.01
    finally:
        prop.amenities = before
        agent_db.commit()


def test_cities_endpoint_ranks_by_what_a_buyer_actually_pays():
    d = client.get("/api/v1/cities").json()
    totals = [c["total_percent"] for c in d["cities"]]
    assert totals == sorted(totals, reverse=True)
    assert d["cities"][0]["city"] == "Chennai"


def test_affordability_accepts_a_city_override():
    a = client.get("/api/v1/properties/1/affordability",
                   params={"monthly_income_inr": 200000, "city": "Bengaluru"}).json()
    b = client.get("/api/v1/properties/1/affordability",
                   params={"monthly_income_inr": 200000, "city": "Chennai"}).json()
    assert b["acquisition"]["extras_inr"] > a["acquisition"]["extras_inr"]


# ------------------------------------- the verify button is now guarded

def _fresh_unverified(db, title="Guard test flat"):
    p = Property(title=title, property_type="Flat", config="2BHK",
                 location_name="Sarjapur", city="Bengaluru", price_inr=7_800_000,
                 area_sqft=1100.0, possession="Ready to move", khata="A-Khata",
                 is_verified=False, listing_type="SALE", amenities=[],
                 disclosures={})
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_verify_refuses_a_listing_with_nothing_on_file(agent_db, auth_on):
    """Regression: found by walking the seller flow as a customer. A listing
    went live with nine questions unanswered and none of nine documents held,
    because the button flipped the flag unconditionally."""
    p = _fresh_unverified(agent_db)
    try:
        r = client.post(f"/api/v1/properties/{p.id}/verify", headers=STAFF)
        assert r.status_code == 409
        body = r.json()["detail"]
        assert "Refusing to verify" in body["message"]
        assert body["documents_missing"]            # nothing was uploaded
        assert body["unanswered"]                   # nothing was answered
        agent_db.refresh(p)
        assert p.is_verified is False               # and it stayed hidden
    finally:
        agent_db.delete(p); agent_db.commit()


def test_verify_override_needs_a_written_reason(agent_db, auth_on):
    p = _fresh_unverified(agent_db, "Override no reason")
    try:
        r = client.post(f"/api/v1/properties/{p.id}/verify",
                        params={"override": "true"}, headers=STAFF)
        assert r.status_code == 422
        assert "reason" in r.json()["detail"].lower()
        agent_db.refresh(p)
        assert p.is_verified is False
    finally:
        agent_db.delete(p); agent_db.commit()


def test_verify_override_with_reason_is_recorded_in_the_audit_trail(agent_db, auth_on):
    """The advocate is the final authority and may have seen originals in
    person — so an override is allowed, but it must leave a trace that says
    exactly what was still open when they signed."""
    p = _fresh_unverified(agent_db, "Override with reason")
    try:
        r = client.post(f"/api/v1/properties/{p.id}/verify",
                        params={"override": "true",
                                "reason": "Originals examined in chambers 12 Sep."},
                        headers=STAFF)
        assert r.status_code == 200
        assert r.json()["is_verified"] is True
        assert r.json()["signed_off_over_open_findings"] is True

        ev = (agent_db.query(AuditEvent)
              .filter_by(property_id=p.id, action="VERIFY")
              .order_by(AuditEvent.id.desc()).first())
        assert ev is not None
        assert ev.detail["override"] is True
        assert "chambers" in ev.detail["reason"]
        assert ev.detail["open_high_at_signoff"] > 0
        assert ev.detail["documents_missing"]
    finally:
        agent_db.query(AuditEvent).filter_by(property_id=p.id).delete()
        agent_db.delete(p); agent_db.commit()


def test_verify_still_needs_a_staff_key(agent_db, auth_on):
    p = _fresh_unverified(agent_db, "No key")
    try:
        assert client.post(f"/api/v1/properties/{p.id}/verify").status_code in (401, 403)
    finally:
        agent_db.delete(p); agent_db.commit()
