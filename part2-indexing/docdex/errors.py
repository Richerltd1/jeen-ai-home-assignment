"""Typed errors for the indexing and search pipeline.

Every failure mode named in the assignment brief maps to exactly one exception
here. The CLI entry points catch `DocdexError` at the top level and turn it into
a readable message plus a non-zero exit code, so no stack trace ever reaches the
user and no secret is ever echoed back.
"""


class DocdexError(Exception):
    """Base class for every expected (i.e. handled) failure."""


class FileNotFoundErrorDocdex(DocdexError):
    """The path passed to --file does not exist or is not a regular file."""


class UnsupportedFileTypeError(DocdexError):
    """The file extension is not one of the supported types (.pdf, .docx)."""


class NoTextExtractedError(DocdexError):
    """The document parsed successfully but contained no extractable text.

    Typically a scanned/image-only PDF, which would need OCR (out of scope).
    """


class EmbeddingError(DocdexError):
    """The Gemini embedding API call failed, or returned an unusable payload."""


class DatabaseConnectionError(DocdexError):
    """Could not connect to PostgreSQL, or pgvector is not installed."""


class ConfigurationError(DocdexError):
    """A required environment variable is missing or empty."""
