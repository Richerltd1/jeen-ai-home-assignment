# Part 3 — Multi-Agent Support Flow (Langflow)

A three-agent customer-support workflow in Langflow 1.11.3. An **Orchestrator**
classifies intent and routes dynamically; an **Analysis Agent** reads the support
database through a SQL Tool; a **Response Agent** writes the customer-facing reply
and sends email through a custom Gmail Tool.

The point of the design is *selective activation*: a greeting invokes no agent and
no tool, a data question invokes SQL, an email request invokes Gmail.

---

## Architecture

```
  SQL Database (Tool) ─────▶ Analysis Agent ────┐
   [read-only, JSON]         gemini-3.1-flash-lite │ exposed as tool:
                                                │  query_support_database
                                                ▼
  Chat Input ───────────────────────────▶ Orchestrator Agent ───▶ Chat Output
                                                ▲   gemini-3.6-flash
                                                │  exposed as tool:
  Gmail Sender (Tool) ─────▶ Response Agent ────┘  compose_customer_response
   [custom component]        gemini-3.1-flash-lite-preview
```

The two specialist agents are attached to the Orchestrator **as tools**, not as
fixed pipeline stages. Nothing forces them to run. The Orchestrator's model
decides per message, which is what makes the routing genuinely dynamic rather
than a hardcoded chain.

### Agent responsibilities

| Agent | Owns | Tools | Never does |
| ----- | ---- | ----- | ---------- |
| **Orchestrator** | Intent classification, routing, context carry-forward | none directly | Query SQL or send email itself |
| **Analysis** | All database access, classification, urgency derivation | SQL Database | Talk to the user |
| **Response** | Customer-facing wording, sending email | Gmail Sender | Invent facts it wasn't given |

### Routing rules

| Intent | Example | Analysis | Response | SQL | Gmail |
| ------ | ------- | :------: | :------: | :-: | :---: |
| `SMALL_TALK` | "Hi there" | — | — | — | — |
| `GENERAL_KNOWLEDGE` | "What does SLA mean?" | — | — | — | — |
| `DATA_LOOKUP` | "Which requests are open?" | ✅ | — | ✅ | — |
| `ACTION_REQUEST` | "Email John about his ticket" | ✅ | ✅ | ✅ | ✅ |
| `ACTION_REQUEST` (no lookup) | "Send a test mail to x@y.com" | — | ✅ | — | ✅ |
| `UNCLEAR` | "Send the email" | — | — | — | — |

---

## Setup

### 1. Database

```bash
createdb support
psql -d support -f schema.sql
```

`schema.sql` contains the table and seed rows exactly as specified in the brief.

Then create the least-privilege role the SQL Tool actually connects as:

```sql
CREATE ROLE support_readonly LOGIN;
GRANT CONNECT ON DATABASE support TO support_readonly;
GRANT USAGE ON SCHEMA public TO support_readonly;
GRANT SELECT ON support_requests TO support_readonly;
```

This matters — see [Security](#security).

### 2. Langflow

```bash
python -m venv .venv-langflow
.venv-langflow/bin/pip install langflow psycopg2-binary

LANGFLOW_COMPONENTS_PATH=./components \
LANGFLOW_SSRF_ALLOWED_HOSTS=127.0.0.1,localhost \
  langflow run --host 127.0.0.1 --port 7860
```

Both environment variables are required:

- `LANGFLOW_COMPONENTS_PATH` loads the custom Gmail Sender component.
- `LANGFLOW_SSRF_ALLOWED_HOSTS` — Langflow blocks outbound connections to
  loopback addresses as SSRF protection, which otherwise blocks the SQL Tool from
  reaching a local PostgreSQL.

`psycopg2-binary` is **not** a Langflow dependency. Without it SQLAlchemy cannot
open a `postgresql://` URL and the SQL Tool fails with `ModuleNotFoundError`.

### 3. Global Variables

**Settings → Global Variables**, or via the API:

| Name | Type | Value |
| ---- | ---- | ----- |
| `GEMINI_API_KEY` | Credential | your AI Studio key |
| `GMAIL_APP_PASSWORD` | Credential | 16-char Google App Password |
| `SUPPORT_DATABASE_URL` | **Generic** | `postgresql://support_readonly@127.0.0.1:5433/support` |
| `GMAIL_ADDRESS` | **Generic** | the sending address |

The last two must be **Generic**, not Credential. Langflow refuses to bind a
Credential-typed variable to a plain-text field, specifically so its value cannot
surface in component output, logs, or traces.

### 4. Import the flow

Import `support-multi-agent-flow.json` through the UI, or rebuild it from source:

```bash
python build_flow.py
```

`build_flow.py` constructs the graph programmatically. With 10 nodes, 9 edges and
three long system prompts, building in code keeps the flow reviewable in a diff
and reproducible on a fresh instance.

---

## Running it

### Playground

Open the flow and use the Playground panel.

### HTTP POST

```bash
curl -X POST "http://127.0.0.1:7860/api/v1/run/support-flow?stream=false" \
  -H "x-api-key: $LANGFLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "input_value": "Which support requests are still open?",
        "output_type": "chat",
        "input_type": "chat",
        "session_id": "demo-1"
      }'
```

Since Langflow 1.5 the run endpoint requires a real API key even when
`LANGFLOW_AUTO_LOGIN=true`. Create one under **Settings → API Keys**.

---

## Error handling

| Case | Behaviour |
| ---- | --------- |
| Record not found | Analysis returns `found:false`; Response says no record exists and asks for a specific detail. Never invents one. |
| SQL tool error | Analysis reports the failure and the query it attempted; does not retry the identical query. |
| Empty result set | Treated as a valid answer, not an error. |
| Unclear request | Orchestrator asks exactly one clarifying question; never guesses a recipient or ticket id. |
| Missing recipient | Gmail tool refuses to send and says the address is missing. A customer *name* is rejected as an address. |
| Email send failure | Custom component returns `success:false` with the reason; Response Agent states the email was **not** sent. Never reports a success it did not achieve. |
| Model rate limit | Surfaced verbatim to the user rather than silently degrading into a fabricated answer. |

The anti-fabrication rules are load-bearing. During development a tool-name
collision caused the Orchestrator to reach the Response Agent — which has no
database access — when it asked for Analysis. Because the prompts forbid
inventing records, the system said it had no data instead of confidently
returning fictional tickets. That is the correct failure mode.

---

## Security

No secret appears in `support-multi-agent-flow.json`. Every credential field is
bound to a Global Variable by name (`load_from_db: true`), and the export is
verified against the real values, not merely pattern-matched:

```
literal GEMINI_API_KEY present in export: False
literal GMAIL_ADDRESS  present in export: False
pattern AIza / AQ. / sk- / postgres-URL-with-password found: False
```

**Least privilege over concealment.** The SQL component exposes `database_url`
as a plain-text field, so it *cannot* hold a Credential-typed variable. Rather than pretend the URL is hidden, the tool connects as
`support_readonly`, which can `SELECT` and nothing else:

```
SELECT  → 3 rows
UPDATE  → ERROR: permission denied for table support_requests
DROP    → ERROR: must be owner of table support_requests
```

The Analysis Agent's prompt also forbids writes, but a prompt is a request, not a
control. The grant is the control.

**Credentials are not agent-controllable.** In the Gmail component only
`to_email`, `subject` and `body` are `tool_mode` inputs. `sender_email` and
`app_password` are operator-set, so no prompt injection can change the sending
identity.

---

## Implementation notes

Four issues found by running the flow rather than trusting it:

**0. The built-in SQL tool silently truncated its own results.** Langflow's
`SQLComponent` returns a pandas DataFrame rendered as a string, and pandas elides
middle columns when the frame is wide:

```
   id customer_name  ...       status                 created_at
0   2   Sarah Cohen  ...  In Progress 2026-08-17 12:16:52.870069
```

`email`, `category` and `priority` are behind that `...`. The agent never receives
them, and a model asked to report fields it cannot see invents plausible ones —
Sarah Cohen's real Billing ticket was reported as "Technical Support" with a null
email. No prompt can fix this; the data is gone before the model is reached.
Replaced with a custom read-only component (`components/custom/sql_query.py`) that
returns every column as JSON and refuses anything that is not a single SELECT.

**1. Agent-as-tool name collision.** Langflow hardcodes `tool_name="Call_Agent"`
(`lfx/components/models_and_agents/agent.py:1019`), so *every* Agent exposed as a
tool is named `Call_Agent_message_response` regardless of display name. With two
specialist agents on one Orchestrator the names collide and routing silently hits
the wrong agent. Fixed with per-agent `tools_metadata`; the redundant
`json_response` variant is disabled so each agent exposes exactly one tool.

**2. Inter-agent contract is JSON, not prose.** Analysis originally returned a
markdown report, which looked like a finished answer — so the Orchestrator pasted
it to the user verbatim, internal field names and all. It now returns
single-line JSON, which is obviously not a customer reply, and the Response Agent
owns all user-facing wording. Structured data between agents, prose to the user.

**3. Stale model list.** The bundled dropdown still offers `gemini-2.5-flash`,
which now returns 404 for new API keys. Model names are set explicitly.

**4. Per-model free-tier quota.** Gemini's free tier allows 20 requests per day
*per model per project* (`GenerateRequestsPerDayPerProjectPerModel`), and one
message costs several once tool loops are counted. Each agent therefore runs on a
different model, which triples usable headroom. Pro-tier models
(`gemini-pro-latest`, `gemini-3.1-pro-preview`) report a limit of **0/day** — they
have no free tier at all. For any sustained use, billing must be enabled.
