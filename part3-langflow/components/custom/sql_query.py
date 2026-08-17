"""Custom Langflow tool: read-only SQL over the support database, returning JSON.

Why this replaces Langflow's built-in SQL Database component:

The built-in tool executes the query correctly but returns a *pandas DataFrame
rendered as a string*. Pandas elides middle columns when the frame is wide:

       id customer_name  ...       status                 created_at
    0   2   Sarah Cohen  ...  In Progress 2026-08-17 12:16:52.870069

`email`, `category` and `priority` are behind that `...`. The agent never sees
them -- and an LLM asked to report fields it cannot see will invent plausible
ones. Observed in practice: Sarah Cohen's real record is Billing with a real
email address, and the agent reported "Technical Support" with a null email.

That is a data-integrity failure, not a formatting nit, and no prompt can fix it
because the information is gone before the model is reached. This component
returns every column as JSON, so what the agent sees is exactly what the
database returned.

Safety: the tool refuses anything that is not a single SELECT. That is defence in
depth -- the connection should also use a read-only role (see README) -- but it
means a prompt-injected DROP fails here as well as at the grant.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from langflow.custom import Component
from langflow.io import IntInput, MessageTextInput, Output, SecretStrInput
from langflow.schema import Data

# One leading SELECT (or WITH ... SELECT), and no statement separator that would
# allow a second statement to be smuggled in.
_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\b",
    re.IGNORECASE,
)

DEFAULT_MAX_ROWS = 50


class SupportSQLComponent(Component):
    display_name = "SQL Database (read-only, JSON)"
    description = (
        "Run a read-only SQL SELECT against the support database and return the "
        "rows as JSON. Use for any question about support requests, tickets, "
        "customers, statuses, priorities, categories or counts."
    )
    icon = "database"
    name = "SupportSQL"

    inputs = [
        MessageTextInput(
            name="query",
            display_name="SQL query",
            info=(
                "A single PostgreSQL SELECT statement. Table: support_requests"
                "(id, customer_name, email, category, priority, status, created_at). "
                "Use ILIKE for name/email matching. Always add LIMIT."
            ),
            tool_mode=True,
            required=True,
        ),
        MessageTextInput(
            name="database_url",
            display_name="Database URL",
            info="PostgreSQL connection string. Bind to a Global Variable.",
            required=True,
        ),
        SecretStrInput(
            name="database_password",
            display_name="Database password (optional)",
            info="Only if the URL does not already carry credentials.",
            required=False,
        ),
        IntInput(
            name="max_rows",
            display_name="Max rows",
            value=DEFAULT_MAX_ROWS,
            info="Hard cap on rows returned, applied after the query runs.",
        ),
    ]

    outputs = [Output(display_name="Rows (JSON)", name="rows", method="run_query")]

    def run_query(self) -> Data:
        """Execute the query and return every column as JSON.

        Never raises: the Analysis Agent is prompted to report tool failures, so
        errors come back as structured data it can read and relay.
        """
        query = (self.query or "").strip().rstrip(";").strip()
        url = (self.database_url or "").strip()
        limit = int(self.max_rows or DEFAULT_MAX_ROWS)

        if not query:
            return self._error("No SQL query was provided.")
        if not url:
            return self._error(
                "No database URL is configured on the SQL tool. Set the "
                "SUPPORT_DATABASE_URL global variable."
            )

        # --- read-only enforcement ------------------------------------------
        if not _SELECT_ONLY.match(query):
            return self._error(
                "Only SELECT queries are permitted. This tool is read-only."
            )
        if ";" in query:
            return self._error(
                "Multiple statements are not permitted. Send a single SELECT."
            )
        forbidden = _FORBIDDEN.search(query)
        if forbidden:
            return self._error(
                f"The keyword '{forbidden.group(0).upper()}' is not permitted. "
                "This tool is read-only."
            )

        # --- execute ---------------------------------------------------------
        try:
            from sqlalchemy import create_engine, text
        except ImportError:  # pragma: no cover
            return self._error("SQLAlchemy is not installed in this environment.")

        try:
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            with engine.connect() as connection:
                result = connection.execute(text(query))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchmany(limit)]
        except Exception as exc:  # noqa: BLE001 - many driver error types
            return self._error(f"SQL execution failed: {self._safe(exc)}")

        payload = {
            "row_count": len(rows),
            "columns": columns,
            "rows": json.loads(json.dumps(rows, default=self._encode)),
        }
        self.status = f"{len(rows)} row(s)"
        return Data(data=payload)

    def _error(self, message: str) -> Data:
        self.status = f"Error: {message}"
        return Data(data={"error": message, "row_count": 0, "rows": []})

    @staticmethod
    def _encode(value):
        """JSON-encode types psycopg returns that json does not handle."""
        if isinstance(value, Decimal):
            return float(value)
        return str(value)

    @staticmethod
    def _safe(exc: Exception) -> str:
        """Render an error without leaking the connection string."""
        message = str(exc).strip() or exc.__class__.__name__
        if "://" in message:
            return f"{exc.__class__.__name__} (details withheld: may contain credentials)"
        return message[:300]
