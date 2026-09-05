"""Engine, session factory and schema bootstrap."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # FastAPI serves requests on a threadpool; SQLite needs this relaxed.
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create the extension, tables and vector index.

    The HNSW index matters: without it pgvector's cosine_distance falls back
    to a sequential scan across every listing (blueprint §8.1).
    """
    from .models import Base

    if settings.is_pgvector:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(bind=engine)

    if settings.is_pgvector:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS properties_embedding_hnsw "
                    "ON properties USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)"
                )
            )
