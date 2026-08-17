"""Configuration loaded from the environment.

Secrets live in `.env` (gitignored) and are read through here only. Nothing in
this module ever logs or prints a value -- `__repr__` is deliberately not
implemented on the settings object for that reason.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .errors import ConfigurationError

# Gemini's current embedding model. It supports Matryoshka truncation, so we can
# ask for fewer dimensions than the 3072 default.
EMBEDDING_MODEL = "gemini-embedding-001"

# Why 1536 and not the 3072 default: pgvector's HNSW and IVFFlat indexes only
# support up to 2000 dimensions. At 3072 the embedding column cannot be indexed
# at all, so every search degrades to a sequential scan. 1536 keeps us indexable
# with a negligible quality loss (Gemini is trained for truncation).
EMBEDDING_DIMENSIONS = 1536

# Gemini rejects oversized batches; 100 inputs per request is well inside limits.
EMBEDDING_BATCH_SIZE = 100

TABLE_NAME = "document_chunks"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Treat every field as a secret."""

    gemini_api_key: str
    postgres_url: str


def load_settings() -> Settings:
    """Read settings from `.env` + the process environment.

    Raises:
        ConfigurationError: if either required variable is missing or blank.
    """
    load_dotenv()

    gemini_api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    postgres_url = (os.getenv("POSTGRES_URL") or "").strip()

    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", gemini_api_key),
            ("POSTGRES_URL", postgres_url),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    return Settings(gemini_api_key=gemini_api_key, postgres_url=postgres_url)
