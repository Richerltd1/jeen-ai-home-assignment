# Credential handling

This repository is public and contains three deliverables that each touch
secrets in a different way. Each surface gets its own mechanism.

| Surface | Secret | Where it lives | How it stays out of git |
| ------- | ------ | -------------- | ----------------------- |
| Part 2 — Python module | `GEMINI_API_KEY`, `POSTGRES_URL` | `.env` (untracked) | `.gitignore` + pre-commit scan |
| Part 3 — Langflow flow | `GEMINI_API_KEY`, Gmail app password | Langflow **Global Variables** (type: Credential) | Flow JSON stores the variable *name*, never the value |
| Part 1 — Presentation | `GEMINI_API_KEY` | Google AI Studio session | Screenshot hygiene (see below) |

---

## The rule that drives all of this

**A key that reaches git history is compromised, permanently.** Deleting it in a
later commit does not remove it — it remains in the history, in every clone, and
in every fork. GitHub's public-repo firehose is scraped continuously by bots;
published keys are typically exercised within minutes.

So the only reliable control is one that runs *before* the commit exists, and the
only remedy after a leak is **rotation**, not deletion.

---

## Part 2 — the Python module

1. Secrets are read exclusively through `docdex/config.py`, which loads `.env`.
   Nothing else in the codebase touches `os.environ` for credentials.
2. `.env` is gitignored. `.env.example` is committed and contains placeholders only.
3. `Settings` is a frozen dataclass with no custom `__repr__`, so it is never
   accidentally rendered into a log line.
4. Errors are redacted. psycopg embeds the full connection string — password
   included — in some exception messages, so `store._safe_error()` detects a DSN
   in an error and replaces it with the exception class name:

   ```python
   if "://" in message:
       return f"{exc.__class__.__name__} (details withheld: may contain credentials)"
   ```

5. No secret is ever printed on success either. The indexer reports the model
   name and dimension count, never the key.

---

## Part 3 — the Langflow flow

This is the highest-risk surface, because **the exported flow JSON is a submitted
deliverable**. Langflow will embed a literal key into that export if the key was
typed directly into a component field.

The correct mechanism is **Global Variables** of type `Credential`:

1. In Langflow: **Settings → Global Variables → Add New**
2. Name: `GEMINI_API_KEY`, Type: **Credential**, Value: the key
3. In the component, bind the field to the variable rather than typing the value.

Langflow stores Credential-type globals encrypted in its own database and writes
only the *reference* into the flow JSON. The same applies to the Gmail app
password (`GMAIL_APP_PASSWORD`).

**Verify, do not assume.** After exporting, the flow JSON is scanned like any
other file:

```bash
./scripts/check-secrets.sh
```

A flow JSON containing `AIza…` fails the scan and must not be submitted.

---

## Part 1 — the presentation

The AI Studio chat interface does not display the API key, but the **"Get code"**
panel does, and so does the browser URL in some sharing flows. When capturing the
two required conversation screenshots:

- Screenshot the **chat panel only**, not the full browser window.
- Do not open or capture "Get code".
- Check the final images before uploading them to the deck.

---

## Automated enforcement

`scripts/check-secrets.sh` scans for Google/Gemini keys, OpenAI keys, Postgres
URLs containing a password, generic `api_key = "..."` assignments, private key
blocks, Google app-password formatting, and any tracked `.env` file.

Enable the pre-commit hook once per clone:

```bash
git config core.hooksPath .githooks
```

Design notes:

- **Fails closed.** Any unexpected error blocks the commit. An earlier version
  used `mapfile`, which does not exist in the bash 3.2 that ships with macOS; it
  errored and printed a green "No files to scan", reporting success it had not
  verified. That failure mode is the reason for the `ERR` trap.
- **Redacts its own output.** The scanner prints `file:line`, never the matched
  value, so the tool itself cannot leak the secret into a terminal log or CI output.
- **Allowlist is explicit** — `.env.example`, `docker-compose.yml` (throwaway
  localhost password), and the security files themselves, which necessarily
  contain the patterns.

Run it over the whole tree at any time:

```bash
./scripts/check-secrets.sh
```

---

## If a key is ever exposed

1. **Rotate immediately** — Gemini: https://aistudio.google.com/apikey (delete
   the key, create a new one). Gmail app password: revoke at
   https://myaccount.google.com/apppasswords
2. Update `.env` and the Langflow Global Variable with the new value.
3. Only then worry about scrubbing history. Rotation is what actually closes the
   exposure; history rewriting is cleanup.
