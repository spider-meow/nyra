"""The back office's "E-mails" page: every e-mail Nyra sends (`email_registry.py`), its preview, and a test mail to yourself.

Platform admins only. A test goes to the address of the signed-in account and nowhere else (the body takes nothing), only
for the e-mails Nyra itself sends, through `mailer.deliver`: it belongs to no organization, so it uses no client's daily
allowance. At most `TEST_LIMIT` tests an hour per account, counted from the audit rows (`email.test`: the e-mail's key and
`started` / `sent` / `failed`, never the address or the content). The Resend key is never in an answer or a log.

*The limit holds for simultaneous requests:* under a lock held for the account (`pg_advisory_xact_lock`), in one short transaction, the attempt is counted
and its `started` row written; the lock goes with that transaction, BEFORE the call to Resend, so no transaction is open during the network call. The
result (`sent` / `failed`) is a second row written after. Only `started` rows are counted: an attempt counts even if the network or the code fails after it.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import audit as cloud_audit
from .. import auth as cloud_auth
from .. import db as cloud_db
from .. import email_registry, mailer
from .common import Ctx, get_ctx, staff_dep

router = APIRouter()

TEST_LIMIT = 10  # tests per account and per hour
BANNER = "E-mail de test : données fictives, envoyé depuis le back office."
_BANNER_HTML = f'<p style="margin:0 0 16px;padding:8px 12px;background:#f1f1ec;color:#555;font:13px Arial,sans-serif">{BANNER}</p>'
_RECENT = """SELECT created_at + interval '1 hour' - now() AS wait FROM audit_log
             WHERE user_id = %s AND action = 'email.test' AND detail->>'result' = 'started' AND created_at > now() - interval '1 hour'
             ORDER BY created_at DESC LIMIT %s"""


class NoBody(BaseModel):
    """The test takes no field: the address is the account's, never one typed in."""
    model_config = ConfigDict(extra="forbid")


def _entry(key: str) -> email_registry.Entry:
    entry = email_registry.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="E-mail inconnu.")
    return entry


def _sample(entry: email_registry.Entry) -> tuple[str, str, str]:
    try:
        return entry.sample()
    except OSError as exc:  # a template file the server does not have
        raise HTTPException(status_code=503, detail="Le gabarit de cet e-mail est introuvable sur le serveur.") from exc


@router.get("/api/staff/emails")
def list_emails(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    s = ctx.settings
    return {"configured": bool(s.resend_api_key and s.mail_from), "sender": s.mail_from,
            "emails": [{"key": e.key, "label": e.label, "description": e.description, "sender": e.sender,
                        "can_send_test": e.sender == "nyra"} for e in email_registry.ENTRIES]}


@router.get("/api/staff/emails/{key}/preview")
def preview_email(key: str, claims=Depends(staff_dep)) -> dict:
    subject, html, text = _sample(_entry(key))
    return {"subject": subject, "html": html, "text": text}


def _marked(subject: str, html: str, text: str) -> tuple[str, str, str]:
    """The sample as a test mail: "[TEST]" in the subject and the banner above the body, in the HTML and in the text."""
    marked, found = re.subn(r"<body[^>]*>", lambda tag: tag.group(0) + _BANNER_HTML, html, count=1)
    return f"[TEST] {subject}", marked if found else _BANNER_HTML + html, f"{BANNER}\n\n{text}"


def _reserve(database_url: str, claims: cloud_auth.Claims, entry: email_registry.Entry) -> None:
    """Count the account's attempts of the last hour and write this one's `started` row, as one step under the account's lock
    (two requests at the same instant cannot both see room for the last place). 429 when the ten are used."""
    with cloud_db.connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(claims.user_id),))
        waits = [row["wait"].total_seconds() for row in conn.execute(_RECENT, (claims.user_id, TEST_LIMIT)).fetchall()]
        if len(waits) < TEST_LIMIT:
            cloud_audit.record(conn, None, claims.user_id, "email.test", "email", entry.key, kind=entry.key, result="started")
            return
    seconds = max(1, math.ceil(waits[-1]))  # when the oldest of the last ten leaves the hour
    raise HTTPException(status_code=429, headers={"Retry-After": str(seconds)},
                        detail=f"Limite atteinte : {TEST_LIMIT} e-mails de test par heure et par compte. Réessayez dans {math.ceil(seconds / 60)} min.")


def _failure(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):  # the account's address is not one a mail can go to
        return HTTPException(status_code=400, detail="L'adresse de votre compte n'est pas valable pour un envoi.")
    return HTTPException(status_code=502, detail=f"L'envoi a échoué : {str(exc)[:200]}")  # MailError: Resend's message, cut, with no key


@router.post("/api/staff/emails/{key}/test")
def send_test(key: str, body: Optional[NoBody] = None, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Send the sample to the signed-in account. The attempt is counted first (`_reserve`); the network call is made with no
    transaction open; its result is traced after."""
    entry, s = _entry(key), ctx.settings
    if entry.sender != "nyra":
        raise HTTPException(status_code=400, detail="Cet e-mail est envoyé par Supabase : aperçu seulement.")
    email = (claims.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Votre compte n'a pas d'adresse e-mail : le test ne peut pas vous être envoyé.")
    missing = [name for name, value in (("RESEND_API_KEY", s.resend_api_key), ("NYRA_MAIL_FROM", s.mail_from)) if not value]
    if missing:
        raise HTTPException(status_code=503, detail=f"L'envoi d'e-mails n'est pas configuré : ajoutez {' et '.join(missing)} au serveur.")
    subject, html, text = _marked(*_sample(entry))
    _reserve(s.database_url, claims, entry)
    failure: Optional[HTTPException] = None
    try:
        mailer.deliver(s.resend_api_key, s.mail_from, [email], subject, html, text, kind=entry.key)
    except (mailer.MailError, ValueError) as exc:
        failure = _failure(exc)
    with cloud_db.connect(s.database_url) as conn:
        cloud_audit.record(conn, None, claims.user_id, "email.test", "email", entry.key, kind=entry.key, result="failed" if failure else "sent")
    if failure:
        raise failure
    return {"sent": True, "to": mailer.mask(email)}
