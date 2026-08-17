"""PostgreSQL + pgvector persistence layer.

Holds every piece of SQL in the project. Callers work with plain Python types
and never see a cursor, so swapping the vector store later touches this file
only.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Sequence

from .config import EMBEDDING_DIMENSIONS, TABLE_NAME, Settings
from .errors import DatabaseConnectionError

# psycopg composes this safely; TABLE_NAME is a module constant, never user input.
_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id             BIGSERIAL PRIMARY KEY,
    chunk_text     TEXT        NOT NULL,
    embedding      vector({EMBEDDING_DIMENSIONS}) NOT NULL,
    filename       TEXT        NOT NULL,
    split_strategy TEXT        NOT NULL,
    chunk_index    INTEGER     NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# HNSW with cosine distance: embeddings are unit-normalised, and cosine is what
# Gemini's embedding space is trained for. This index is the reason we truncate
# to 1536 dimensions -- HNSW refuses anything above 2000.
_CREATE_INDEXES = f"""
CREATE INDEX IF NOT EXISTS {TABLE_NAME}_embedding_idx
    ON {TABLE_NAME} USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS {TABLE_NAME}_filename_idx
    ON {TABLE_NAME} (filename);
"""

_INSERT_CHUNK = f"""
INSERT INTO {TABLE_NAME} (chunk_text, embedding, filename, split_strategy, chunk_index)
VALUES (%s, %s, %s, %s, %s);
"""

_DELETE_EXISTING = f"""
DELETE FROM {TABLE_NAME} WHERE filename = %s AND split_strategy = %s;
"""

# `1 - (a <=> b)` converts pgvector's cosine *distance* into a similarity in
# [0, 1], which is what a human reading the CLI output expects to see.
#
# The `::vector` casts are required, not cosmetic: a bare placeholder carries no
# column context, so Postgres infers `double precision[]` and then finds no
# `<=>` operator for it. Casting also lets the planner use the HNSW index.
_SEARCH = f"""
SELECT id, chunk_text, filename, split_strategy, chunk_index, created_at,
       1 - (embedding <=> %s::vector) AS similarity
FROM {TABLE_NAME}
WHERE (%s::text IS NULL OR filename = %s)
  AND (%s::text IS NULL OR split_strategy = %s)
ORDER BY embedding <=> %s::vector
LIMIT %s;
"""


@dataclass(frozen=True)
class SearchResult:
    """One row returned by a semantic search, ordered by similarity."""

    id: int
    chunk_text: str
    filename: str
    split_strategy: str
    chunk_index: int
    created_at: datetime
    similarity: float


@contextmanager
def connect(settings: Settings) -> Iterator["object"]:
    """Open a psycopg connection with the pgvector adapter registered.

    Raises:
        DatabaseConnectionError: on any connection or adapter failure. The
            connection string is never included in the message -- it contains
            the password.
    """
    try:
        import psycopg
        from pgvector.psycopg import register_vector
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise DatabaseConnectionError(
            "psycopg and pgvector are not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        connection = psycopg.connect(settings.postgres_url, connect_timeout=10)
    except Exception as exc:  # noqa: BLE001 - psycopg raises many error types
        raise DatabaseConnectionError(
            f"Could not connect to PostgreSQL: {_safe_error(exc)}. "
            "Check that POSTGRES_URL is correct and the server is reachable."
        ) from exc

    try:
        # The extension must exist before the psycopg adapter can look up the
        # `vector` type OID. On a fresh database it does not, so creating it here
        # -- rather than in ensure_schema() -- is what makes the very first run
        # work instead of failing with "vector type not found in the database".
        with connection.cursor() as cursor:
            cursor.execute(_CREATE_EXTENSION)
        connection.commit()
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        connection.close()
        raise DatabaseConnectionError(
            f"Could not enable the pgvector extension: {_safe_error(exc)}. "
            "pgvector must be installed on the server, and the connecting role "
            "needs permission to run CREATE EXTENSION."
        ) from exc

    try:
        register_vector(connection)
        yield connection
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        if isinstance(exc, DatabaseConnectionError):
            raise
        raise DatabaseConnectionError(f"Database error: {_safe_error(exc)}") from exc
    finally:
        connection.close()


def ensure_schema(connection) -> None:
    """Create the table and its indexes if absent.

    The `vector` extension itself is created earlier, by `connect()`.

    Raises:
        DatabaseConnectionError: if the schema cannot be created.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(_CREATE_TABLE)
            cursor.execute(_CREATE_INDEXES)
        connection.commit()
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        raise DatabaseConnectionError(
            f"Could not prepare the database schema: {_safe_error(exc)}. "
            "The pgvector extension must be available on this PostgreSQL server."
        ) from exc


def replace_document_chunks(
    connection,
    filename: str,
    split_strategy: str,
    chunks: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> int:
    """Atomically replace all chunks for one (filename, strategy) pair.

    Re-indexing the same file with the same strategy is idempotent: the old rows
    are deleted in the same transaction as the insert, so a crash mid-run leaves
    the previous index intact rather than a half-written one.

    Returns:
        The number of chunks inserted.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must be the same length.")

    try:
        with connection.cursor() as cursor:
            cursor.execute(_DELETE_EXISTING, (filename, split_strategy))
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                cursor.execute(
                    _INSERT_CHUNK,
                    (chunk, list(embedding), filename, split_strategy, index),
                )
        connection.commit()
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        raise DatabaseConnectionError(
            f"Failed to store chunks: {_safe_error(exc)}"
        ) from exc

    return len(chunks)


def search(
    connection,
    query_embedding: Sequence[float],
    limit: int = 5,
    filename: str | None = None,
    split_strategy: str | None = None,
) -> list[SearchResult]:
    """Return the `limit` most similar chunks, optionally filtered.

    An empty list is a valid result (the table may be empty or the filters may
    exclude everything); the caller decides how to present that.
    """
    vector = list(query_embedding)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                _SEARCH,
                (
                    vector,
                    filename, filename,
                    split_strategy, split_strategy,
                    vector,
                    limit,
                ),
            )
            rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        raise DatabaseConnectionError(f"Search query failed: {_safe_error(exc)}") from exc

    return [
        SearchResult(
            id=row[0],
            chunk_text=row[1],
            filename=row[2],
            split_strategy=row[3],
            chunk_index=row[4],
            created_at=row[5],
            similarity=float(row[6]),
        )
        for row in rows
    ]


def count_chunks(connection) -> int:
    """Total number of indexed chunks. Used for friendlier empty-result messages."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {TABLE_NAME};")
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _safe_error(exc: Exception) -> str:
    """Render an exception without leaking the connection string.

    psycopg puts the full DSN -- password included -- into some error messages.
    """
    message = str(exc).strip() or exc.__class__.__name__
    if "://" in message:
        return f"{exc.__class__.__name__} (details withheld: may contain credentials)"
    return message
