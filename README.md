# Jeen.AI — Home Assignment

Eitan Rafael · August 2026

Three deliverables: a business/prompting exercise, a document indexing and
retrieval module, and a multi-agent workflow.

---

## Deliverables

| Part | What it is | Where |
| ---- | ---------- | ----- |
| **1** | Hebrew presentation — three AI use cases for Super-Pharm, deep dive on the agent, OpenAI model comparison, full bot prompt, research summary | [`part1-presentation/`](./part1-presentation/) · *presentation link below* |
| **2** | Python module — PDF/DOCX → clean text → three chunking strategies → Gemini embeddings → PostgreSQL/pgvector → semantic search | [`part2-indexing/`](./part2-indexing/README.md) |
| **3** | Langflow multi-agent support workflow — Orchestrator + Analysis (SQL Tool) + Response (custom Gmail Tool), with dynamic routing | [`part3-langflow/`](./part3-langflow/README.md) |

**Presentation link:** https://gamma.app/docs/1q5q1taq3881164
**Video link:** *(to be added)*

---

## Repository layout

```
├── part1-presentation/
│   └── content-he.md              # source content for the deck (Hebrew)
├── part2-indexing/
│   ├── index_documents.py         # CLI: extract → chunk → embed → store
│   ├── search.py                  # CLI: semantic search
│   ├── docdex/                    # the module
│   ├── docs/example.pdf           # sample document
│   ├── docker-compose.yml         # local PostgreSQL + pgvector
│   └── README.md                  # full setup and sample output
├── part3-langflow/
│   ├── support-multi-agent-flow.json   # the flow export
│   ├── build_flow.py              # builds the flow programmatically
│   ├── prompts.py                 # the three agent system prompts
│   ├── components/custom/         # custom SQL (read-only, JSON) + Gmail Sender tools
│   ├── schema.sql                 # support_requests table + seed data
│   ├── VIDEO-SCRIPT.md            # recording script
│   └── README.md                  # architecture, setup, routing table
├── scripts/check-secrets.sh       # credential scanner
├── .githooks/pre-commit           # blocks commits containing secrets
├── HOW-IT-WORKS.md                # full walkthrough + design rationale
└── SECURITY.md                    # how credentials are handled
```

---

**Deep dive:** [`HOW-IT-WORKS.md`](./HOW-IT-WORKS.md) — a step-by-step walkthrough
of both systems, the reasoning behind every design decision, how the project was
built and in what order, and the known limitations.

## A note on how this was built

Each part was verified by running it, not by inspection. That surfaced a number
of issues that would otherwise have shipped:

**Part 2**

- The fixed-size chunker aligned the *end* of its sliding window to a word
  boundary but not the *start*, so every overlapped chunk began mid-word.
- `register_vector()` ran before `CREATE EXTENSION`, so the very first run
  against a fresh database always failed — exactly what a reviewer would hit.
- The query vector had no `::vector` cast, so search failed outright and the
  HNSW index could not be used.
- pypdf returns hard-wrapped lines with no blank lines between paragraphs, so
  the `paragraph` strategy produced byte-identical output to `fixed` — two of the
  three required strategies were the same one. Fixed with paragraph reflow.

**Part 3**

- Langflow hardcodes `tool_name="Call_Agent"` for every Agent exposed as a tool,
  so two specialist agents on one orchestrator collide: it asked for Analysis and
  reached Response, which has no database access.
- Langflow's built-in SQL component returns a pandas DataFrame rendered as a
  string, and pandas elides middle columns as `...`. `email`, `category` and
  `priority` never reached the agent, which then invented them — a real Billing
  ticket was reported as "Technical Support" with a null email. Replaced with a
  custom read-only component returning JSON.
- Langflow flattens nested agent-as-tool output into the parent's text, so
  chaining Analysis → Response duplicated every answer. Questions now use
  Analysis alone, which also activates fewer components.
- Gemini's free tier allows 20 requests per day *per model per project*.

**Security**

The credential scanner itself had two defects worth mentioning, because both are
the kind that make a security control worse than useless:

- It used `mapfile`, absent from the bash 3.2 that ships with macOS. It errored
  and printed a green *"No files to scan"* — reporting success it had not
  verified. It now fails closed.
- Its Gemini pattern only matched legacy `AIza…` keys and would have passed the
  newer `AQ.` AI Studio format straight through.

---

## Credentials

No secrets are committed. See [`SECURITY.md`](./SECURITY.md) for the full
approach — `.env` for Part 2, Langflow Global Variables for Part 3, and a
fail-closed pre-commit scanner across both.

Enable the hook once per clone:

```bash
git config core.hooksPath .githooks
```
