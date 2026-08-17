# How it works, and why

A step-by-step walkthrough of both systems, with the reasoning behind each
decision. Written for someone reviewing the code who wants to know not just what
it does but why it does it that way — including the places where the obvious
approach turned out to be wrong.

- [How this was built — what I did and why](#how-this-was-built--what-i-did-and-why)
- [Part 2 — tracing a document through the indexer](#part-2--tracing-a-document-through-the-indexer)
- [Part 2 — tracing a query through search](#part-2--tracing-a-query-through-search)
- [Part 3 — tracing a message through the agents](#part-3--tracing-a-message-through-the-agents)
- [Credential handling](#credential-handling)
- [Known limitations](#known-limitations)

---

# How this was built — what I did and why

## Build order

Part 2 → Part 3 → Part 1, deliberately, and not in the order the brief lists them.

**Part 3 is the long pole**, so its slowest dependency — a 2 GB Langflow install —
was started in the background before anything else and installed while Part 2 was
being written. **Part 2 is the most self-contained**, so it could be finished
while that ran. **Part 1 went last** because a presentation is the most
reworkable artifact: if time ran short, a slightly thinner deck is a far smaller
loss than a broken flow.

The presentation also depended on facts I did not yet have. Writing the model
comparison first would have meant writing it from memory.

## Verify by running, not by reading

The governing decision was that nothing counts as done until it has been executed
and the output inspected. That is slower up front and it is the reason every
significant bug in this project was found before submission rather than by a
reviewer.

The bugs that only surfaced this way:

| Found by | Bug |
| --- | --- |
| Printing chunk contents and reading them | Every overlapped chunk began mid-word |
| Counting paragraphs after extraction | `paragraph` and `fixed` produced identical output |
| Running against an empty database | First run always crashed — `register_vector()` before `CREATE EXTENSION` |
| Running a search at all | Missing `::vector` cast; search failed outright |
| Comparing agent output against the database row by row | The SQL tool silently truncated its own results |
| Reading the Langflow trace | Two agents shared one tool name; routing hit the wrong agent |
| Running the scanner on a clean tree | It failed *open* and printed a green pass |

Every one of these reads as "works fine" from the source code.

## Infrastructure decisions

**Not using the company Supabase.** Twelve Supabase projects were available, all
belonging to Richer LTD. Putting a personal job application on company
infrastructure would also have meant a company connection string in a public
repo. Went local instead.

**No Docker, no Homebrew, no local Postgres on the machine.** Rather than block
on a GUI installer, `pgserver` was used — a pip package bundling real PostgreSQL
binaries plus pgvector. Its default unix-socket path exceeded the 103-byte limit
under the scratch directory, so the server runs TCP-only on `127.0.0.1:5433`. The
repo still ships a `docker-compose.yml` as the documented path, because that is
what a reviewer will actually use, and it is the same PostgreSQL and the same
pgvector.

**Switching pip to `uv`.** `pip install langflow` wedged in dependency-resolver
backtracking — six and a half minutes of pure CPU with zero bytes downloaded.
Killed it and used `uv`, which resolved the same tree in seconds.

**Building the flow in code rather than on the canvas.** `build_flow.py`
constructs all 10 nodes and 9 edges programmatically. With three long system
prompts, this keeps the graph reviewable in a diff, reproducible on a fresh
Langflow instance, and impossible to break by dragging a wire to the neighbouring
port. It also made the several rebuilds during debugging a one-command operation
rather than a manual re-wiring each time.

## Debugging method

When the agents returned wrong customer data, the tempting conclusion was "the
cheap model is hallucinating." That was wrong, and acting on it would have meant
upgrading models and shipping a system that still corrupted data.

The trace showed the SQL was correct and the tool's *output* was truncated. The
fix belonged one layer below where the symptom appeared. The general rule applied
throughout: when an LLM produces wrong output, check what actually reached it
before concluding anything about the model.

Similarly, when Snyk kept flagging a path traversal after two genuine fixes, the
answer was not to argue with the scanner or suppress the finding — it was to
notice that the arbitrary-path capability had never been used and delete it. The
finding disappeared because the surface disappeared.

## Security work

The brief asks for keys kept out of code and out of the flow. That was treated as
the floor rather than the goal, because the repository is public.

Beyond `.env` and global variables: a fail-closed pre-commit scanner, DSN
redaction in error paths, a least-privilege database role, and a Snyk scan before
push. The scanner itself was tested in **both** directions — that it passes a
clean tree *and* that it still catches a planted key — because a control that has
only been tested one way is half-tested.

The scanner's own two defects (failing open on macOS bash; missing the current
Gemini key format) are documented rather than quietly fixed, since "the security
tool reported success it never performed" is the most transferable lesson here.

## What was deliberately not done

**No OCR.** Out of scope; scanned PDFs are detected and rejected clearly.

**No token-based chunk sizing.** A tokenizer dependency for a marginal gain, and
character counts behave more predictably across Hebrew and English.

**No test suite.** The brief does not ask for one and the time was better spent
on verified end-to-end runs. Given more time this is the first thing to add — the
chunking invariants in particular (no chunk over budget, no chunk starting
mid-word) are exactly what property tests are good at.

**The Gmail send is left unwired.** A deliberate choice: the tool is fully built
and connected, and without an app password it returns a clean handled failure —
which demonstrates the brief's required "email sending failure" handling. A real
send would be stronger, and is one credential away.

---

# Part 2 — tracing a document through the indexer

Command:

```bash
python index_documents.py --file ./docs/example.pdf --strategy paragraph
```

## Step 1 — Configuration loads before anything else

`config.load_settings()` reads `.env` and validates that `GEMINI_API_KEY` and
`POSTGRES_URL` are both present and non-blank, raising `ConfigurationError` if
not.

**Why first?** Because failing on a missing key *after* spending thirty seconds
parsing a PDF and calling an embedding API is a worse experience than failing
immediately. Validation is cheap; the work after it is not.

**Why a frozen dataclass with no custom `__repr__`?** A settings object that
prints nicely is a settings object that ends up in a log line. Leaving `__repr__`
undefined means the default shows the class, and nobody is tempted to `print()`
it for debugging.

**Why is `os.getenv` confined to this one file?** So that "where do secrets enter
the program" has exactly one answer. A reviewer can audit credential handling by
reading sixty lines.

## Step 2 — Extraction dispatches on file type

`extract.extract_text(path)` checks three things in order: the file exists, its
extension is supported, and — after parsing — that it produced text.

```
missing file      -> FileNotFoundErrorDocdex
.txt, .rtf, ...   -> UnsupportedFileTypeError
scanned PDF       -> NoTextExtractedError  (suggests OCR, which is out of scope)
```

**Why three separate exception types rather than one?** Each one maps to a
different user action. "The path is wrong" and "this format isn't supported" and
"this PDF is a photograph" need different responses, and a single generic error
forces the user to guess which situation they are in.

### PDF reading

`_read_pdf` iterates pages and wraps each `extract_text()` call in a try/except
that skips failures:

```python
for page in reader.pages:
    try:
        pages.append(page.extract_text() or "")
    except Exception:
        continue
```

**Why swallow per-page errors?** Real PDFs contain individual corrupt pages. A
single bad page on a 200-page document should cost you that page, not the whole
run. The alternative — aborting — means a document that is 99% readable indexes
0% of itself.

Pages are then joined with `"\n\n"` so that a page boundary still reads as a
paragraph boundary downstream.

### DOCX reading

`_read_docx` reads paragraphs *and* walks tables, joining cells with `" | "`.

**Why bother with tables?** Because in policy and support documents the tables
often hold the actual answers — SLA times, price tiers, escalation thresholds.
`python-docx` does not include table text in `document.paragraphs`, so a naive
implementation silently drops the most queryable content in the file.

## Step 3 — Cleaning normalises whitespace without destroying structure

`clean_text` does four things, in this order:

1. Normalise line endings (`\r\n`, `\r` → `\n`).
2. **De-hyphenate words split across lines**: `inter-\nnational` → `international`.
3. Strip control characters that survive some PDF encoders.
4. Collapse runs of spaces, then normalise blank-line runs to exactly `\n\n`.

**Why de-hyphenation matters:** `inter-` and `national` are two meaningless
tokens. Left alone they degrade the embedding of every chunk containing a
line-broken word, which in a justified PDF is a large fraction of them.

**Why paragraph breaks survive but everything else collapses:** the paragraph
chunking strategy depends on `\n\n` being an unambiguous separator. Normalising
to *exactly* two newlines means the splitter needs one rule, not a regex that
guesses.

## Step 4 — Paragraph reflow (PDF only)

This step exists because of a bug found by testing, not by design.

A PDF stores each visual **line**, not each paragraph. Extracted text therefore
has a newline roughly every 90 characters and no blank lines at all. The first
version of this code ran `_split_paragraphs` on that and got **2 paragraphs** for
the whole document — so the `paragraph` strategy saw two enormous blocks, handed
them to the hard-splitter, and produced **byte-identical output to `fixed`**.

Two of the three required strategies were secretly the same strategy.

`reflow_paragraphs` reconstructs the structure using how wrapped text looks:

> Every line in a paragraph is filled to roughly the column width **except the
> last one**, which is ragged.

So the algorithm computes the median length of substantial lines, and treats any
line shorter than 75% of that as ending its paragraph. Numbered or short
unpunctuated lines are treated as standalone headings.

On the sample document this recovers **16 paragraphs from 2**.

**Why a heuristic rather than a layout library?** Libraries that recover true
layout (pdfplumber, PyMuPDF with block extraction) are heavier dependencies and
still guess. The ragged-line rule is three lines of code, has an obvious failure
mode (documents that are entirely short lines), and is easy to reason about.

**Why is this PDF-only?** DOCX already knows its own paragraphs. Reflowing a DOCX
would corrupt structure that was already correct.

## Step 5 — Chunking

Three strategies, one interface. `chunk_text(text, strategy, chunk_size, overlap)`.

### `fixed` — sliding window with overlap

The window slides by `chunk_size - overlap`. Two details matter:

**Both ends align to word boundaries.** The first version aligned only the *end*
of the window, retreating to the last space. But the *start* is set by the
overlap step, which lands at an arbitrary offset — so every overlapped chunk
began with a word fragment:

```
"...n out fully and sign back in..."     <- was
"...out fully and sign back in..."       <- now
```

A leading fragment is a garbage token at the most heavily weighted position in
the chunk. This was invisible until chunk contents were printed and read.

**Forward progress is guaranteed.** After boundary alignment the next start could
theoretically land at or behind the current one, producing an infinite loop. The
code takes `next_start if next_start > start else end`. There is also a guard
rejecting `overlap >= chunk_size` at the API boundary, since that configuration
means the window never advances.

### `sentence` and `paragraph` — split, then *pack*

Both split into units and then greedily pack consecutive units up to the size
budget, rather than emitting one chunk per unit.

**Why pack?** A four-word sentence makes a near-useless embedding. There is not
enough context in "Escalate to the identity team." to match against a real user
query, and it will either never be retrieved or be retrieved for the wrong
reasons. Packing keeps chunks semantically whole while still worth embedding.

**Why hard-split oversized units?** A single 4,000-character paragraph would blow
the budget. Any unit longer than `chunk_size` is passed to the fixed-size
splitter, which guarantees the invariant *no chunk ever exceeds `chunk_size`*
regardless of strategy.

### Sentence splitting and abbreviations

The boundary regex uses negative lookbehinds for `Mr`, `Mrs`, `Dr`, `Ms`, `St`,
`e.g`, `i.e`:

```python
_ABBREVIATIONS = r"(?<!\bMr)(?<!\bMrs)(?<!\bDr)..."
```

**Why not a full NLP sentence tokenizer?** It would mean a heavyweight dependency
(nltk/spacy plus model downloads) for a marginal gain on documents like these.
The abbreviation list covers the cases that actually appear in business prose. If
this were processing medical or legal text the trade-off would flip.

### Why character counts, not tokens

Chunk sizes are in characters. Token counting requires a tokenizer, adds a
dependency, and — importantly — is wildly inconsistent across languages. Hebrew
tokenises very differently from English, and this project is Israeli-market
adjacent. Characters are approximate but stable and need nothing installed.

## Step 6 — Embedding

`EmbeddingClient.embed_documents(chunks)` calls `gemini-embedding-001`.

### Why 1536 dimensions and not the 3072 default

**pgvector's HNSW and IVFFlat indexes support at most 2000 dimensions.** At 3072
the embedding column cannot be indexed at all, and every search degrades to a
sequential scan over the entire table. That is fine for 23 chunks and useless at
100,000.

`gemini-embedding-001` is trained with Matryoshka representation learning, so
truncated vectors remain meaningful. 1536 buys a usable index for a very small
quality cost. This is the single most consequential decision in Part 2 and it is
invisible unless you know the index limit.

### Why embeddings are re-normalised

Gemini returns unit-length vectors **only at its native 3072 dimensions**. Chop a
unit vector down to 1536 components and it is no longer unit length. Cosine
distance on non-normalised vectors is still computable but no longer means what
you think it means, and results drift subtly.

```python
magnitude = math.sqrt(sum(v * v for v in vector))
return [v / magnitude for v in vector]
```

Three lines. Skipping them produces a system that works and is quietly wrong —
the worst category of bug.

### Why documents and queries are embedded differently

- Chunks: `task_type="RETRIEVAL_DOCUMENT"`
- Queries: `task_type="RETRIEVAL_QUERY"`

Gemini projects the two into a shared space optimised for matching *questions*
against *passages*. A question and its answer are not textually similar — "why
can't I log in" and "expired session tokens cause sign-in failures" share almost
no vocabulary. Symmetric embedding treats them as unrelated; asymmetric embedding
is trained for exactly this mismatch.

### Batching and retry

Batches of 100 inputs, three attempts with linear backoff (2s, 4s).

**Why retry at all?** Embedding APIs return transient 429s and 503s. Losing a
2,000-chunk indexing run to one blip is unacceptable. **Why only three?** Because
a persistent failure — bad key, revoked project — should surface in seconds, not
after a long exponential ramp. The error message names the batch offset so a
partial failure is diagnosable.

The response is validated: if Gemini returns a different number of embeddings
than inputs sent, that is an error, not something to silently zip together.

## Step 7 — Storage

### Connecting

`store.connect()` opens the connection, runs `CREATE EXTENSION IF NOT EXISTS
vector`, commits, and only *then* registers the pgvector type adapter.

**Why that order?** The adapter looks up the `vector` type OID in the database.
On a fresh database that type does not exist yet, so `register_vector()` raises
`vector type not found in the database` — and the very first run always fails.
Which is exactly what a reviewer cloning the repo would hit. Creating the
extension inside `connect()` rather than in `ensure_schema()` is what makes the
first run work.

### Schema

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
CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);
```

**Why `chunk_index` beyond the required columns?** It preserves document order.
Without it a retrieved chunk is a floating fragment; with it you can locate it in
its source, show neighbouring context, or reconstruct the document. It also makes
results deterministic to inspect.

**Why HNSW and not IVFFlat?** HNSW needs no training step and performs well
without tuning on small-to-medium datasets. IVFFlat requires the table to be
populated before building a useful index — awkward when the schema is created
automatically on first run.

**Why cosine and not L2?** Gemini's embedding space is trained for cosine
similarity, and the vectors are unit-normalised, which makes cosine the natural
metric.

**Why `TIMESTAMPTZ` rather than the `TIMESTAMP` the brief lists?** Timezone-naive
timestamps are a latent bug in anything that ever crosses a machine boundary. The
column still satisfies "created_at"; it just carries the offset.

### Idempotent writes

```python
DELETE FROM document_chunks WHERE filename = %s AND split_strategy = %s;
INSERT ... (one per chunk)
```

Both in a single transaction.

**Why delete-then-insert rather than upsert?** Re-indexing a document may produce
a *different number* of chunks — the file changed, or the chunk size did. Upsert
by index would leave stale rows beyond the new count. Replace-all is the correct
semantic.

**Why one transaction?** A crash mid-run leaves the previous index intact rather
than a half-written one. Partial indexes are worse than stale indexes because
they look complete.

**Why the `(filename, strategy)` pair as the key?** So the same document can be
indexed under all three strategies simultaneously and compared, which is exactly
what the assignment asks you to demonstrate.

---

# Part 2 — tracing a query through search

```bash
python search.py --query "login issue"
```

1. Settings load and validate.
2. The query is embedded with `RETRIEVAL_QUERY`.
3. The SQL runs:

```sql
SELECT id, chunk_text, filename, split_strategy, chunk_index, created_at,
       1 - (embedding <=> %s::vector) AS similarity
FROM document_chunks
WHERE (%s::text IS NULL OR filename = %s)
  AND (%s::text IS NULL OR split_strategy = %s)
ORDER BY embedding <=> %s::vector
LIMIT %s;
```

**Why the `::vector` casts?** A bare placeholder carries no column context, so
PostgreSQL infers `double precision[]`, finds no `<=>` operator for it, and the
query fails outright with `operator does not exist: vector <=> double
precision[]`. The cast also lets the planner use the HNSW index rather than
falling back to a sequential scan.

**Why `1 - distance`?** pgvector's `<=>` returns cosine *distance* where lower is
better. Humans reading CLI output expect higher-is-better similarity. The
conversion happens in SQL so every consumer sees the same convention.

**Why the `(%s::text IS NULL OR col = %s)` pattern?** It expresses optional
filters in one prepared statement rather than building SQL strings conditionally.
No string concatenation means no injection surface and one query plan to reason
about.

**Why order by raw distance rather than the computed similarity?** So the
expression matches the index's operator exactly. Ordering by `1 - (…)` would be a
different expression and could defeat index usage.

### Empty results are not an error

`search.py` exits **0** when nothing matches, and distinguishes two cases:

- *the index is empty* → tells you to index something, with the command
- *filters excluded everything* → tells you how many chunks exist and suggests
  broadening

**Why does this distinction matter?** They are different problems with different
fixes. "No results" alone sends the user hunting for a bug in the wrong place.

---

# Part 3 — tracing a message through the agents

## The graph

```
  SQL Database (Tool) ─────▶ Analysis Agent ────┐
   [read-only, JSON]                            │  as tool: query_support_database
                                                ▼
  Chat Input ───────────────────────────▶ Orchestrator Agent ───▶ Chat Output
                                                ▲
  Gmail Sender (Tool) ─────▶ Response Agent ────┘  as tool: compose_customer_response
   [custom component]
```

**The central design decision: the two specialists are attached to the
Orchestrator as *tools*, not as pipeline stages.**

A pipeline would run Analysis → Response for every message, which fails the
assignment's core requirement — *"not every Agent has to run for every message."*
As tools, nothing forces them to execute; the Orchestrator's model decides per
message whether to call neither, one, or both. That is what makes the routing
genuinely dynamic rather than a chain with `if` statements in front of it.

## Step 1 — A message arrives

Via the Playground, or via HTTP:

```bash
POST /api/v1/run/support-flow?stream=false
{"input_value": "...", "output_type": "chat", "input_type": "chat", "session_id": "..."}
```

`session_id` is what gives the Orchestrator conversational memory, which its
prompt relies on ("if the user said John Smith three messages ago and now says
'send him an email', the subject is still John Smith").

## Step 2 — The Orchestrator classifies intent

Its prompt defines five intents and, for each, the exact action:

| Intent | Example | What runs |
| --- | --- | --- |
| `SMALL_TALK` | "Hi there" | Nothing. Direct reply. |
| `GENERAL_KNOWLEDGE` | "What does SLA mean?" | Nothing. Direct reply. |
| `DATA_LOOKUP` | "Which requests are open?" | Analysis + SQL |
| `ACTION_REQUEST` | "Email John about his ticket" | Analysis + SQL, then Response + Gmail |
| `UNCLEAR` | "Send the email" | Nothing. One clarifying question. |

**Why enumerate intents rather than describe the role?** Because "route
intelligently to the right agent" produces a model that calls everything to be
safe. Helpful over-activation is the dominant failure mode in agent systems, and
the assignment grades against it explicitly. Naming five buckets and stating the
action for each converts a judgement call into a lookup.

**Why does the prompt state what the agent must NOT do so heavily?** Same reason.
"Never call the SQL tool yourself", "never guess a recipient", "never fabricate a
ticket id" are the rules that actually get tested, because the model's instinct
is to be useful.

## Step 3 — Data lookup routes to Analysis only

For `DATA_LOOKUP` the Orchestrator calls `query_support_database` **once** and
returns its answer.

**Why doesn't the Response Agent run here?** Two reasons, one principled and one
practical.

*Principled:* nothing needs composing and nothing needs sending. A question is
not an action. Activating the Response Agent for a plain question is exactly the
over-activation the brief penalises.

*Practical:* Langflow flattens nested agent-as-tool output into the parent's
text. When both Analysis and Response ran, the final message contained both of
their outputs concatenated — the same answer printed twice. Removing Response
from the question path removed the duplication and improved brief compliance at
the same time.

### The tool-name collision

Langflow hardcodes `tool_name="Call_Agent"` in
`lfx/components/models_and_agents/agent.py`. **Every** Agent exposed as a tool is
therefore named `Call_Agent_message_response`, regardless of its display name.

With two specialist agents on one Orchestrator, the names collide. Observed
behaviour: the Orchestrator asked for the Analysis Agent and reached the Response
Agent — which has no database access — and so answered with nothing.

The fix is a per-agent `tools_metadata` entry keyed on the default tag, renaming
them to `query_support_database` and `compose_customer_response`, each with a
description stating when to use it and when not to. The redundant
`json_response` variant of each agent is disabled (`status: False`) so every
agent exposes exactly one unambiguous tool rather than two near-identical ones.

**Why the tool *descriptions* are written carefully:** they are the only thing the
Orchestrator's model sees when choosing. `compose_customer_response`'s
description ends "Do NOT use for plain questions that need no email" — a
negative instruction placed where the decision is actually made, rather than
buried in a system prompt the model may weigh less at tool-selection time.

## Step 4 — Analysis queries the database

The Analysis Agent holds the SQL tool and is the only component with database
access.

### Why the built-in SQL component was replaced

This is the most important bug in the project.

Langflow's `SQLComponent` executes the query correctly, then returns a **pandas
DataFrame rendered as a string**. Pandas elides middle columns when the frame is
wide:

```
   id customer_name  ...       status                 created_at
0   2   Sarah Cohen  ...  In Progress 2026-08-17 12:16:52.870069
```

`email`, `category` and `priority` are behind that `...`. The agent never
receives them. And a language model asked to report fields it cannot see will
produce plausible ones: Sarah Cohen's real **Billing** ticket was reported as
**"Technical Support"** with a **null** email. Verified against the database.

No prompt can fix this. The information is destroyed before the model is reached.
It would corrupt answers on any model, and it presents as "the LLM hallucinated"
— which sends you debugging the wrong layer entirely.

`components/custom/sql_query.py` replaces it:

- Returns `{"row_count", "columns", "rows"}` as JSON with **every** column.
- Rejects anything that is not a single leading `SELECT`/`WITH`.
- Rejects `;` so a second statement cannot be smuggled in.
- Rejects `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO`.
- Caps rows after execution.
- Never raises — failures come back as `{"error": ...}` because the Analysis
  Agent is prompted to report tool errors, and an exception would abort the flow
  instead.
- Redacts connection strings from error text.

### Why the database role is read-only

The SQL keyword filter is defence in depth, not the defence. The actual control is
a PostgreSQL grant:

```sql
CREATE ROLE support_readonly LOGIN;
GRANT CONNECT ON DATABASE support TO support_readonly;
GRANT USAGE ON SCHEMA public TO support_readonly;
GRANT SELECT ON support_requests TO support_readonly;
```

Verified:

```
SELECT  → 5 rows
UPDATE  → ERROR: permission denied for table support_requests
DROP    → ERROR: must be owner of table support_requests
```

**Why this matters more than the prompt:** the Analysis Agent's prompt also
forbids writes. But a prompt is a *request* to a language model, and language
models can be argued out of requests. A grant cannot be argued with. If a prompt
injection ever talks the agent into `DROP TABLE`, the database refuses.

This also resolves a genuine constraint: Langflow only permits Credential-typed
global variables on *secret* fields, and `database_url` is a plain-text field. So
the URL cannot be hidden. Rather than pretend otherwise, the URL points at an
account that can only read.

### What Analysis returns

A short factual briefing in plain sentences — not JSON.

This changed twice, and the reasoning is worth recording:

1. **First: markdown report.** It looked like a finished answer, so the
   Orchestrator pasted it to the user verbatim, internal field names and all.
2. **Then: single-line JSON.** Obviously not a customer reply — but Langflow
   surfaced it into the final output anyway, so users saw raw JSON.
3. **Now: customer-ready prose.** Since the output *will* surface regardless of
   intent, the correct move is to make surfacing harmless. The briefing is
   written so that if it reaches the user, it still reads as a sensible answer.

The general lesson: when a framework leaks intermediate output, hardening the
prompt against leaking is fighting the framework. Making the leaked thing
presentable is cheaper and more robust.

Urgency is **derived, not copied**: Login Issue and Account Access become High
because they block product usage, regardless of the stored priority column. The
prompt states this rule explicitly, because "determine urgency" without a rubric
produces a model that echoes the `priority` field and adds nothing.

## Step 5 — Response composes and sends

The Response Agent runs only for `ACTION_REQUEST`.

### The SEND_EMAIL contract

The Orchestrator passes a literal line, `SEND_EMAIL: yes` or `SEND_EMAIL: no`,
and the Response Agent may only call the Gmail tool if it sees `yes`.

**Why an explicit token rather than "use judgement"?** Because judgement failed.
Observed behaviour before this contract: a user asked *"what is the status of
Sarah Cohen's ticket?"* and the system emailed her. The agent reasoned that
sending an update was helpful. It was not asked for.

A machine-checkable token converts a fuzzy inference into a binary condition. The
prompt reinforces it: *"If it says `SEND_EMAIL: no`, or says nothing at all, you
MUST NOT call the Gmail tool — not even to be helpful."*

### Why the Gmail tool is custom

Langflow ships no Gmail *send* component. `GmailLoader` only reads; the Composio
component requires a third-party Composio account and OAuth handshake. The brief
permits custom tools, so `components/custom/gmail_sender.py` sends over SMTP.

Two design points:

**Only three inputs are agent-controllable.** `to_email`, `subject` and `body`
carry `tool_mode=True`. `sender_email` and `app_password` are operator-set,
bound to global variables. The model can therefore choose *what* to send and to
whom, but can never be talked into changing the sending identity. Prompt
injection cannot make the system send as someone else.

**It never raises.** Every failure returns `Data(data={"success": False,
"message": ...})`. The Response Agent is prompted to report send failures to the
user; an exception would abort the flow instead, turning a handled condition into
a crash.

Validation runs before any connection is opened — a missing recipient, or a
customer *name* where an address belongs, is rejected without a network round
trip. `SMTPAuthenticationError` is caught specifically and its message is
rewritten, because the raw server reply can echo the password.

## Step 6 — Output

The Orchestrator returns the answer to `ChatOutput`. Its prompt forbids restating
what a specialist already produced, because Langflow concatenates nested agent
text and a restatement prints the whole answer twice.

## Verified routing behaviour

Run live against the brief's own four examples:

| Message | SQL | Gmail | Tools called |
| --- | :-: | :-: | --- |
| "Hello there" | no | no | none |
| "How many support requests are open?" | **yes** | no | `query_support_database`, `run_query` |
| "What is a service level agreement?" | no | no | none |
| "Send an email to john@example.com…" | no | **yes** | `compose_customer_response`, `send_email` |

---

# Credential handling

Three surfaces, three mechanisms, because they fail in different ways.

**Part 2 — `.env`.** Gitignored, read only through `config.py`. Additionally,
psycopg embeds the full DSN — password included — in some exception messages, so
`store._safe_error()` detects `://` in an error and replaces the message with the
exception class name. Secrets escape through error paths far more often than
through source code.

**Part 3 — Langflow Global Variables.** A field is set to the *name* of a
variable plus `load_from_db: true`; Langflow resolves it at runtime. The exported
flow JSON therefore carries references, never values. This is the highest-risk
surface because the JSON is a submitted deliverable — type a key into a component
field and it ships.

Verification is done against the **real values**, not just patterns:

```
literal GEMINI_API_KEY in export : False
literal GMAIL_ADDRESS  in export : False
pattern AIza / AQ. / sk- / postgres-URL-with-password : False
```

**Part 1 — screenshot hygiene.** AI Studio's chat panel does not show the key,
but the "Get code" panel does.

## The pre-commit scanner

`scripts/check-secrets.sh` blocks commits containing credentials. Two defects
found while building it are worth recording, because both made it worse than
nothing:

**It failed open.** An early version used `mapfile`, which does not exist in the
bash 3.2 that ships with macOS. It errored, then printed a green *"No files to
scan"* and exited 0 — reporting success it had never performed. It now installs
an `ERR` trap and fails closed: any unexpected error blocks the commit.

**Its Gemini pattern was incomplete.** It matched only legacy `AIza…` keys.
Current AI Studio keys are `AQ.`-prefixed and ~53 characters, and would have
passed straight through. Both formats are now covered.

It also **redacts its own output** — printing `file:line`, never the matched
value — so the scanner cannot become the thing that leaks the secret into a
terminal log or CI transcript.

And it filters documentation placeholders, because a check that cries wolf gets
bypassed with `--no-verify`, after which it protects nothing.

---

# Known limitations

Stated plainly rather than discovered by a reviewer.

**Duplicated output on the lookup-and-email path.** Langflow flattens nested
agent-as-tool output into the parent's text, so on `ACTION_REQUEST` the Response
Agent's reply and the Orchestrator's copy are concatenated. The content is
correct; it is repeated. Three prompt-level fixes reduced but did not eliminate
it. Fixing it properly means not nesting agents as tools, which would cost the
dynamic routing the assignment asks for.

**No OCR.** Scanned PDFs are detected and rejected with a clear message rather
than silently indexing nothing.

**Sentence splitting is regex-based.** Fine for business prose; would need a real
tokenizer for medical or legal text.

**Gemini free tier is unusable for a multi-agent demo.** 20 requests per day *per
model per project*. One conversation costs several. Billing is required for any
sustained use — pro-tier models report a limit of 0/day.

**The Langflow instance can drift from the exported JSON.** Observed once: the
server dropped the three model edges, leaving the flow unrunnable while the
exported file remained correct. Re-run `python build_flow.py` and verify the
deployed edge count before demoing.

**`chunk_index` ordering assumes stable extraction.** Re-indexing after a library
upgrade may renumber chunks. Since writes are replace-all per
`(filename, strategy)`, this is consistent within a document but not comparable
across runs.
