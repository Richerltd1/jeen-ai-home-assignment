"""Three chunking strategies, behind one interface.

    fixed     -- fixed-size windows with a character overlap
    sentence  -- sentence boundaries, packed up to a size budget
    paragraph -- blank-line-delimited paragraphs, packed up to a size budget

`sentence` and `paragraph` both *pack* small units together rather than emitting
one chunk per unit. A lone 4-word sentence makes a near-useless embedding: too
little context to match a real query against. Packing keeps chunks semantically
whole while still worth embedding. Any unit that exceeds the budget on its own is
hard-split by the fixed-size splitter so no chunk is ever oversized.
"""

from __future__ import annotations

import re

# Defaults are in characters, not tokens: they stay accurate across languages
# (Hebrew tokenises very differently from English) and need no tokenizer.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 200

STRATEGIES = ("fixed", "sentence", "paragraph")

# Sentence terminator followed by whitespace. Guarded with a lookbehind against
# common abbreviations so "Dr. Cohen" or "e.g. this" do not split.
_ABBREVIATIONS = r"(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bMs)(?<!\bSt)(?<!\be\.g)(?<!\bi\.e)"
_SENTENCE_BOUNDARY = re.compile(rf"{_ABBREVIATIONS}(?<=[.!?])\s+")

_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")


def chunk_text(
    text: str,
    strategy: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split `text` into chunks using the named strategy.

    Args:
        text: Cleaned document text.
        strategy: One of `STRATEGIES`.
        chunk_size: Maximum chunk length in characters.
        overlap: Character overlap between consecutive chunks (`fixed` only).

    Returns:
        Non-empty chunks, in document order.

    Raises:
        ValueError: unknown strategy, or nonsensical size/overlap.
    """
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Choose one of: {', '.join(STRATEGIES)}."
        )
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0.")
    if overlap < 0:
        raise ValueError("overlap must be 0 or greater.")
    if overlap >= chunk_size:
        # Otherwise the sliding window never advances and the loop never ends.
        raise ValueError("overlap must be smaller than chunk_size.")

    text = (text or "").strip()
    if not text:
        return []

    if strategy == "fixed":
        return _chunk_fixed(text, chunk_size, overlap)
    if strategy == "sentence":
        return _pack(_split_sentences(text), chunk_size, overlap)
    return _pack(_split_paragraphs(text), chunk_size, overlap)


def _chunk_fixed(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Slide a fixed-size window across the text, stepping by size - overlap.

    The window prefers to end on a whitespace boundary so chunks do not begin or
    end mid-word, which would corrupt the embedding.
    """
    chunks: list[str] = []
    step = chunk_size - overlap
    length = len(text)
    start = 0

    while start < length:
        # Align the *start* to a word boundary. The overlap step lands at an
        # arbitrary offset, which without this would begin the chunk on a word
        # fragment ("...n out fully" instead of "...sign out fully") and corrupt
        # the embedding. Costs at most one word of overlap, which is slack we have.
        if start > 0:
            while start < length and not text[start].isspace():
                start += 1
            while start < length and text[start].isspace():
                start += 1
            if start >= length:
                break

        end = min(start + chunk_size, length)

        # Retreat the end to the last space in the final 20% of the window,
        # unless this is the last chunk (nothing after it to align to).
        if end < length:
            pivot = text.rfind(" ", max(start + 1, end - chunk_size // 5), end)
            if pivot != -1:
                end = pivot

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= length:
            break

        # Guarantee forward progress even if the boundary alignment pulled the
        # candidate start back to or behind the current one.
        next_start = max(start + step, end - overlap)
        start = next_start if next_start > start else end

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Split into sentences, treating each paragraph independently."""
    sentences: list[str] = []
    for paragraph in _split_paragraphs(text):
        for sentence in _SENTENCE_BOUNDARY.split(paragraph):
            cleaned = sentence.strip()
            if cleaned:
                sentences.append(cleaned)
    return sentences


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines into paragraphs."""
    return [p.strip() for p in _PARAGRAPH_BOUNDARY.split(text) if p.strip()]


def _pack(units: list[str], chunk_size: int, overlap: int) -> list[str]:
    """Greedily pack units into chunks without exceeding `chunk_size`.

    A single unit longer than the budget is hard-split with the fixed-size
    splitter, guaranteeing the invariant that no chunk exceeds `chunk_size`.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal buffer, length
        if buffer:
            chunks.append(" ".join(buffer).strip())
            buffer, length = [], 0

    for unit in units:
        if len(unit) > chunk_size:
            flush()
            chunks.extend(_chunk_fixed(unit, chunk_size, overlap))
            continue

        # +1 accounts for the space inserted when joining.
        projected = length + len(unit) + (1 if buffer else 0)
        if projected > chunk_size:
            flush()

        buffer.append(unit)
        length += len(unit) + (1 if len(buffer) > 1 else 0)

    flush()
    return [c for c in chunks if c]
