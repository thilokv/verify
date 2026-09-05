"""Hybrid search — Pin 2 of the blueprint.

Combines relational SQL constraints with vector similarity, exactly as
FR-2 specifies, and gates on verification per FR-5.

Two execution paths behind one interface:
  pgvector  ORDER BY embedding <=> query_vector, in the database.
  sqlite    candidate rows pulled, cosine computed in Python. Fine for a
            pilot-sized catalogue; it is a scan, so it does not scale.
"""

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .embeddings import cosine, embed_one
from .llm import get_llm
from .models import Property

# ------------------------------------------------------------------ parsing

_NUM = r"(\d+(?:\.\d+)?)"

# "under 1.3 crore", "below 90 lakhs", "budget 1.5cr", "upto 75 lakh"
_BUDGET_PATTERNS = [
    (re.compile(rf"(?:under|below|upto|up to|within|max|budget of|budget)\s*(?:rs\.?|₹|inr)?\s*{_NUM}\s*(cr|crore|crores)", re.I), 1e7),
    (re.compile(rf"(?:under|below|upto|up to|within|max|budget of|budget)\s*(?:rs\.?|₹|inr)?\s*{_NUM}\s*(l|lac|lakh|lakhs)", re.I), 1e5),
    (re.compile(rf"(?:rs\.?|₹|inr)\s*{_NUM}\s*(cr|crore|crores)", re.I), 1e7),
    (re.compile(rf"(?:rs\.?|₹|inr)\s*{_NUM}\s*(l|lac|lakh|lakhs)", re.I), 1e5),
    (re.compile(rf"{_NUM}\s*(cr|crore|crores)\b", re.I), 1e7),
    (re.compile(rf"{_NUM}\s*(lakhs|lakh|lac)\b", re.I), 1e5),
]

_CONFIG = re.compile(r"\b(\d)\s*[- ]?\s*(?:bhk|bedroom|bed room|br)\b", re.I)

_TYPE_WORDS = {
    "Plot": ["plot", "land", "site", "parcel", "acre", "farmland", "farm land"],
    "Villa": ["villa", "bungalow", "independent house", "row house"],
    "Flat": ["flat", "apartment", "condo"],
    "Commercial": ["commercial", "office", "shop", "retail", "warehouse"],
}

_POSSESSION = {
    "Ready to move": ["ready to move", "ready-to-move", "ready", "immediate", "move in"],
    "Under construction": ["under construction", "pre launch", "prelaunch", "booking"],
}

# Locality is a HARD constraint, not a similarity hint. A buyer asking for
# Whitefield does not want Electronic City ranked above it because the two
# descriptions happen to share generic words. Extend this list per city as
# the catalogue grows; back it with a gazetteer table before multi-city.
LOCALITIES = [
    "whitefield", "sarjapur", "hebbal", "electronic city", "koramangala",
    "indiranagar", "jp nagar", "yelahanka", "devanahalli", "kanakapura",
    "bannerghatta", "marathahalli", "hsr layout", "btm", "rajajinagar",
    "jayanagar", "gachibowli", "kondapur", "kokapet",
]


def find_locality(text: str) -> Optional[str]:
    """Longest match wins, so 'electronic city' beats a stray 'city'."""
    low = (text or "").lower()
    hits = [loc for loc in LOCALITIES if loc in low]
    if not hits:
        return None
    return max(hits, key=len).title()


def parse_constraints(prompt: str) -> Dict[str, Any]:
    """Pull hard filters out of the natural-language brief.

    Anything not confidently found is left None so the vector side decides.
    """
    text = prompt or ""
    out: Dict[str, Any] = {
        "max_price": None,
        "config": None,
        "property_type": None,
        "possession": None,
        "locality": find_locality(text),
    }

    for pattern, multiplier in _BUDGET_PATTERNS:
        m = pattern.search(text)
        if m:
            out["max_price"] = float(m.group(1)) * multiplier
            break

    m = _CONFIG.search(text)
    if m:
        out["config"] = f"{m.group(1)}BHK"

    low = text.lower()
    for canonical, words in _TYPE_WORDS.items():
        if any(w in low for w in words):
            out["property_type"] = canonical
            break

    for canonical, words in _POSSESSION.items():
        if any(w in low for w in words):
            out["possession"] = canonical
            break

    return out


# ------------------------------------------------------------------ ranking

def _candidate_query(constraints: Dict[str, Any], overrides: Dict[str, Any],
                     with_locality: bool = True):
    """Relational half of the hybrid search."""
    stmt = select(Property)

    # Buyer search sells things. A rental has no asking price and belongs to a
    # different market, so it is excluded here rather than relying on it
    # happening to stay unverified.
    stmt = stmt.where(Property.listing_type != "RENT")

    if settings.ENFORCE_VERIFICATION_GATE:
        # FR-5: unverified stock never reaches a buyer.
        stmt = stmt.where(Property.is_verified.is_(True))

    max_price = overrides.get("max_price") or constraints.get("max_price")
    if max_price:
        stmt = stmt.where(Property.price_inr <= float(max_price))

    ptype = overrides.get("property_type") or constraints.get("property_type")
    if ptype:
        stmt = stmt.where(Property.property_type.ilike(f"%{ptype}%"))

    config = constraints.get("config")
    if config:
        stmt = stmt.where(Property.config.ilike(f"%{config}%"))

    locality = constraints.get("locality")
    if locality and with_locality:
        stmt = stmt.where(Property.location_name.ilike(f"%{locality}%"))

    return stmt


def search(
    db: Session,
    prompt: str,
    limit: Optional[int] = None,
    max_price: Optional[float] = None,
    property_type: Optional[str] = None,
    explain: bool = True,
) -> Dict[str, Any]:
    """Run the hybrid search and return ranked matches with rationale."""
    limit = limit or settings.DEFAULT_RESULT_LIMIT
    constraints = parse_constraints(prompt)
    overrides = {"max_price": max_price, "property_type": property_type}
    effective = {
        "max_price": max_price or constraints["max_price"],
        "config": constraints["config"],
        "property_type": property_type or constraints["property_type"],
        "possession": constraints["possession"],
        "locality": constraints["locality"],
    }

    query_vec = embed_one(prompt)

    stmt = _candidate_query(constraints, overrides)
    relaxed_locality = False

    # If the requested locality has no stock, widen rather than return nothing —
    # but say so, so the buyer is never quietly shown a different neighbourhood.
    if constraints.get("locality"):
        probe = db.execute(stmt).scalars().first()
        if probe is None:
            stmt = _candidate_query(constraints, overrides, with_locality=False)
            relaxed_locality = True

    scored: List[Dict[str, Any]] = []

    if settings.is_pgvector:
        # Distance ordering happens in the database, using the HNSW index.
        stmt = stmt.order_by(Property.embedding.cosine_distance(query_vec)).limit(limit)
        rows = db.execute(stmt).scalars().all()
        for row in rows:
            payload = row.to_public()
            payload["score"] = None  # ordering is authoritative here
            scored.append(payload)
    else:
        rows = db.execute(stmt).scalars().all()
        for row in rows:
            payload = row.to_public()
            payload["score"] = round(cosine(query_vec, row.embedding or []), 4)
            scored.append(payload)
        scored.sort(key=lambda p: p["score"], reverse=True)
        scored = scored[:limit]

    rationale = []
    if scored and explain:
        rationale = get_llm().explain(prompt, scored, effective)

    by_id = {str(r["id"]): r for r in rationale}
    for item in scored:
        hit = by_id.get(str(item["id"]), {})
        item["why"] = hit.get("why", "")
        item["concern"] = hit.get("concern", "")

    return {
        "query": prompt,
        "parsed_constraints": effective,
        "semantic": settings.EMBEDDING_PROVIDER != "hash",
        "rationale_source": get_llm().name,
        "relaxed_locality": relaxed_locality,
        "notice": (
            f"No verified stock in {effective['locality']} matched — showing the "
            "closest alternatives elsewhere."
        ) if relaxed_locality else None,
        "count": len(scored),
        "matches": scored,
    }


def reindex(db: Session, batch_size: int = 64) -> int:
    """(Re)compute embeddings for every listing. Run after a bulk import or
    after changing the embedding provider — vectors from different providers
    are not comparable."""
    from .embeddings import embed_many

    rows = db.execute(select(Property)).scalars().all()
    written = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        vectors = embed_many([r.embedding_text() for r in chunk])
        for row, vec in zip(chunk, vectors):
            row.embedding = vec
            written += 1
        db.commit()
    return written
