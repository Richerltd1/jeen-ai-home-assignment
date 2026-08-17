# Part 2 — Document Indexing & Semantic Retrieval

A Python module that ingests a **PDF or DOCX** file, extracts clean text, splits it
into chunks using one of **three strategies**, embeds the chunks with the Gemini
API, stores them in **PostgreSQL + pgvector**, and answers natural-language
queries by vector similarity search.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Database](#database)
- [Usage](#usage)
- [Chunking strategies](#chunking-strategies)
- [Sample output](#sample-output)
- [Error handling](#error-handling)
- [Design notes](#design-notes)
- [Project structure](#project-structure)

---

## Requirements

- Python 3.10+
- A PostgreSQL database with the [`pgvector`](https://github.com/pgvector/pgvector)
  extension available
- A Google AI Studio API key ([get one here](https://aistudio.google.com/apikey))

---

## Installation

```bash
git clone https://github.com/Richerltd1/jeen-ai-home-assignment.git
cd jeen-ai-home-assignment/part2-indexing

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## Environment variables

Secrets are read from a `.env` file and never from the source. Copy the template
and fill it in:

```bash
cp .env.example .env
```

| Variable         | Required | Description                                                        |
| ---------------- | -------- | ------------------------------------------------------------------ |
| `GEMINI_API_KEY` | yes      | Google AI Studio API key, used for `gemini-embedding-001`.           |
| `POSTGRES_URL`   | yes      | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/db`. |

`.env` is listed in `.gitignore`. No key or connection string is ever printed —
error messages that could contain a DSN are redacted (see `store._safe_error`).

---

## Database

### Option A — local PostgreSQL via Docker (recommended)

```bash
docker compose up -d
```

This starts `pgvector/pgvector:pg17` on port 5432 with database `docdex`, matching
the default `POSTGRES_URL` in `.env.example`.

### Option B — any hosted PostgreSQL

Point `POSTGRES_URL` at it. The role must be allowed to run
`CREATE EXTENSION IF NOT EXISTS vector`.

### Schema

The table and its indexes are created automatically on first run. No migration
step is required.

```sql
CREATE TABLE document_chunks (
    id             BIGSERIAL PRIMARY KEY,
    chunk_text     TEXT        NOT NULL,
    embedding      vector(1536) NOT NULL,
    filename       TEXT        NOT NULL,
    split_strategy TEXT        NOT NULL,
    chunk_index    INTEGER     NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX document_chunks_filename_idx
    ON document_chunks (filename);
```

`chunk_index` is beyond the required columns; it preserves document order so a
retrieved chunk can be located in its source, and makes results reproducible.

---

## Usage

### Indexing

```bash
python index_documents.py --file ./docs/example.pdf --strategy paragraph
```

| Flag           | Default     | Description                                              |
| -------------- | ----------- | -------------------------------------------------------- |
| `--file`       | *required*  | Path to a `.pdf` or `.docx` file.                         |
| `--strategy`   | `paragraph` | `fixed`, `sentence`, or `paragraph`.                      |
| `--chunk-size` | `1000`      | Maximum chunk length in characters.                       |
| `--overlap`    | `200`       | Character overlap between chunks (used by `fixed`).       |

Re-indexing the same file with the same strategy is **idempotent** — old rows for
that `(filename, split_strategy)` pair are replaced inside a single transaction,
so an interrupted run never leaves a half-written index.

### Searching

```bash
python search.py --query "login issue"
```

| Flag         | Default    | Description                                    |
| ------------ | ---------- | ---------------------------------------------- |
| `--query`    | *required* | Natural-language query.                        |
| `--top-k`    | `5`        | Number of results.                             |
| `--filename` | *all*      | Restrict to one source file.                   |
| `--strategy` | *all*      | Restrict to one chunking strategy.             |
| `--full`     | off        | Print full chunk text instead of a preview.    |

---

## Chunking strategies

| Strategy    | How it splits                                    | Best for                                        |
| ----------- | ------------------------------------------------ | ----------------------------------------------- |
| `fixed`     | Sliding window of `chunk-size` with `overlap`     | Dense prose with no clear structure              |
| `sentence`  | Sentence boundaries, packed to the size budget    | Precise retrieval, Q&A over facts                |
| `paragraph` | Blank-line paragraphs, packed to the size budget  | Structured documents — the default               |

Two details worth noting:

**Packing, not one-chunk-per-unit.** `sentence` and `paragraph` group consecutive
units up to the size budget rather than emitting one chunk per sentence or
paragraph. A four-word sentence makes a near-useless embedding — too little
context to match a real query. Any single unit larger than the budget is
hard-split by the fixed-size splitter, so no chunk ever exceeds `--chunk-size`.

**PDF paragraph reflow.** A PDF stores visual *lines*, not paragraphs — extracted
text has a newline every ~90 characters and no blank lines. Without
reconstruction, `paragraph` would see one enormous block and degenerate into
`fixed`, making two of the three strategies identical. `extract.reflow_paragraphs`
rebuilds the structure by exploiting how wrapped text looks: every line in a
paragraph fills the column width *except the last*, so a short line ends a
paragraph. On the sample document this recovers 16 paragraphs from 2.

---

## Sample output

All output below is copied verbatim from a real run against PostgreSQL 16.2 with
pgvector 0.6.2, embedding the included `docs/example.pdf` with
`gemini-embedding-001`.

### Indexing

```console
$ python index_documents.py --file ./docs/example.pdf --strategy paragraph
Reading      : /path/to/part2-indexing/docs/example.pdf
Extracted    : 6,073 characters
Chunked      : 8 chunks via 'paragraph' (avg 780 chars)
Embedding    : gemini-embedding-001 @ 1536 dimensions
Embedded     : 8 vectors
Stored       : 8 chunks for 'example.pdf' [paragraph]
Done.
```

The same document under each strategy:

| Strategy    | Chunks | Avg chars |
| ----------- | ------ | --------- |
| `fixed`     | 8      | 927       |
| `sentence`  | 7      | 864       |
| `paragraph` | 8      | 780       |

### Searching

```console
$ python search.py --query "login issue" --top-k 3 --strategy paragraph

Query: "login issue"
========================================================================================

[1] similarity 0.7047   id=2
    example.pdf  |  strategy=paragraph  |  chunk #1  |  2026-08-17 12:30
    A login issue is any failure that prevents an authenticated user from reaching their
    dashboard. The most common cause by a wide margin is an expired session token:
    Nimbus sessions are valid for thirty days, after which the refresh token is rejected
    and the user is returned to the sign-in screen without an explanatory message. Ask
    the ...

[2] similarity 0.6766   id=3
    example.pdf  |  strategy=paragraph  |  chunk #2  |  2026-08-17 12:30
    mail after three hard bounces. Login issues are classified as High priority because
    they block all product usage. The target first response time is one hour, and the
    target resolution time is four hours. Escalate to the identity team if the customer
    uses single sign-on and the error mentions a SAML assertion, since those failures
    ...

[3] similarity 0.6572   id=1
    example.pdf  |  strategy=paragraph  |  chunk #0  |  2026-08-17 12:30
    Nimbus Support Knowledge Base About this document This knowledge base documents the
    standard resolution procedures used by the Nimbus customer support team. Each
    section covers one request category, the diagnostic steps an agent should follow,
    the expected resolution time, and the conditions under which a request must be
    escalated to ...

========================================================================================
3 result(s) from 23 indexed chunks.
```

Note the ranking: the dedicated "Login Issues" section scores **0.7047**, the
paragraph continuing it scores **0.6766**, and the generic document preamble —
which contains no login vocabulary but is topically adjacent — scores **0.6572**.

### Rows as stored in PostgreSQL

```console
$ psql $POSTGRES_URL -c "SELECT id, filename, split_strategy, chunk_index,
                                left(chunk_text, 42) AS preview,
                                vector_dims(embedding) AS dims
                         FROM document_chunks ORDER BY id LIMIT 5;"

 id |  filename   | split_strategy | chunk_index |                  preview                   | dims
----+-------------+----------------+-------------+--------------------------------------------+------
  1 | example.pdf | paragraph      |           0 | Nimbus Support Knowledge Base About this d | 1536
  2 | example.pdf | paragraph      |           1 | A login issue is any failure that prevents | 1536
  3 | example.pdf | paragraph      |           2 | mail after three hard bounces. Login issue | 1536
  4 | example.pdf | paragraph      |           3 | 2. Billing Billing requests cover invoices | 1536
  5 | example.pdf | paragraph      |           4 | Technical support covers product defects,  | 1536
(5 rows)
```

### Error handling, demonstrated

```console
$ python index_documents.py --file ./docs/nope.pdf
Reading      : /path/to/docs/nope.pdf
Error: File not found: /path/to/docs/nope.pdf                                  # exit 1

$ python index_documents.py --file ./notes.txt
Error: Unsupported file type '.txt'. Supported types: .docx, .pdf.             # exit 1

$ python index_documents.py --file ./scanned.pdf
Error: No extractable text found in scanned.pdf. If this is a scanned document
       it would need OCR, which is not supported.                              # exit 1

$ python index_documents.py --file ./docs/example.pdf     # with an invalid key
Error: Embedding request failed after 3 attempts (batch starting at chunk 0):
       400 INVALID_ARGUMENT ... API key not valid.                             # exit 1

$ python search.py --query "test"                         # database unreachable
Error: Could not connect to PostgreSQL: connection failed: ... Connection refused.
       Check that POSTGRES_URL is correct and the server is reachable.         # exit 1

$ python search.py --query "quantum astrophysics" --filename "absent.pdf"
No results matching filename=absent.pdf. The index holds 23 chunks -- try a
broader query or remove the filters.                                           # exit 0
```

The last case exits **0**, not 1: an empty result set is a valid answer, not a
failure. The message also distinguishes "the index is empty" from "your filters
excluded everything", which are different problems with different fixes.

---

## Error handling

Every expected failure is raised as a typed exception (`docdex/errors.py`), caught
at the CLI boundary, and reported as one readable line on stderr with exit code 1.
No stack traces and no secrets reach the user.

| Case                       | Exception                  | Message                                                  |
| -------------------------- | -------------------------- | -------------------------------------------------------- |
| Missing file               | `FileNotFoundErrorDocdex`  | `File not found: <path>`                                  |
| Unsupported file type      | `UnsupportedFileTypeError` | `Unsupported file type '.txt'. Supported types: .docx, .pdf.` |
| Document with no text      | `NoTextExtractedError`     | `No extractable text found in <name>` (+ OCR hint)        |
| Embedding failure          | `EmbeddingError`           | `Embedding request failed after 3 attempts: <reason>`     |
| Database connection failure| `DatabaseConnectionError`  | `Could not connect to PostgreSQL: <reason>`               |
| pgvector unavailable       | `DatabaseConnectionError`  | `Could not enable the pgvector extension: <reason>`       |
| Empty search results       | *(not an error)*           | Distinguishes "index is empty" from "no match for filters" |
| Missing env variable       | `ConfigurationError`       | `Missing required environment variable(s): ...`           |

Transient embedding failures are retried three times with exponential backoff
before being surfaced.

---

## Design notes

**Why 1536 dimensions and not Gemini's 3072 default.** pgvector's HNSW and IVFFlat
indexes support at most 2000 dimensions. At 3072 the embedding column cannot be
indexed at all and every search degrades to a sequential scan over the table.
`gemini-embedding-001` is trained for Matryoshka truncation, so 1536 costs very
little quality and keeps the index usable.

**Why embeddings are re-normalised.** Gemini returns unit-length vectors only at
its native 3072 dimensions. A truncated unit vector is no longer unit length, so
`embeddings._normalise` rescales it. Skipping this quietly distorts every cosine
distance.

**Why documents and queries are embedded differently.** Chunks are embedded with
`task_type="RETRIEVAL_DOCUMENT"` and queries with `"RETRIEVAL_QUERY"`. Gemini
projects the two into a shared space optimised for matching questions against
passages, which beats embedding both sides identically.

**Why `::vector` casts appear in the search SQL.** A bare placeholder carries no
column context, so PostgreSQL infers `double precision[]` and finds no `<=>`
operator. The cast also lets the planner use the HNSW index.

---

## Project structure

```
part2-indexing/
├── index_documents.py     # CLI: extract → chunk → embed → store
├── search.py              # CLI: embed query → vector search → render
├── docdex/
│   ├── config.py          # env loading, model + dimension constants
│   ├── errors.py          # one typed exception per failure mode
│   ├── extract.py         # PDF/DOCX text extraction, cleaning, reflow
│   ├── chunking.py        # the three strategies
│   ├── embeddings.py      # Gemini client, batching, retry, normalisation
│   └── store.py           # all SQL: schema, insert, similarity search
├── docs/example.pdf       # sample document
├── docker-compose.yml     # local PostgreSQL + pgvector
├── requirements.txt
└── .env.example
```

Each module has a single responsibility and no knowledge of the layers around it:
`extract` knows file formats, `chunking` knows text, `embeddings` knows Gemini,
`store` knows SQL. Swapping the vector store or the embedding provider touches
exactly one file.
