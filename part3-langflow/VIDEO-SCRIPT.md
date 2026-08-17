# Part 3 — Video recording script (2–5 minutes)

Everything the brief asks the video to show, in the order to show it. The seven
messages are chosen so that between them they exercise every routing path while
spending the fewest possible model calls.

**Record with:** ⌘⇧5 on macOS (screen recording). Capture the browser window only.

**Before you hit record**

- [ ] Postgres running, `support_requests` seeded (5 rows)
- [ ] Langflow running with `LANGFLOW_COMPONENTS_PATH` and `LANGFLOW_SSRF_ALLOWED_HOSTS`
- [ ] Billing enabled on the Gemini key (done — verified, quota lifted)
- [ ] Flow open, canvas zoomed so all 10 nodes are visible
- [ ] A terminal window ready with the `curl` command pasted but not run
- [ ] Close any tab showing an API key

---

## 0:00–0:35 — The flow and the three agents

Show the canvas. Trace the graph with the cursor while saying:

> This is a customer-support workflow with three agents. Chat input goes to the
> **Orchestrator**, which classifies what the user wants and decides who should
> handle it. The **Analysis Agent** is the only one with database access — it
> holds the SQL tool. The **Response Agent** writes the customer-facing reply and
> holds the Gmail tool.
>
> The important detail is *how* the two specialists are connected. They're
> attached to the Orchestrator as **tools**, not as fixed pipeline stages. Nothing
> forces them to run. The Orchestrator decides per message — so a greeting costs
> nothing, and a data question activates only what it needs.

Point out the read-only database role:

> The SQL tool connects as `support_readonly`, which can SELECT and nothing else.
> The prompt also forbids writes, but a prompt is a request — the grant is the
> control.

---

## 0:35–2:15 — Conversation in the Playground

Open Playground. Send these **in order**. Between each, say one line about what
routing you expect *before* the answer appears — that demonstrates the decision
logic rather than just the output.

**1.** `Hi there`
> Small talk. No agent, no tool. Watch — nothing lights up.

**2.** `What does SLA stand for?`
> General knowledge. The Orchestrator answers from its own knowledge. Still no
> database call — there's nothing here worth querying.

**3.** `Which support requests are still open?`
> Now it needs data. This routes to the Analysis Agent, which runs SQL, then to
> the Response Agent to word the answer.

*Expected: three open tickets — John Smith (Login Issue, High), Emma Johnson
(Account Access, High), Michael Brown (Subscription, Medium).*

**4.** `What is the status of Sarah Cohen's ticket?`
> A targeted lookup. Same route, narrower query.

*Expected: Billing, Medium, In Progress.*

**5.** `Send the email`
> Deliberately ambiguous — no recipient, no subject. A weak system would guess an
> address. This one asks a single clarifying question and calls nothing.

**6.** `Do we have any tickets from alice@example.com?`
> This customer does not exist. The Analysis Agent returns found = false, and the
> Response Agent says so. It does not invent a plausible ticket — that's the rule
> that matters most in a support system.

**7.** `Email John Smith about his login issue`
> Now the full chain: look up John, then compose and send. This is where the
> Gmail tool activates.

*Known cosmetic issue on this path only: the answer prints twice. Langflow
flattens nested agent-as-tool output into the parent's text, so the Response
Agent's reply and the Orchestrator's copy of it are concatenated. The content is
correct; it is repeated. If you would rather avoid it on camera, use this instead
— it skips the database lookup, so only one agent produces text:*

> `Send an email to john@example.com saying his ticket is being reviewed`

*Note: with the app password unset, this returns a clean handled failure —* "the
email was NOT sent" *with the reason. Call that out deliberately:*

> And here's the error path. The send failed, and notice it says explicitly that
> the email was **not** sent, with the reason. It doesn't report a success it
> didn't achieve — that's the failure mode that actually costs you customer trust.

---

## 2:15–3:30 — Langflow Trace walkthrough

Open the trace for **message 3** (the SQL one).

Show, in this order:

1. **The Orchestrator's tool call** — name `query_support_database`. Say:
   > It chose this tool by name. The Analysis and Response agents have distinct
   > tool names and descriptions, so routing is a deliberate choice, not a guess.

2. **Tool Input** — expand it. Show the question passed through.

3. **The Analysis Agent's SQL Tool call** — expand **Tool Input** and show the
   generated SQL:
   ```sql
   SELECT id, customer_name, email, category, priority, status
   FROM support_requests WHERE status ILIKE 'Open' LIMIT 50;
   ```

4. **Tool Output** — the three returned rows.

5. **The Response Agent call** — show that it receives the Analysis Agent's JSON
   and returns prose. Say:
   > Structured JSON between agents, plain language to the user. The JSON never
   > reaches the customer.

Then open the trace for **message 1** (the greeting):

> And for comparison — the greeting. No tool calls at all. That contrast is the
> whole design.

---

## 3:30–4:30 — HTTP POST run

Switch to the terminal. Run:

```bash
curl -X POST "http://127.0.0.1:7860/api/v1/run/support-flow?stream=false" \
  -H "x-api-key: $LANGFLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "input_value": "Which support requests are still open?",
        "output_type": "chat",
        "input_type": "chat",
        "session_id": "http-demo"
      }'
```

> The same flow over HTTP, same routing, same result — so this isn't a UI demo,
> it's an API-callable service.

Make sure the key is in an environment variable, **not** pasted literally on
screen.

---

## 4:30–5:00 — Close on security

Show the exported JSON, search for `AIza` / `AQ.` → no matches.

> No credentials in the exported flow. Every secret is a Global Variable
> reference resolved at runtime — the JSON carries the name, never the value.
> And the database user can only read.

---

## Verified rehearsal results

All seven messages were run end-to-end after billing was enabled. Observed:

| # | Message | Tools called | Correct? |
| - | ------- | ------------ | -------- |
| 1 | Hi there | **none** | ✅ |
| 2 | What does SLA stand for? | **none** | ✅ |
| 3 | Which support requests are still open? | SQL | ✅ all three records exact |
| 4 | Status of Sarah Cohen's ticket? | SQL | ✅ Billing / Medium / In Progress |
| 5 | Send the email | **none** | ✅ one clarifying question |
| 6 | Tickets from alice@example.com? | SQL | ✅ says not found, invents nothing |
| 7 | Email John Smith about his login issue | SQL + Gmail | ✅ reports send failure honestly |

## If you hit a 429 mid-recording

Billing is enabled, so this should not happen. If it does, stop and check
https://aistudio.google.com/billing rather than talking over it.
