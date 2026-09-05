"""Evidence engine — verification by document, not by declaration.

A tick-box saying "yes I hold the sale deed" is worth nothing: a fraudster
ticks it too. This module replaces the declaration with evidence, and then
attacks the evidence.

WHAT IT ACTUALLY CATCHES

  Tampering after upload   Every file is SHA-256 hashed at rest. A byte
                           changed later no longer matches its digest.

  Document re-use rings    The same scan submitted against two different
                           properties is the signature of a fraud ring. Hash
                           collision across property ids is flagged loudly.

  Cross-document lies      A sale deed saying Survey 42/1, an EC saying 88/3
                           and a khata in a third name cannot all be the same
                           property. Field extraction runs on every document
                           and disagreement between them is the strongest
                           machine-detectable fraud signal available here —
                           and it is exactly what a checklist can never see.

  Unsigned "official" PDFs Encumbrance certificates issued through Kaveri and
                           most state portals carry a digital signature. A PDF
                           EC with no signature object is not necessarily
                           forged, but it is worth a human look.

  Screenshot substitution  A 40 KB image is not a scan of a 30-year EC.

WHAT IT CANNOT DO — SAY THIS PLAINLY TO USERS

  It cannot prove a document is genuine. That needs the issuing registry, and
  this platform has no connection to Bhoomi, Kaveri, BBMP or any RERA portal.
  A well-made forgery that is internally consistent passes every check here.
  The advocate's title opinion, against originals, remains the only thing that
  actually clears a title. This engine decides WHO GETS LOOKED AT FIRST.
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from . import documents as docs

# Verification ladder. Only ADVOCATE_VERIFIED is a real clearance.
LEVELS = [
    ("NOT_SUBMITTED", "No documents submitted"),
    ("PARTIAL", "Some required documents still missing"),
    ("SUBMITTED", "All required documents submitted, checks pending"),
    ("MACHINE_CHECKED", "Machine checks passed — awaiting advocate"),
    ("FLAGGED", "Machine checks found a problem"),
    ("ADVOCATE_VERIFIED", "Advocate title opinion on file"),
]

MIN_SCAN_BYTES = 60 * 1024      # below this, an "official document" is suspect


def digest(data: bytes) -> str:
    """SHA-256, hex. The chain-of-custody anchor for one file."""
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ PDF signals

def pdf_signals(data: bytes) -> Dict[str, Any]:
    """Cheap structural read of a PDF. No parsing library, no attack surface."""
    if not data.startswith(b"%PDF-"):
        return {"is_pdf": False}

    head = data[:2048]
    version = ""
    m = re.match(rb"%PDF-(\d\.\d)", head)
    if m:
        version = m.group(1).decode("ascii", "ignore")

    # A digitally signed PDF carries a signature dictionary.
    signed = (b"/Sig" in data or b"adbe.pkcs7" in data
              or b"/ByteRange" in data or b"ETSI.CAdES" in data)

    # Incremental saves leave more than one EOF marker. Normal for signed
    # documents; on an unsigned one it means the file was edited after issue.
    eofs = data.count(b"%%EOF")

    producer = ""
    m = re.search(rb"/Producer\s*\(([^)]{0,120})\)", data)
    if m:
        producer = m.group(1).decode("latin-1", "ignore").strip()

    return {
        "is_pdf": True,
        "version": version,
        "digitally_signed": signed,
        "revisions": eofs,
        "producer": producer,
        "has_text_layer": b"/Font" in data or b"BT" in data,
    }


# --------------------------------------------------- cross-document fields

_SURVEY = re.compile(r"\bsurvey\s*(?:no\.?|number)?\s*[:\-]?\s*(\d+(?:/\d+[A-Za-z]?)?)", re.I)
_KHATA_NO = re.compile(r"\bkhata\s*(?:no\.?|number)\s*[:\-]?\s*([A-Za-z0-9/\-]+)", re.I)
_RERA = re.compile(r"\bPRM/[A-Z]{2}/RERA/[A-Za-z0-9/]+", re.I)
_NAMES = re.compile(r"\b(?:executed by|vendor|seller|owner|in favour of|khatedar)\s*[:\-]?\s*"
                    r"((?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+)")


def extract_fields(text: str) -> Dict[str, Optional[str]]:
    text = text or ""
    survey = _SURVEY.search(text)
    khata = _KHATA_NO.search(text)
    rera = _RERA.search(text)
    names = _NAMES.findall(text)
    return {
        "survey_no": survey.group(1).strip() if survey else None,
        "khata_no": khata.group(1).strip() if khata else None,
        "rera_id": rera.group(0).strip() if rera else None,
        "names": sorted({n.strip() for n in names}) or None,
    }


def _norm(v: Optional[str]) -> Optional[str]:
    return re.sub(r"[\s\-]", "", v).lower() if v else None


def cross_check(per_document: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Compare extracted fields across every submitted document.

    `per_document` items: {code, label, fields}
    """
    findings: List[Dict[str, str]] = []

    for field, human in (("survey_no", "survey number"),
                         ("khata_no", "khata number"),
                         ("rera_id", "RERA registration")):
        seen: Dict[str, List[str]] = {}
        for d in per_document:
            val = _norm((d.get("fields") or {}).get(field))
            if val:
                seen.setdefault(val, []).append(d["code"])
        if len(seen) > 1:
            detail = "; ".join(
                f"{', '.join(codes)} say {val}" for val, codes in seen.items())
            findings.append({
                "severity": "high",
                "code": f"MISMATCH_{field.upper()}",
                "detail": (f"The documents disagree on the {human}: {detail}. "
                           f"Documents describing the same property must agree. "
                           f"This is either the wrong document or a substituted one."),
            })

    # Owner names: at least one shared name across documents that carry names.
    named = [d for d in per_document if (d.get("fields") or {}).get("names")]
    if len(named) > 1:
        sets = [{_norm(n) for n in d["fields"]["names"]} for d in named]
        common = set.intersection(*sets) if sets else set()
        if not common:
            findings.append({
                "severity": "medium",
                "code": "NO_COMMON_NAME",
                "detail": ("No name appears across all the documents that name a "
                           "person. Indian records vary in name order and "
                           "initials, so this is not proof of a problem — but an "
                           "advocate should confirm they describe one owner."),
            })

    return findings


# ------------------------------------------------------------- assessment

def assess_documents(
    required: List[Dict[str, str]],
    submitted: List[Dict[str, Any]],
    *,
    duplicate_hits: Optional[Dict[str, List[int]]] = None,
) -> Dict[str, Any]:
    """Score the evidence.

    `submitted` items:
      {code, label, media_type, bytes, sha256, text (optional), pdf (optional)}
    `duplicate_hits`: sha256 -> [other property ids using the same bytes]
    """
    findings: List[Dict[str, str]] = []
    per_document: List[Dict[str, Any]] = []
    by_code = {}

    for s in submitted:
        code = (s.get("code") or "").upper()
        by_code.setdefault(code, []).append(s)

        fields = extract_fields(s.get("text") or "")
        per_document.append({"code": code, "label": s.get("label"), "fields": fields})

        # --- re-use across properties -------------------------------
        others = (duplicate_hits or {}).get(s.get("sha256") or "", [])
        if others:
            findings.append({
                "severity": "high",
                "code": "DUPLICATE_ACROSS_PROPERTIES",
                "detail": (f"This exact file is already submitted against "
                           f"property {', '.join('#' + str(o) for o in others)}. "
                           f"The same document cannot evidence two properties. "
                           f"Treat as suspected fraud until explained."),
            })

        # --- substance ----------------------------------------------
        size = int(s.get("bytes") or 0)
        if size and size < MIN_SCAN_BYTES:
            findings.append({
                "severity": "medium",
                "code": "THIN_FILE",
                "detail": (f"{s.get('label') or code} is only {size // 1024} KB. "
                           f"A scan of a real multi-page document is normally "
                           f"much larger. Check it is the whole document and "
                           f"not a screenshot or a single page."),
            })

        # --- PDF integrity ------------------------------------------
        pdf = s.get("pdf") or {}
        if pdf.get("is_pdf"):
            if code in {"EC", "RERA", "KHATA"} and not pdf.get("digitally_signed"):
                findings.append({
                    "severity": "medium",
                    "code": "UNSIGNED_OFFICIAL_PDF",
                    "detail": (f"{s.get('label') or code} is a PDF with no digital "
                               f"signature. Certificates issued through state "
                               f"portals are normally signed. Not proof of "
                               f"forgery — but worth opening first."),
                })
            if not pdf.get("digitally_signed") and int(pdf.get("revisions") or 0) > 1:
                findings.append({
                    "severity": "medium",
                    "code": "PDF_EDITED_AFTER_CREATION",
                    "detail": (f"{s.get('label') or code} contains "
                               f"{pdf['revisions']} saved revisions and no "
                               f"signature, meaning it was modified after it was "
                               f"first written."),
                })
            if not pdf.get("has_text_layer"):
                findings.append({
                    "severity": "low",
                    "code": "NO_TEXT_LAYER",
                    "detail": (f"{s.get('label') or code} has no text layer, so "
                               f"nothing could be read from it automatically. "
                               f"It must be read by a person."),
                })

    # --- duplicates within this property ----------------------------
    for code, items in by_code.items():
        hashes = {i.get("sha256") for i in items if i.get("sha256")}
        if len(items) > 1 and len(hashes) == 1:
            findings.append({
                "severity": "low",
                "code": "REPEATED_UPLOAD",
                "detail": f"The same file was uploaded more than once for {code}.",
            })

    findings.extend(cross_check(per_document))

    # --- completeness -----------------------------------------------
    required_codes = [d["code"] for d in required]
    have = set(by_code)
    missing = [d for d in required if d["code"] not in have]

    core_missing = [d for d in missing]
    for d in core_missing:
        findings.append({
            "severity": "high",
            "code": "MISSING_" + d["code"],
            "detail": f"{d['label']} has not been submitted. {d['why']}",
        })

    highs = sum(1 for f in findings if f["severity"] == "high")
    mediums = sum(1 for f in findings if f["severity"] == "medium")

    if not submitted:
        level = "NOT_SUBMITTED"
    elif missing:
        level = "PARTIAL"
    elif highs:
        level = "FLAGGED"
    elif mediums:
        level = "SUBMITTED"
    else:
        level = "MACHINE_CHECKED"

    return {
        "level": level,
        "level_label": dict(LEVELS)[level],
        "high_count": highs,
        "medium_count": mediums,
        "submitted_codes": sorted(have),
        "missing_codes": [d["code"] for d in missing],
        "required_count": len(required_codes),
        "extracted": per_document,
        "findings": sorted(
            findings,
            key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["severity"]]),
    }


def read_text(data: bytes, media_type: str) -> str:
    """Best-effort text out of an upload, for cross-checking.

    PDFs: only the uncompressed text operators, which is all that is available
    without a parser. Images: nothing — OCR is not wired (see documents.py),
    and returning "" rather than guessing keeps the finding honest.
    """
    if media_type != "application/pdf":
        return ""
    out: List[str] = []
    for m in re.finditer(rb"\(((?:[^()\\]|\\.){1,300})\)\s*Tj", data):
        try:
            out.append(m.group(1).decode("latin-1", "ignore"))
        except Exception:
            pass
    return " ".join(out)[:20000]


def triage_text(text: str) -> Optional[Dict[str, Any]]:
    """Run the existing title checks when we managed to read anything."""
    if not (text or "").strip():
        return None
    return docs.verify_document(text)
