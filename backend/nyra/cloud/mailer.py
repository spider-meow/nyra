"""Sending an e-mail through Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

`send` is what the summaries and alerts call: it takes one of the organization's e-mails of the day first
(`usage.claim_email`), gives it back when nothing left, and never succeeds silently without a key.
`deliver` is the network part alone. The API key and full addresses never reach a log or an exception message."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
import uuid
from typing import Callable, Optional, Sequence

import httpx

from . import db as cloud_db
from . import usage as cloud_usage
from .routes.members import EmailLimit

log = logging.getLogger("nyra.mailer")

API_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0
ATTEMPTS = 3                  # the first try and at most 2 retries (network error, 5xx, 429)
RETRY_WAIT_SECONDS = 1.0      # waits 1 s then 2 s
MAX_RECIPIENTS = 10
MAX_SUBJECT = 200
MAX_BODY_BYTES = 200_000
MAX_ERROR_CHARS = 200
MAX_CODE_CHARS = 64
MAX_ANSWER_BYTES = 64_000
DEADLINE_SECONDS = 30.0       # the whole of `deliver`, tries and waits included

_ADDRESS = re.compile(r"[^\s<>@,;]+@[^\s<>@,;]+")
_SENDER = re.compile(r"[^<>@\r\n]+<[^\s<>@,;]+@[^\s<>@,;]+>|[^\s<>@,;]+@[^\s<>@,;]+")


class MailNotConfigured(RuntimeError):
    """RESEND_API_KEY or NYRA_MAIL_FROM is missing: nothing can be sent."""


class MailError(RuntimeError):
    """The mail did not leave (network, Resend's refusal). The message is safe to show: no key, cut short.
    `code` is Resend's error name (`invalid_idempotent_request`...), cut short, or "" when there is none (network)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def parse_sender(value: str) -> str:
    """`Name <address>` or `address`; ValueError (naming NYRA_MAIL_FROM) otherwise."""
    value = value.strip()
    if not _SENDER.fullmatch(value) or len(value) > 254:
        raise ValueError(f"NYRA_MAIL_FROM is not valid: write it as 'Nyra <nyra@your-domain.com>' or 'nyra@your-domain.com' (got {value[:60]!r}).")
    return value


def sender_from_env(value: str, has_key: bool) -> str:
    """NYRA_MAIL_FROM at start. A mistyped sender stops the start (RuntimeError); a key without a sender only warns
    (the mail stays unconfigured, so a deployment that sets the key first still starts); a sender alone is fine."""
    value = value.strip()
    if not value:
        if has_key:
            log.warning("RESEND_API_KEY is set but NYRA_MAIL_FROM is not: e-mail sending stays off until both are set.")
        return ""
    try:
        return parse_sender(value)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def mask(address: str) -> str:
    """`alice@client.fr` -> `a***@client.fr`, for the logs."""
    name, _, domain = address.partition("@")
    return f"{name[:1]}***@{domain}"


def _recipients(to: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for raw in to:
        address = raw.strip().lower()
        if not _ADDRESS.fullmatch(address) or len(address) > 254:
            raise ValueError(f"Invalid recipient address: {raw[:60]!r}.")
        seen[address] = None
    if not 1 <= len(seen) <= MAX_RECIPIENTS:
        raise ValueError(f"A mail needs 1 to {MAX_RECIPIENTS} different recipients (got {len(seen)}).")
    return list(seen)


def _check_content(subject: str, html: str, text: str) -> None:
    if not subject.strip() or len(subject) > MAX_SUBJECT or "\n" in subject or "\r" in subject:
        raise ValueError(f"The subject must be one line of 1 to {MAX_SUBJECT} characters.")
    for name, body in (("html", html), ("text", text)):
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(f"The {name} body is over {MAX_BODY_BYTES // 1000} KB.")


def _json(body: bytes) -> dict:
    try:
        parsed = json.loads(body)
    except ValueError:  # not JSON: the caller falls back on the raw text
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _refusal(body: bytes, api_key: str) -> str:
    message = str(_json(body).get("message") or body.decode("utf-8", "replace"))
    return message.replace(api_key, "***")[:MAX_ERROR_CHARS]


def _check_idempotency_key(key: str) -> None:
    if not 1 <= len(key) <= 256 or not (key.isascii() and key.isprintable()):
        raise ValueError("The idempotency key must be 1 to 256 printable ASCII characters.")


def _retryable(status: int, body: bytes) -> bool:
    """Network-side trouble worth another try; a 409 is only retried when Resend says the same request is still running."""
    if status == 409:
        return _json(body).get("name") == "concurrent_idempotent_requests"
    return status == 429 or status >= 500


def _post_once(client: httpx.Client, api_key: str, payload: dict, idempotency_key: str, deadline: float) -> tuple[Optional[tuple[int, bytes]], str]:
    """((status, body), "") or (None, why) when the network failed or the answer was too slow to arrive."""
    headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": idempotency_key}
    timeout = max(0.1, min(TIMEOUT_SECONDS, deadline - time.monotonic()))
    try:
        with client.stream("POST", API_URL, json=payload, headers=headers, timeout=timeout) as response:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    return None, "deadline exceeded while reading the answer"
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_ANSWER_BYTES:
                    break
            return (response.status_code, b"".join(chunks)), ""
    except httpx.HTTPError as exc:  # the type only: a message could repeat the request
        return None, type(exc).__name__


def deliver(api_key: str, sender: str, to: Sequence[str], subject: str, html: str, text: str, *, kind: str, idempotency_key: Optional[str] = None,
            deadline_seconds: float = DEADLINE_SECONDS, transport: Optional[httpx.BaseTransport] = None,
            sleep: Callable[[float], None] = time.sleep) -> str:
    """Post the mail to Resend; returns its id. ValueError on bad input (nothing is sent), MailError on failure.

    `kind` names the e-mail in `email_registry.py` (it only reaches the logs; a test refuses a call whose kind is not registered).

    Retries (network error, 5xx, 429, 409 "still running") reuse the same Idempotency-Key. Callers that may call again
    after a MailError (a timeout where Resend had accepted the mail) pass their own stable `idempotency_key`
    (the monthly summary: `monthly:<org_id>:<YYYY-MM>`): Resend then never sends it twice for 24 hours.

    The whole call stops at `deadline_seconds`: checked between tries and while the answer's body arrives.
    ponytail: a server dripping the answer's *headers* one byte at a time is not cut by the deadline: httpx has no
    total timeout and each byte resets the per-read one. Measured with a byte every 0.4 s: 89 s, until httpx's own
    header size limit gives up. Resend does not do this; a hard bound would need a watchdog thread."""
    recipients = _recipients(to)
    _check_content(subject, html, text)
    idempotency_key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
    _check_idempotency_key(idempotency_key)
    payload = {"from": sender, "to": recipients, "subject": subject, "html": html, "text": text}
    shown, why, deadline = ", ".join(mask(a) for a in recipients), "", time.monotonic() + deadline_seconds
    with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
        for attempt in range(ATTEMPTS):
            answer, why = _post_once(client, api_key, payload, idempotency_key, deadline)
            if answer is not None:
                status, body = answer
                if status < 300:
                    log.info("mail %s sent to %s", kind, shown)
                    return str(_json(body).get("id", ""))
                why = f"Resend answered {status}"
                if not _retryable(status, body):
                    raise MailError(f"{why}: {_refusal(body, api_key)}", code=str(_json(body).get("name") or "")[:MAX_CODE_CHARS])
            log.warning("mail %s to %s not sent (try %d of %d): %s", kind, shown, attempt + 1, ATTEMPTS, why)
            wait = RETRY_WAIT_SECONDS * (attempt + 1)
            if attempt + 1 == ATTEMPTS or time.monotonic() + wait >= deadline:
                break
            sleep(wait)
    raise MailError(f"The mail could not be sent after {attempt + 1} tries ({why}).")


def send(settings, org_id: uuid.UUID, to: Sequence[str], subject: str, html: str, text: str, *, kind: str, idempotency_key: Optional[str] = None,
         transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep) -> str:
    """Send one mail for an organization; returns Resend's id (`deliver` explains `kind` and `idempotency_key`).

    MailNotConfigured without a key or sender, ValueError on bad input, EmailLimit when the organization's
    day is used up (nothing is sent: keep the message and try again), MailError when it did not leave.
    Whatever stops the send before it succeeds (even SystemExit or Ctrl-C), the place of the day is given back."""
    if not (settings.resend_api_key and settings.mail_from):
        raise MailNotConfigured("E-mail sending is not configured: set both RESEND_API_KEY and NYRA_MAIL_FROM.")
    recipients = _recipients(to)
    _check_content(subject, html, text)
    if idempotency_key is not None:
        _check_idempotency_key(idempotency_key)
    with cloud_db.connect(settings.database_url) as conn:
        limit = cloud_usage.quota_for(conn, settings.base_config().quota, org_id).emails_per_day
        if not cloud_usage.claim_email(conn, org_id, limit):
            raise EmailLimit()
    sent = False
    try:
        result = deliver(settings.resend_api_key, settings.mail_from, recipients, subject, html, text,
                         kind=kind, idempotency_key=idempotency_key, transport=transport, sleep=sleep)
        sent = True
        return result
    finally:
        if not sent:
            with cloud_db.connect(settings.database_url) as conn:
                cloud_usage.release_email(conn, org_id)


def render(title: str, paragraphs: Sequence[str]) -> tuple[str, str]:
    """(html, text) of a plain mail. Every value is escaped in the HTML; the text is left as written."""
    esc = html_lib.escape
    body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    html = f"<!doctype html><html><body><h1>{esc(title)}</h1>{body}</body></html>"
    return html, "\n\n".join([title, *paragraphs])
