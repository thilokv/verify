"""Title & document intelligence — Pin 4 of the blueprint.

Scope discipline matters here. This module triages; it does not clear title.
A signed advocate opinion is what carries legal weight in an Indian
conveyance, and no OCR pipeline replaces it (blueprint §8.3). What this does
is cheap, fast pre-screening so a human reviews the right 10% of files.

OCR is a pluggable hook: `extract_text` accepts already-extracted text, or
calls a configured OCR backend. The *checks* below are the real value and
they run on text from any source.
"""

import re
from typing import Any, Dict, List, Optional

# Karnataka RERA registration numbers look like PRM/KA/RERA/1251/446/PR/...
_RERA_RE = re.compile(r"\bPRM/KA/RERA/[0-9]{3,5}/[0-9]{3,5}/[A-Z]{2}/[0-9]{6}/[0-9]{6}\b", re.I)
_RERA_LOOSE = re.compile(r"\bPRM/[A-Z]{2}/RERA/[A-Za-z0-9/]+\b", re.I)

_SURVEY_RE = re.compile(r"\bsurvey\s*(?:no\.?|number)\s*[:\-]?\s*([0-9]+(?:/[0-9A-Za-z]+)?)", re.I)
_EXTENT_RE = re.compile(r"\b(?:extent|measuring|area)\s*[:\-]?\s*([0-9,.]+)\s*(sq\.?\s*ft|sqft|sq\.?\s*mtr|acres?|guntas?)", re.I)

_ENCUMBRANCE_CLEAR = re.compile(
    r"\b(no\s+encumbrance|nil\s+encumbrance|free\s+from\s+(?:all\s+)?encumbrance)", re.I)
_ENCUMBRANCE_CHARGE = re.compile(
    r"\b(mortgage|charge\s+created|lien|hypotheca|attachment|lis\s+pendens)\b", re.I)

_LITIGATION = re.compile(
    r"\b(suit|litigation|stay\s+order|injunction|court\s+case|o\.?s\.?\s*no)\b", re.I)

_KHATA_B = re.compile(r"\bB[\s\-]?khata\b", re.I)
_KHATA_A = re.compile(r"\bA[\s\-]?khata\b", re.I)

_SIGNATURE = re.compile(r"\b(signature|signed|sd/-|witness)\b", re.I)

DOC_TYPES = ["Sale Deed", "Pahani", "RERA", "EC", "Khata", "Other"]


def extract_text(*, text: Optional[str] = None, file_path: Optional[str] = None) -> str:
    """Return document text.

    Supply `text` when you already have it. `file_path` requires an OCR
    backend — wire Textract, Vision or Tesseract here. Kept as an explicit
    unimplemented branch rather than a silent empty string, so a missing OCR
    backend fails loudly instead of passing every document as clean.
    """
    if text is not None:
        return text
    if file_path:
        raise NotImplementedError(
            "No OCR backend configured. Wire AWS Textract / Google Vision / "
            "Tesseract in extract_text(), or pass pre-extracted text."
        )
    return ""


def classify(text: str) -> str:
    low = (text or "").lower()
    if "encumbrance certificate" in low or re.search(r"\bform\s*(?:no\.?\s*)?(?:15|16)\b", low):
        return "EC"
    if "sale deed" in low or "deed of sale" in low:
        return "Sale Deed"
    if "pahani" in low or "rtc" in low or "record of rights" in low:
        return "Pahani"
    if _RERA_LOOSE.search(text or ""):
        return "RERA"
    if _KHATA_A.search(text or "") or _KHATA_B.search(text or ""):
        return "Khata"
    return "Other"


def verify_document(
    text: str,
    *,
    expected_owner: Optional[str] = None,
    expected_survey: Optional[str] = None,
    expected_rera: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the title checks and return a triage verdict.

    Verdict is one of PASSED | PENDING | RED_FLAGGED. PASSED never means
    "title is clear" — it means "nothing this screen can detect is wrong".
    """
    text = text or ""
    issues: List[Dict[str, str]] = []

    def flag(severity: str, code: str, detail: str) -> None:
        issues.append({"severity": severity, "code": code, "detail": detail})

    doc_type = classify(text)

    if not text.strip():
        flag("high", "EMPTY_DOCUMENT", "No text could be read from this document.")
        return {
            "document_type": doc_type,
            "verdict": "RED_FLAGGED",
            "issues": issues,
            "extracted": {},
        }

    # ---- extraction ----------------------------------------------------
    rera = _RERA_RE.search(text) or _RERA_LOOSE.search(text)
    survey = _SURVEY_RE.search(text)
    extent = _EXTENT_RE.search(text)

    extracted = {
        "rera_id": rera.group(0) if rera else None,
        "survey_no": survey.group(1) if survey else None,
        "extent": f"{extent.group(1)} {extent.group(2)}" if extent else None,
        "khata": "B-Khata" if _KHATA_B.search(text) else ("A-Khata" if _KHATA_A.search(text) else None),
    }

    # ---- checks --------------------------------------------------------
    if _ENCUMBRANCE_CHARGE.search(text):
        flag("high", "ENCUMBRANCE_FOUND",
             "The document mentions a mortgage, lien or charge. An advocate must "
             "confirm it is discharged before any token payment.")
    elif doc_type == "EC" and not _ENCUMBRANCE_CLEAR.search(text):
        flag("medium", "ENCUMBRANCE_UNCLEAR",
             "Encumbrance certificate does not carry an explicit nil-encumbrance "
             "statement for the period examined.")

    if _LITIGATION.search(text):
        flag("high", "LITIGATION_REFERENCE",
             "The document references litigation, a suit or a stay order.")

    if extracted["khata"] == "B-Khata":
        flag("medium", "B_KHATA",
             "B-Khata property. Regularisation status must be confirmed; bank "
             "lending against B-Khata is restricted.")

    if expected_owner:
        # Compare on surname-insensitive token overlap; Indian names vary in
        # order and initials across records.
        want = {t for t in re.findall(r"[a-z]+", expected_owner.lower()) if len(t) > 2}
        have = set(re.findall(r"[a-z]+", text.lower()))
        if want and not (want & have):
            flag("high", "OWNER_MISMATCH",
                 f"Expected owner '{expected_owner}' does not appear in this document.")

    if expected_survey and extracted["survey_no"]:
        if expected_survey.strip().lower() != extracted["survey_no"].strip().lower():
            flag("high", "SURVEY_MISMATCH",
                 f"Survey number {extracted['survey_no']} does not match the "
                 f"listing's {expected_survey}.")

    if expected_rera and extracted["rera_id"]:
        if expected_rera.replace(" ", "").lower() != extracted["rera_id"].replace(" ", "").lower():
            flag("high", "RERA_MISMATCH",
                 "RERA number on the document differs from the listing.")

    if doc_type == "Sale Deed" and not _SIGNATURE.search(text):
        flag("medium", "NO_SIGNATURE_BLOCK",
             "No signature or witness block detected in the sale deed.")

    if doc_type == "RERA" and not extracted["rera_id"]:
        flag("medium", "RERA_UNREADABLE",
             "Document appears to be a RERA certificate but no registration "
             "number could be parsed.")

    severities = {i["severity"] for i in issues}
    if "high" in severities:
        verdict = "RED_FLAGGED"
    elif issues:
        verdict = "PENDING"
    else:
        verdict = "PASSED"

    return {
        "document_type": doc_type,
        "verdict": verdict,
        "issues": issues,
        "extracted": extracted,
        "disclaimer": (
            "Automated triage only. This is not a title opinion and carries no "
            "legal weight. A licensed advocate must sign off before any payment."
        ),
    }
