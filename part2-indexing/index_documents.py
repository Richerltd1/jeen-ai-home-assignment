#!/usr/bin/env python3
"""Index a PDF or DOCX file into PostgreSQL/pgvector.

Usage:
    python index_documents.py --file ./docs/example.pdf --strategy paragraph

Every expected failure is reported as a single readable line on stderr with a
non-zero exit code; no stack traces and no secrets are ever printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docdex.chunking import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    STRATEGIES,
    chunk_text,
)
from docdex.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, load_settings
from docdex.embeddings import EmbeddingClient
from docdex.errors import DocdexError
from docdex.extract import extract_text
from docdex.store import connect, ensure_schema, replace_document_chunks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="index_documents.py",
        description="Extract, chunk, embed and store a PDF or DOCX document.",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to the .pdf or .docx file to index.",
    )
    parser.add_argument(
        "--strategy",
        default="paragraph",
        choices=STRATEGIES,
        help="Chunking strategy to use (default: paragraph).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Maximum chunk size in characters (default: {DEFAULT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=DEFAULT_OVERLAP,
        help=(
            "Character overlap between consecutive chunks "
            f"(default: {DEFAULT_OVERLAP}; used by the 'fixed' strategy)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # stdout is block-buffered when redirected while stderr is not, which would
    # print progress lines *after* an error message in captured output. Line
    # buffering keeps the two streams interleaved in the order they happened.
    sys.stdout.reconfigure(line_buffering=True)

    try:
        settings = load_settings()

        source: Path = args.file.expanduser().resolve()
        print(f"Reading      : {source}")
        text = extract_text(source)
        print(f"Extracted    : {len(text):,} characters")

        chunks = chunk_text(
            text,
            strategy=args.strategy,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
        )
        if not chunks:
            raise DocdexError(
                f"The '{args.strategy}' strategy produced no chunks from {source.name}."
            )
        average = sum(len(c) for c in chunks) // len(chunks)
        print(
            f"Chunked      : {len(chunks)} chunks "
            f"via '{args.strategy}' (avg {average:,} chars)"
        )

        print(f"Embedding    : {EMBEDDING_MODEL} @ {EMBEDDING_DIMENSIONS} dimensions")
        vectors = EmbeddingClient(settings).embed_documents(chunks)
        print(f"Embedded     : {len(vectors)} vectors")

        with connect(settings) as connection:
            ensure_schema(connection)
            stored = replace_document_chunks(
                connection,
                filename=source.name,
                split_strategy=args.strategy,
                chunks=chunks,
                embeddings=vectors,
            )

        print(f"Stored       : {stored} chunks for '{source.name}' [{args.strategy}]")
        print("Done.")
        return 0

    except DocdexError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
