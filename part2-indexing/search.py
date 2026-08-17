#!/usr/bin/env python3
"""Semantic search over indexed document chunks.

Usage:
    python search.py --query "login issue"
    python search.py --query "billing problem" --top-k 3 --strategy paragraph
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from docdex.chunking import STRATEGIES
from docdex.config import load_settings
from docdex.embeddings import EmbeddingClient
from docdex.errors import DocdexError
from docdex.store import connect, count_chunks, ensure_schema, search

_PREVIEW_WIDTH = 88


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search.py",
        description="Find the document chunks most semantically similar to a query.",
    )
    parser.add_argument("--query", required=True, help="Natural-language search query.")
    parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results to return (default: 5)."
    )
    parser.add_argument("--filename", help="Restrict results to a single source file.")
    parser.add_argument(
        "--strategy",
        choices=STRATEGIES,
        help="Restrict results to chunks produced by one chunking strategy.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print full chunk text instead of a truncated preview.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.query.strip():
        print("Error: --query must not be empty.", file=sys.stderr)
        return 1
    if args.top_k <= 0:
        print("Error: --top-k must be greater than 0.", file=sys.stderr)
        return 1

    try:
        settings = load_settings()

        query_vector = EmbeddingClient(settings).embed_query(args.query)

        with connect(settings) as connection:
            ensure_schema(connection)
            results = search(
                connection,
                query_vector,
                limit=args.top_k,
                filename=args.filename,
                split_strategy=args.strategy,
            )
            total_indexed = count_chunks(connection)

        _render(args, results, total_indexed)
        return 0

    except DocdexError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _render(args, results, total_indexed: int) -> None:
    """Print search results, distinguishing 'nothing indexed' from 'no match'."""
    print(f'\nQuery: "{args.query}"')
    print("=" * _PREVIEW_WIDTH)

    if not results:
        if total_indexed == 0:
            print(
                "No results: the index is empty.\n"
                "Index a document first, e.g.\n"
                "  python index_documents.py --file ./docs/example.pdf --strategy paragraph"
            )
        else:
            filters = [
                f"filename={args.filename}" if args.filename else None,
                f"strategy={args.strategy}" if args.strategy else None,
            ]
            active = ", ".join(f for f in filters if f)
            suffix = f" matching {active}" if active else ""
            print(
                f"No results{suffix}. "
                f"The index holds {total_indexed:,} chunks -- try a broader query "
                "or remove the filters."
            )
        print()
        return

    for rank, result in enumerate(results, start=1):
        body = (
            result.chunk_text
            if args.full
            else textwrap.shorten(result.chunk_text, width=340, placeholder=" ...")
        )
        print(f"\n[{rank}] similarity {result.similarity:.4f}   id={result.id}")
        print(
            f"    {result.filename}  |  strategy={result.split_strategy}  "
            f"|  chunk #{result.chunk_index}  "
            f"|  {result.created_at:%Y-%m-%d %H:%M}"
        )
        print(textwrap.indent(textwrap.fill(body, width=_PREVIEW_WIDTH - 4), "    "))

    print("\n" + "=" * _PREVIEW_WIDTH)
    print(f"{len(results)} result(s) from {total_indexed:,} indexed chunks.\n")


if __name__ == "__main__":
    raise SystemExit(main())
