"""Embedding providers — Pin 2, first half.

The blueprint specifies OpenAI text-embedding-3-small (1536 dims). That is
the production path and it is implemented below. Anthropic does not offer an
embeddings endpoint, so a Claude subscription alone does not cover this —
Voyage is included as the usual alternative.

A dependency-free `hash` provider is the default so the pipeline runs on a
laptop with no API key. It is LEXICAL, not semantic: it scores word overlap.
"Villa" and "bungalow" are unrelated to it. Never ship it to buyers — the
/api/v1/health endpoint reports semantic_search=false while it is active.
"""

import hashlib
import math
import re
from typing import List, Sequence

from .config import settings

_TOKEN = re.compile(r"[a-z0-9]+")

# Words that carry no discriminating signal in a property corpus.
_STOP = {
    "the", "a", "an", "and", "or", "for", "with", "in", "on", "at", "to",
    "of", "is", "are", "be", "near", "my", "me", "i", "we", "want", "need",
    "looking", "find", "show", "property", "house", "home",
}


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


def _l2(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashEmbedder:
    """Deterministic hashing vectoriser. No network, no model, no key.

    Unigrams and bigrams are hashed into a fixed-width vector with signed
    buckets (the standard hashing-trick sign to limit collision bias), then
    L2-normalised so cosine similarity is a plain dot product.
    """

    name = "hash"

    def __init__(self, dim: int):
        self.dim = dim

    def _add(self, vec: List[float], term: str, weight: float) -> None:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        bucket = h % self.dim
        sign = 1.0 if (h >> 17) & 1 else -1.0
        vec[bucket] += sign * weight

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            toks = _tokens(text)
            for t in toks:
                self._add(vec, t, 1.0)
            # Bigrams capture "east facing", "ready move", "gated community".
            for a, b in zip(toks, toks[1:]):
                self._add(vec, a + "_" + b, 0.6)
            out.append(_l2(vec))
        return out


class OpenAIEmbedder:
    """Production path from the blueprint: text-embedding-3-small, 1536 dims."""

    name = "openai"

    def __init__(self, model: str, dim: int, api_key: str):
        from openai import OpenAI  # imported lazily

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model, input=list(texts))
        # The API preserves input order, but sort by index to be certain.
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


class VoyageEmbedder:
    """Alternative provider, commonly paired with Claude deployments."""

    name = "voyage"

    def __init__(self, model: str, dim: int, api_key: str):
        import voyageai  # imported lazily

        self.client = voyageai.Client(api_key=api_key)
        self.model = model or "voyage-3"
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return self.client.embed(list(texts), model=self.model).embeddings


_embedder = None


def get_embedder():
    """Build the configured provider once, then reuse it."""
    global _embedder
    if _embedder is not None:
        return _embedder

    provider = settings.EMBEDDING_PROVIDER.lower()

    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is unset. "
                "Set the key, or fall back to EMBEDDING_PROVIDER=hash for local dev."
            )
        _embedder = OpenAIEmbedder(
            settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM, settings.OPENAI_API_KEY
        )
    elif provider == "voyage":
        if not settings.VOYAGE_API_KEY:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is unset.")
        _embedder = VoyageEmbedder(
            settings.EMBEDDING_MODEL, settings.EMBEDDING_DIM, settings.VOYAGE_API_KEY
        )
    else:
        _embedder = HashEmbedder(settings.EMBEDDING_DIM)

    return _embedder


def embed_one(text: str) -> List[float]:
    return get_embedder().embed([text])[0]


def embed_many(texts: Sequence[str]) -> List[List[float]]:
    if not texts:
        return []
    return get_embedder().embed(texts)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Used by the SQLite backend; pgvector does this in SQL."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
