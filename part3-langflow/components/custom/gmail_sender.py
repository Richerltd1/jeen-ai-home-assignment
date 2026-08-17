"""Custom Langflow tool: send an email through Gmail SMTP.

Langflow ships no built-in Gmail *send* component -- `GmailLoader` only reads,
and the Composio Gmail component requires a third-party Composio account. The
assignment permits custom tools, so this implements sending directly.

Design points:

- The three arguments an agent controls (`to_email`, `subject`, `body`) are
  declared with `tool_mode=True`, so the Agent sees them as a function signature
  and fills them itself.
- Credentials are NOT agent-controllable. `sender_email` and `app_password` are
  plain component inputs bound to Langflow Global Variables, so the model can
  never be talked into changing the sending identity.
- Nothing raises. Every failure is returned as structured `Data` with
  `success=False` and a human-readable reason, because the Response Agent is
  prompted to report send failures to the user rather than crash the flow.
- The app password is never logged, never echoed, and never included in an
  error message.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage

from langflow.custom import Component
from langflow.io import MessageTextInput, Output, SecretStrInput
from langflow.schema import Data

SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465
SMTP_TIMEOUT_SECONDS = 20

# Deliberately permissive: full RFC 5322 validation is not the point here, we
# only want to catch an agent passing a customer *name* where an address belongs.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class GmailSenderComponent(Component):
    display_name = "Gmail Sender"
    description = "Send an email via Gmail SMTP. Use when the user asks to email someone."
    documentation = "https://support.google.com/accounts/answer/185833"
    icon = "mail"
    name = "GmailSender"

    inputs = [
        # --- agent-controlled arguments -------------------------------------
        MessageTextInput(
            name="to_email",
            display_name="To",
            info="Recipient email address. Must be a real address, never a person's name.",
            tool_mode=True,
            required=True,
        ),
        MessageTextInput(
            name="subject",
            display_name="Subject",
            info="Specific subject line, e.g. 'Update on your Login Issue (ticket #1)'.",
            tool_mode=True,
            required=True,
        ),
        MessageTextInput(
            name="body",
            display_name="Body",
            info="Plain-text email body. State status, next step, and owner. Under 120 words.",
            tool_mode=True,
            required=True,
        ),
        # --- operator-controlled credentials (never agent-controlled) -------
        MessageTextInput(
            name="sender_email",
            display_name="Sender Gmail address",
            info="The Gmail account that sends. Bind to a Global Variable.",
            required=True,
            advanced=False,
        ),
        SecretStrInput(
            name="app_password",
            display_name="Gmail App Password",
            info=(
                "16-character Google App Password (not the account password). "
                "Bind to a Credential-type Global Variable."
            ),
            required=True,
        ),
    ]

    outputs = [Output(display_name="Result", name="result", method="send_email")]

    def send_email(self) -> Data:
        """Send the email and return a structured result.

        Returns:
            Data with `success` (bool), `message` (str), and on success the
            `recipient` and `subject` actually used, so the agent can confirm
            them back to the user and a mistake is immediately visible.
        """
        to_email = (self.to_email or "").strip()
        subject = (self.subject or "").strip()
        body = (self.body or "").strip()
        sender = (self.sender_email or "").strip()
        password = (self.app_password or "").strip()

        # --- validation: fail before opening a connection -------------------
        if not to_email:
            return self._failure("No recipient address was provided. The email was not sent.")
        if not EMAIL_PATTERN.match(to_email):
            return self._failure(
                f"'{to_email}' is not a valid email address. The email was not sent. "
                "A customer name is not an address -- look up the address first."
            )
        if not subject:
            return self._failure("No subject was provided. The email was not sent.")
        if not body:
            return self._failure("No body was provided. The email was not sent.")
        if not sender or not password:
            return self._failure(
                "Gmail credentials are not configured on this component. "
                "Set the sender address and app password Global Variables."
            )

        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        # --- send -----------------------------------------------------------
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                SMTP_HOST, SMTP_SSL_PORT, context=context, timeout=SMTP_TIMEOUT_SECONDS
            ) as server:
                server.login(sender, password)
                server.send_message(message)

        except smtplib.SMTPAuthenticationError:
            # Never include the password or the raw server reply, which echoes it.
            return self._failure(
                "Gmail rejected the credentials. The email was NOT sent. "
                "Check that the App Password is current and that 2-Step "
                "Verification is enabled on the sending account."
            )
        except smtplib.SMTPRecipientsRefused:
            return self._failure(
                f"Gmail refused the recipient '{to_email}'. The email was NOT sent."
            )
        except smtplib.SMTPSenderRefused:
            return self._failure(
                f"Gmail refused the sender address '{sender}'. The email was NOT sent."
            )
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            return self._failure(
                f"Could not send the email ({type(exc).__name__}: {exc}). "
                "The email was NOT sent."
            )

        self.status = f"Sent to {to_email}"
        return Data(
            data={
                "success": True,
                "message": f"Email sent successfully to {to_email}.",
                "recipient": to_email,
                "subject": subject,
            }
        )

    def _failure(self, reason: str) -> Data:
        """Return a structured failure. Never raises, never leaks credentials."""
        self.status = f"Failed: {reason}"
        return Data(data={"success": False, "message": reason})
