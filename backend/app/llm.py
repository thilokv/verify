"""Language-model layer: match rationale and conversational replies.

The source draft returned `f"Top match based on criteria: '{prompt}'"` as the
match reason — which hands the buyer their own words back and calls it AI
(blueprint §8.1). This module replaces that with either a real model call or
an honest rule-based explanation derived from the actual field comparison.

The stub never invents a fact. Every clause it emits is computed from the
listing row and the parsed constraints.
"""

import json
from typing import Any, Dict, List, Optional

from .config import settings


# ---------------------------------------------------------------- rationale

def _lakh(v: Optional[float]) -> str:
    if not v:
        return "price on request"
    lakh = v / 100000.0
    if lakh >= 100:
        cr = lakh / 100.0
        return f"₹{cr:.2f} Cr".replace(".00 Cr", " Cr")
    return f"₹{lakh:.0f} L"


def _rule_based_reason(prop: Dict[str, Any], constraints: Dict[str, Any]) -> str:
    """Derive an explanation from real field comparisons only."""
    bits: List[str] = []

    if prop.get("config"):
        bits.append(f"{prop['config']} in {prop.get('location_name') or prop.get('city')}")
    elif prop.get("location_name"):
        bits.append(prop["location_name"])

    price = prop.get("price_inr")
    budget = constraints.get("max_price")
    if price and budget:
        if price <= budget:
            head = _lakh(price)
            room = (budget - price) / 100000.0
            if room >= 5:
                bits.append(f"{head}, about ₹{room:.0f} L under your budget")
            else:
                bits.append(f"{head}, just inside your budget")
        else:
            over = (price - budget) / 100000.0
            bits.append(f"{_lakh(price)} — about ₹{over:.0f} L over your budget")
    elif price:
        bits.append(_lakh(price))

    if prop.get("possession"):
        bits.append(prop["possession"].lower())

    if prop.get("is_verified"):
        bits.append("title opinion signed")
    else:
        bits.append("title not yet verified")

    text = "; ".join(bits)
    # Not .capitalize() — that lowercases the rest, mangling "3BHK" and "₹1.24 Cr".
    return (text[:1].upper() + text[1:] + ".") if text else ""


def _rule_based_concern(prop: Dict[str, Any], constraints: Dict[str, Any]) -> str:
    if not prop.get("is_verified"):
        return (
            prop.get("verification_note")
            or "Title checks are not complete — do not pay a token before the advocate opinion."
        )
    price, budget = prop.get("price_inr"), constraints.get("max_price")
    if price and budget and price > budget:
        return "Priced above the budget you gave."
    if (prop.get("khata") or "").upper().startswith("B"):
        return "B-Khata property — confirm regularisation status before proceeding."
    return ""


_RATIONALE_PROMPT = """You are ranking Bengaluru property listings against a buyer's brief.

BUYER'S BRIEF:
{brief}

CANDIDATES (JSON; price_inr is in rupees, 100 lakh = 1 crore):
{candidates}

For each candidate return an object with:
  "id"      - the candidate id, unchanged
  "why"     - one sentence, max 22 words, addressed to the buyer, naming the
              specific thing that fits: locality, price against their budget,
              possession, or title status.
  "concern" - one short sentence naming a real drawback or title risk they
              should know before visiting. Empty string if there is none.

Never invent a fact that is not in the candidate data. An unverified title is
always worth flagging as a concern.

Reply with ONLY a JSON array, same order as the candidates."""


class StubLLM:
    name = "stub"
    generative = False

    def explain(self, brief: str, candidates: List[Dict[str, Any]],
                constraints: Dict[str, Any]) -> List[Dict[str, str]]:
        return [
            {
                "id": c["id"],
                "why": _rule_based_reason(c, constraints),
                "concern": _rule_based_concern(c, constraints),
            }
            for c in candidates
        ]

    def reply(self, system: str, history: List[Dict[str, str]]) -> str:
        raise RuntimeError(
            "LLM_PROVIDER=stub cannot generate free-form replies. "
            "Set LLM_PROVIDER=anthropic or openai for the conversational agent."
        )


class AnthropicLLM:
    name = "anthropic"
    generative = True

    def __init__(self, model: str, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def _complete(self, system: str, messages: List[Dict[str, str]],
                  max_tokens: int = 1200) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def explain(self, brief, candidates, constraints):
        raw = self._complete(
            "You rank property listings honestly. Reply with JSON only.",
            [{"role": "user", "content": _RATIONALE_PROMPT.format(
                brief=brief, candidates=json.dumps(candidates, default=str))}],
        )
        return _parse_json_array(raw, candidates, constraints)

    def reply(self, system, history):
        return self._complete(system, history, max_tokens=400).strip()


class OpenAILLM:
    name = "openai"
    generative = True

    def __init__(self, model: str, api_key: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _complete(self, system, messages, max_tokens=1200):
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + messages,
        )
        return resp.choices[0].message.content or ""

    def explain(self, brief, candidates, constraints):
        raw = self._complete(
            "You rank property listings honestly. Reply with JSON only.",
            [{"role": "user", "content": _RATIONALE_PROMPT.format(
                brief=brief, candidates=json.dumps(candidates, default=str))}],
        )
        return _parse_json_array(raw, candidates, constraints)

    def reply(self, system, history):
        return self._complete(system, history, max_tokens=400).strip()


def _parse_json_array(raw: str, candidates, constraints) -> List[Dict[str, str]]:
    """Tolerant parse; falls back to the rule-based explanation on failure.

    A malformed model reply must not take the search endpoint down.
    """
    text = raw.strip()
    if "```" in text:
        chunks = text.split("```")
        text = max(chunks, key=len).lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            by_id = {str(o.get("id")): o for o in parsed if isinstance(o, dict)}
            out = []
            for c in candidates:
                hit = by_id.get(str(c["id"]))
                out.append({
                    "id": c["id"],
                    "why": (hit or {}).get("why") or _rule_based_reason(c, constraints),
                    "concern": (hit or {}).get("concern", ""),
                })
            return out
        except (ValueError, TypeError):
            pass
    return StubLLM().explain("", candidates, constraints)


_llm = None


def get_llm():
    global _llm
    if _llm is not None:
        return _llm

    provider = settings.LLM_PROVIDER.lower()
    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset.")
        _llm = AnthropicLLM(settings.ANTHROPIC_MODEL, settings.ANTHROPIC_API_KEY)
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is unset.")
        _llm = OpenAILLM(settings.OPENAI_MODEL, settings.OPENAI_API_KEY)
    else:
        _llm = StubLLM()
    return _llm
