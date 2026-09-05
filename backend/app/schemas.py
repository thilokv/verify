"""Pydantic request/response models."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    user_prompt: str = Field(..., min_length=2, max_length=2000)
    max_price: Optional[float] = None
    property_type: Optional[str] = None
    limit: int = Field(3, ge=1, le=25)


class PropertyMatch(BaseModel):
    id: int
    title: str
    property_type: Optional[str] = None
    config: Optional[str] = None
    location_name: Optional[str] = None
    city: Optional[str] = None
    price_inr: Optional[float] = None
    area_sqft: Optional[float] = None
    possession: Optional[str] = None
    facing: Optional[str] = None
    rera_id: Optional[str] = None
    khata: Optional[str] = None
    is_verified: bool = False
    verification_note: Optional[str] = None
    description: Optional[str] = None
    amenities: List[Any] = []
    score: Optional[float] = None
    why: str = ""
    concern: str = ""


class SearchResponse(BaseModel):
    query: str
    parsed_constraints: Dict[str, Any]
    semantic: bool
    rationale_source: str
    relaxed_locality: bool = False
    notice: Optional[str] = None
    count: int
    matches: List[PropertyMatch]


class PropertyIn(BaseModel):
    title: str
    property_type: str
    location_name: str
    city: str = "Bengaluru"
    price_inr: float
    area_sqft: Optional[float] = None
    config: Optional[str] = None
    possession: Optional[str] = None
    facing: Optional[str] = None
    rera_id: Optional[str] = None
    khata: Optional[str] = None
    description: Optional[str] = None
    amenities: List[str] = []
    developer_id: Optional[int] = None


class DocumentCheck(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    expected_owner: Optional[str] = None
    expected_survey: Optional[str] = None
    expected_rera: Optional[str] = None


class ChatIn(BaseModel):
    """Simulates one inbound WhatsApp message, for local testing."""
    phone: str = Field(..., min_length=4, max_length=20)
    text: str = Field(..., min_length=1, max_length=2000)


class SellerTurn(BaseModel):
    """One turn of the Jarvis seller intake. The client holds the state."""
    text: str = Field("", max_length=2000)
    stage: Optional[str] = None
    draft: Dict[str, Any] = {}


class DisclosureIn(BaseModel):
    """Seller answers to the fraud-screen questions, plus documents held."""
    answers: Dict[str, Any] = {}
    documents_held: List[str] = []


class ReportIn(BaseModel):
    """A buyer reporting a suspicious listing or approach."""
    property_id: Optional[int] = None
    reason: str = Field(..., min_length=3, max_length=120)
    detail: str = Field("", max_length=4000)
    contact: Optional[str] = Field(None, max_length=120)


class MessageScan(BaseModel):
    """Text a seller or agent sent the buyer, checked for pressure tactics."""
    text: str = Field(..., min_length=1, max_length=4000)
