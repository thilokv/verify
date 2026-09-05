"""Runtime configuration.

Two supported modes, selected by environment:

  dev  (default)  SQLite + local hash embeddings. Zero external services,
                  runs on a laptop with nothing installed. Similarity is
                  lexical, NOT semantic — good enough to exercise the
                  pipeline, not good enough to ship.

  prod            PostgreSQL + pgvector + a real embedding provider,
                  exactly as specified in the blueprint (§4.2).

Everything downstream reads these settings rather than os.getenv, so the
storage and model providers can be swapped without touching the routers.
"""

import os
from typing import Optional


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # ---- storage -------------------------------------------------------
    # "sqlite" | "pgvector"
    VECTOR_BACKEND: str = os.getenv("VECTOR_BACKEND", "sqlite")

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./realestate.db",
    )

    # ---- embeddings ----------------------------------------------------
    # "hash" | "openai" | "voyage"
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "hash")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1536"))

    # ---- language model (match rationale, WhatsApp replies) ------------
    # "stub" | "anthropic" | "openai"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "stub")
    # Pin model ids in config, never in prose. See blueprint §8.2.
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    VOYAGE_API_KEY: Optional[str] = os.getenv("VOYAGE_API_KEY")

    # ---- WhatsApp (Meta Cloud API) -------------------------------------
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token")
    WHATSAPP_TOKEN: Optional[str] = os.getenv("WHATSAPP_TOKEN")
    WHATSAPP_PHONE_ID: Optional[str] = os.getenv("WHATSAPP_PHONE_ID")
    WHATSAPP_APP_SECRET: Optional[str] = os.getenv("WHATSAPP_APP_SECRET")

    # ---- auth ----------------------------------------------------------
    # Comma-separated staff keys. If blank, a random key is generated at boot
    # and logged — the service never falls open on its own.
    STAFF_API_KEYS: str = os.getenv("STAFF_API_KEYS", "")
    REQUIRE_AUTH: bool = _bool("REQUIRE_AUTH", True)

    # ---- document vault ------------------------------------------------
    # Encryption key for uploaded title documents. Keep in a secrets manager.
    DOCUMENT_KEY: str = os.getenv("DOCUMENT_KEY", "")

    # ---- behaviour -----------------------------------------------------
    # Only verified stock reaches buyers. Blueprint §4.1 FR-5.
    ENFORCE_VERIFICATION_GATE: bool = _bool("ENFORCE_VERIFICATION_GATE", True)
    DEFAULT_RESULT_LIMIT: int = int(os.getenv("DEFAULT_RESULT_LIMIT", "3"))

    @property
    def is_pgvector(self) -> bool:
        return self.VECTOR_BACKEND == "pgvector"

    def describe(self) -> dict:
        """Non-secret summary, surfaced at /api/v1/health."""
        return {
            "vector_backend": self.VECTOR_BACKEND,
            "embedding_provider": self.EMBEDDING_PROVIDER,
            "embedding_dim": self.EMBEDDING_DIM,
            "llm_provider": self.LLM_PROVIDER,
            "verification_gate": self.ENFORCE_VERIFICATION_GATE,
            "semantic_search": self.EMBEDDING_PROVIDER != "hash",
            "auth_required": self.REQUIRE_AUTH,
            "documents_encrypted": bool(self.DOCUMENT_KEY),
        }


settings = Settings()
