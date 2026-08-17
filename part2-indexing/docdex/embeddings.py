"""Embedding generation via the Gemini API.

Two details here are deliberate and worth calling out:

1. **Asymmetric task types.** Documents are embedded with `RETRIEVAL_DOCUMENT`
   and queries with `RETRIEVAL_QUERY`. Gemini projects the two into a shared
   space optimised for matching questions against passages, which measurably
   beats embedding both sides identically.

2. **Explicit re-normalisation.** `gemini-embedding-001` returns unit-length
   vectors only at its native 3072 dimensions. We truncate to 1536 (see
   `config.EMBEDDING_DIMENSIONS`), and a truncated unit vector is no longer unit
   length -- so we renormalise. Skipping this quietly distorts cosine distance.
"""

from __future__ import annotations

import math
import time
from typing import Sequence

from .config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    Settings,
)
from .errors import EmbeddingError

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


class EmbeddingClient:
    """Thin wrapper over the Gemini embeddings endpoint."""

    def __init__(self, settings: Settings) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise EmbeddingError(
                "The google-genai package is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._client = genai.Client(api_key=settings.gemini_api_key)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed document chunks for storage."""
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        return self._embed([text], task_type="RETRIEVAL_QUERY")[0]

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        """Embed `texts` in batches, retrying transient API failures.

        Raises:
            EmbeddingError: on empty input, or after `_MAX_ATTEMPTS` failures.
        """
        if not texts:
            raise EmbeddingError("Nothing to embed: received an empty list of texts.")

        from google.genai import types

        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=EMBEDDING_DIMENSIONS,
        )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = list(texts[start : start + EMBEDDING_BATCH_SIZE])
            vectors.extend(self._embed_batch(batch, config, start))
        return vectors

    def _embed_batch(self, batch: list[str], config, offset: int) -> list[list[float]]:
        """Embed one batch with bounded exponential backoff."""
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=batch,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - SDK raises many error types
                last_error = exc
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    continue
                break

            embeddings = getattr(response, "embeddings", None)
            if not embeddings or len(embeddings) != len(batch):
                raise EmbeddingError(
                    f"Gemini returned {len(embeddings or [])} embeddings for "
                    f"{len(batch)} inputs (batch starting at chunk {offset})."
                )
            return [_normalise(list(item.values)) for item in embeddings]

        raise EmbeddingError(
            f"Embedding request failed after {_MAX_ATTEMPTS} attempts "
            f"(batch starting at chunk {offset}): {last_error}"
        ) from last_error


def _normalise(vector: list[float]) -> list[float]:
    """Scale a vector to unit length, required after dimension truncation."""
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        raise EmbeddingError("Gemini returned a zero-magnitude embedding.")
    return [value / magnitude for value in vector]
