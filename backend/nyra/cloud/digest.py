"""The monthly e-mail: ONE per organization and per month, to its administrators who are allowed and want it (AXE-523).

*Who receives it* (`receives_monthly`, the one rule): a member whose role is `admin`, AND whom the Nyra team allows
(`memberships.monthly_email_allowed`), AND who wants it (`monthly_email_wanted`). Both switches are on by default. When nobody
receives it the month is marked `skipped` with the reason `no_recipient` (`no_admin`: the organization has no administrator).

*What it says.* `gather` reads, per brand: the **new visuals to treat** (a match found since the last e-mail on a visual whose
rights have expired, with no decision yet), the visuals that **expire within 30 days** and those that expire in **31 to 90
days**, and whether the brand's last read looks incomplete (`crawl_runs.suspect`), was stopped by the month's page limit, or
is not finished. `build` turns that into the subject, the HTML and the text; it reads nothing and is tested on made-up data.
Unlimited rights are left out everywhere. Each list shows 20 lines at most and says how many more there are.

*When a match is "new".* `matches.created_at` cannot tell: a pass that rewrites a match (a re-indexed reference, a new
threshold) sets it again. A match cannot exist before both of its images do, so it is dated by the later of the site
image's `first_seen` and the reference's `created_at`, which nothing rewrites.

*When it leaves* (`due_orgs`): the organization has a brand with the monthly read on, the date of its latest brand has
passed, and no scheduled read of the organization waits or runs, or 48 hours have passed since that date (the expiry alert
does not depend on the read: a read that failed is told in the mail). It leaves only if there is something to say; else
the month is marked `skipped`.

*Once, even when workers die.* `claim` takes the month with one `INSERT ... ON CONFLICT DO UPDATE ... WHERE ... RETURNING`: two
workers cannot both get it. A claim left `sending` (a worker that died, or a refusal) is taken again after 30 minutes, at most
5 times a month, always with the same idempotency key `monthly:<org>:<YYYY-MM>`: Resend never sends it twice for 24 hours,
whatever happens between the send and the mark. After 5 attempts the worker gives up and says so once a day.
"""

from __future__ import annotations

import html as html_lib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence
from urllib.parse import quote

import httpx
import psycopg

from . import audit, mailer, schedule
from . import db as cloud_db
from .routes.members import EmailLimit

log = logging.getLogger("nyra.cloud.digest")

SOON_DAYS, LATER_DAYS = 30, 90  # the two lists: expires within SOON_DAYS days, then within SOON_DAYS + 1 to LATER_DAYS days
LIST_MAX = 20                   # lines per list and per brand; the rest is "et N autres"
PERIOD_DAYS = 35                # how far back the first e-mail of an organization looks
MAX_ADMINS = mailer.MAX_RECIPIENTS
LATE_AFTER = timedelta(hours=48)  # after the date of the latest brand, the mail leaves even if a read is not over
RESUME_MINUTES = 30             # a claim left `sending` this long is taken again
MAX_ATTEMPTS = 5                # claims per organization and month
MAX_PER_PASS = 5                # organizations handled by a pass (every 5 minutes): the rest go at the next one
MAX_BRANDS = 50
GIVE_UP_LIST = 50
DAY_SECONDS = 24 * 3600
ORG_LIMIT = 200                 # organizations looked at per page of `due_orgs`
MAX_PAGES = 5                   # pages per pass: at most 1,000 organizations
ALREADY_SENT = "invalid_idempotent_request"  # Resend: this key was used with another body, so the first mail left
_SENTENCES = {
    "unfinished": "La lecture de ce mois n'est pas terminée pour la marque « {} ». Les informations ci-dessous peuvent donc être partielles.",
    "suspect": "La dernière lecture de la marque « {} » semble incomplète : rien n'est marqué retiré pour cette marque.",
    "capped": "La lecture de la marque « {} » s'est arrêtée avant la fin : la limite de pages lues ce mois-ci est atteinte.",
}


@dataclass
class Mail:
    subject: str
    html: str
    text: str
    counts: dict


# --- what it says (pure) ----------------------------------------------------------------------------------------------------

def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _fr(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _when(left: int) -> str:
    return "aujourd'hui" if left == 0 else "demain" if left == 1 else f"dans {left} jours"


def _more(items: Sequence, total: int) -> str:
    left = total - len(items[:LIST_MAX])
    return f"et {left} {_plural(left, 'autre', 'autres')}"


def _subject(org_name: str, counts: dict) -> str:
    facts = [f"{counts['new']} {_plural(counts['new'], 'visuel', 'visuels')} à traiter"] if counts["new"] else []
    if counts["soon"]:
        facts.append(f"{counts['soon']} {_plural(counts['soon'], 'expire', 'expirent')} sous {SOON_DAYS} jours")
    if counts["later"]:
        facts.append(f"{counts['later']} {_plural(counts['later'], 'expire', 'expirent')} d'ici {LATER_DAYS} jours")
    subject = f"Nyra · {' '.join(org_name.split())[:30]} : {', '.join(facts) or 'lecture à vérifier'}"
    return subject if len(subject) <= 100 else subject[:99] + "…"


def _section(blocks: list, title: str, intro: str, brands: list, key: str, link: Callable[[dict], tuple], line: Callable[[dict], str]) -> None:
    shown = [b for b in brands if b[key + "_total"]]
    if not shown:
        return
    blocks += [("h2", title), ("p", intro)]
    for b in shown:
        items = b[key][:LIST_MAX]
        blocks += [("h3", b["name"]), ("ul", [line(item) for item in items] + ([_more(b[key], b[key + "_total"])] if b[key + "_total"] > len(items) else [])),
                   ("a", *link(b))]


def _blocks(data: dict, public_url: str, counts: dict) -> list:
    brands, base = data["brands"], public_url.rstrip("/")

    def link(page: str, label: str) -> Callable[[dict], tuple]:
        return lambda b: (label, f"{base}/o/{quote(data['org_slug'], safe='')}/b/{quote(b['slug'], safe='')}/{page}" if base else None)

    blocks: list = [("h1", f"Nyra : le point du mois pour {data['org_name']}"),
                    ("p", "Bonjour, voici ce qu'il faut savoir ce mois-ci sur les visuels de votre bibliothèque.")]
    blocks += [("p", sentence.format(b["name"])) for key, sentence in _SENTENCES.items() for b in brands if b[key]]
    _section(blocks, "Nouveaux visuels à traiter", "Ces visuels ont des droits expirés et ont été retrouvés en ligne depuis le dernier e-mail. "
             "Aucune décision n'a encore été prise.", brands, "new", link("review", "Ouvrir la page À traiter"),
             lambda i: f"{i['filename']} : expiré le {_fr(i['expiry_date'])}, {i['images']} {_plural(i['images'], 'image', 'images')} en ligne")
    expiring = lambda i: f"{i['filename']} : expire le {_fr(i['expiry_date'])} ({_when(i['days'])})"  # noqa: E731
    _section(blocks, f"Visuels qui expirent dans {SOON_DAYS} jours", "Pensez à les renouveler ou à les remplacer.", brands, "soon",
             link("library", "Ouvrir la bibliothèque"), expiring)
    _section(blocks, f"Visuels qui expirent dans {SOON_DAYS + 1} à {LATER_DAYS} jours", "À prévoir.", brands, "later",
             link("library", "Ouvrir la bibliothèque"), expiring)
    # The settings are a page of a brand (`/o/<org>/b/<brand>/settings`): the first brand's, no link when the address is unknown.
    settings_link = f"{base}/o/{quote(data['org_slug'], safe='')}/b/{quote(brands[0]['slug'], safe='')}/settings?section=notifications" if base and brands else None
    return blocks + [("p", "Ce message est envoyé une fois par mois aux administrateurs de l'organisation."),
                     ("p", "Vous pouvez choisir de ne plus recevoir ce message dans Réglages > Notifications."),
                     ("a", "Ouvrir les réglages de notification", settings_link)]


def _render(blocks: list) -> tuple[str, str]:
    esc = html_lib.escape
    html_parts, text_parts = [], []
    for kind, *args in blocks:
        if kind == "ul":
            html_parts.append("<ul>" + "".join(f"<li>{esc(line)}</li>" for line in args[0]) + "</ul>")
            text_parts.append("\n".join(f"- {line}" for line in args[0]))
        elif kind == "a":
            label, href = args
            if href:
                html_parts.append(f'<p><a href="{esc(href, quote=True)}">{esc(label)}</a></p>')
                text_parts.append(f"{label} : {href}")
        else:
            html_parts.append(f"<{kind}>{esc(args[0])}</{kind}>")
            text_parts.append(args[0])
    html = ('<!doctype html><html lang="fr"><body style="font-family:Arial,sans-serif;color:#1a1a1a;max-width:640px">'
            + "".join(html_parts) + "</body></html>")
    return html, "\n\n".join(text_parts)


def build(data: dict, public_url: str) -> Optional[Mail]:
    """The mail for `gather`'s data, or None when there is nothing to say. Pure: every value is escaped in the HTML."""
    brands = data["brands"]
    counts = {key: sum(b[key + "_total"] for b in brands) for key in ("new", "soon", "later")}
    counts["brands"] = sum(1 for b in brands if b["new_total"] or b["soon_total"] or b["later_total"])
    if not (counts["new"] or counts["soon"] or counts["later"] or any(b[key] for b in brands for key in _SENTENCES)):
        return None
    html, text = _render(_blocks(data, public_url, counts))
    return Mail(_subject(data["org_name"], counts), html, text, counts)


# --- what it reads ------------------------------------------------------------------------------------------------------------

_NEW = """
    SELECT brand_id, filename, expiry_date, images, total FROM (
        SELECT g.*, row_number() OVER (PARTITION BY g.brand_id ORDER BY g.expiry_date, lower(g.filename), g.id) AS rn,
               count(*) OVER (PARTITION BY g.brand_id) AS total
        FROM (SELECT r.id, r.brand_id, r.filename, r.expiry_date, count(*) AS images
              FROM matches m JOIN reference_images r ON r.id = m.reference_id JOIN site_images s ON s.id = m.site_image_id
              WHERE r.org_id = %(org)s AND NOT r.unlimited_rights AND r.expiry_date < %(today)s
                AND GREATEST(s.first_seen, r.created_at) > %(since)s
                AND NOT EXISTS (SELECT 1 FROM reviews v WHERE v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id)
              GROUP BY r.id) g
    ) t WHERE rn <= %(cap)s"""

_EXPIRING = """
    SELECT brand_id, filename, expiry_date, days, bucket, total FROM (
        SELECT t.*, row_number() OVER (PARTITION BY brand_id, bucket ORDER BY expiry_date, lower(filename), id) AS rn,
               count(*) OVER (PARTITION BY brand_id, bucket) AS total
        FROM (SELECT r.id, r.brand_id, r.filename, r.expiry_date, (r.expiry_date - %(today)s::date) AS days,
                     CASE WHEN r.expiry_date - %(today)s::date <= %(soon)s THEN 'soon' ELSE 'later' END AS bucket
              FROM reference_images r
              WHERE r.org_id = %(org)s AND NOT r.unlimited_rights
                AND r.expiry_date BETWEEN %(today)s::date AND %(today)s::date + %(later)s) t
    ) u WHERE rn <= %(cap)s"""

_SUSPECT = """
    SELECT DISTINCT s.brand_id FROM sites s
    JOIN LATERAL (SELECT c.suspect, c.started_at FROM crawl_runs c WHERE c.site_id = s.id AND c.status = 'done'
                  ORDER BY c.started_at DESC LIMIT 1) l ON true
    WHERE s.org_id = %s AND l.suspect AND l.started_at > %s"""

_READS = """
    SELECT DISTINCT ON (brand_id) brand_id, status, COALESCE((result ->> 'quota_reached')::boolean, false) AS capped FROM jobs
    WHERE org_id = %s AND kind = 'crawl' AND params @> '{"scheduled": true}' AND created_at >= %s
    ORDER BY brand_id, created_at DESC"""


def period_start(conn, org_id: uuid.UUID, now: datetime) -> datetime:
    """When the last e-mail left, else `PERIOD_DAYS` days ago."""
    row = conn.execute("SELECT max(sent_at) AS at FROM monthly_digests WHERE org_id = %s AND status = 'sent'", (org_id,)).fetchone()
    return row["at"] or now - timedelta(days=PERIOD_DAYS)


def receives_monthly(role: str, allowed: bool, wanted: bool) -> bool:
    """Pure: the monthly e-mail goes to an administrator the Nyra team allows and who wants it."""
    return role == "admin" and allowed and wanted


def recipients(conn, org_id: uuid.UUID) -> tuple[int, list[str]]:
    """(how many administrators the organization has, the sorted addresses of those who receive the mail, by `receives_monthly`).
    The mail goes to the first `MAX_ADMINS`. Raises `UndefinedColumn` while migration 20261025000035 is not applied."""
    rows = conn.execute(
        """SELECT m.role, m.monthly_email_allowed AS allowed, m.monthly_email_wanted AS wanted, u.email
           FROM memberships m JOIN auth.users u ON u.id = m.user_id WHERE m.org_id = %s""", (org_id,)).fetchall()
    admins = {r["email"].strip().lower() for r in rows if r["role"] == "admin" and r["email"]}
    to = {r["email"].strip().lower() for r in rows if r["email"] and receives_monthly(r["role"], r["allowed"], r["wanted"])}
    return len(admins), sorted(to)


def gather(conn, org_id: uuid.UUID, since: datetime, now: datetime, schedules: Sequence[dict]) -> dict:
    """Everything the mail is made of, per brand, lists cut at `LIST_MAX` rows with their real totals."""
    org = conn.execute("SELECT name, slug FROM organizations WHERE id = %s", (org_id,)).fetchone()
    rows = conn.execute("SELECT id, name, slug FROM brands WHERE org_id = %s ORDER BY lower(name), id LIMIT %s", (org_id, MAX_BRANDS)).fetchall()
    brands = {r["id"]: {"name": r["name"], "slug": r["slug"], "new": [], "new_total": 0, "soon": [], "soon_total": 0, "later": [],
                        "later_total": 0, "suspect": False, "capped": False, "unfinished": False} for r in rows}
    args = {"org": org_id, "today": now.date(), "since": since, "cap": LIST_MAX, "soon": SOON_DAYS, "later": LATER_DAYS}
    for r in conn.execute(_NEW, args).fetchall():
        if r["brand_id"] in brands:
            b = brands[r["brand_id"]]
            b["new"].append({"filename": r["filename"], "expiry_date": r["expiry_date"], "images": r["images"]})
            b["new_total"] = r["total"]
    for r in conn.execute(_EXPIRING, args).fetchall():
        if r["brand_id"] in brands:
            b = brands[r["brand_id"]]
            b[r["bucket"]].append({"filename": r["filename"], "expiry_date": r["expiry_date"], "days": r["days"]})
            b[r["bucket"] + "_total"] = r["total"]
    for r in conn.execute(_SUSPECT, (org_id, since)).fetchall():
        if r["brand_id"] in brands:
            brands[r["brand_id"]]["suspect"] = True
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    for r in conn.execute(_READS, (org_id, month_start)).fetchall():
        if r["brand_id"] in brands:
            brands[r["brand_id"]]["unfinished"] = r["status"] != "done"
            brands[r["brand_id"]]["capped"] = r["status"] == "done" and r["capped"]
    for row in schedules:  # a brand whose read was never queued this month (still due) is not finished either
        if row["brand_id"] in brands and schedule.is_due(row, now):
            brands[row["brand_id"]]["unfinished"] = True
    return {"org_name": org["name"], "org_slug": org["slug"], "brands": list(brands.values())}


# --- when -----------------------------------------------------------------------------------------------------------------------

def is_ready(latest_due: datetime, waiting_reads: int, now: datetime) -> bool:
    """Pure: the latest date of the organization has passed, and no scheduled read waits or runs, or 48 hours have passed."""
    return now >= latest_due and (waiting_reads == 0 or now >= latest_due + LATE_AFTER)


_CANDIDATES = """
    SELECT s.org_id FROM brand_schedules s
    WHERE s.enabled AND s.org_id > %s AND NOT EXISTS (
        SELECT 1 FROM monthly_digests d WHERE d.org_id = s.org_id AND d.month = %s
          AND (d.status <> 'sending' OR d.attempts >= %s OR d.updated_at > now() - make_interval(mins => %s)))
    GROUP BY s.org_id HAVING max(s.day_of_month) <= %s
    ORDER BY s.org_id LIMIT %s"""


def _ready(conn, org_ids: list[uuid.UUID], now: datetime) -> list[dict]:
    """Among these organizations (whole: all their rows), those whose mail may leave: `{org_id, schedules}`. A brand counts
    only if its schedule existed BEFORE this month's date: one switched on after it is not read this month."""
    by_org: dict[uuid.UUID, list[dict]] = {}
    for row in conn.execute("SELECT brand_id, org_id, enabled, day_of_month, last_run_at, created_at FROM brand_schedules "
                            "WHERE enabled AND org_id = ANY(%s) ORDER BY org_id, brand_id", (org_ids,)).fetchall():
        if row["created_at"] < schedule.due_date(row["brand_id"], row["day_of_month"], now.year, now.month):
            by_org.setdefault(row["org_id"], []).append(row)
    latest = {org: max(schedule.due_date(r["brand_id"], r["day_of_month"], now.year, now.month) for r in rows) for org, rows in by_org.items()}
    passed = [org for org, at in latest.items() if at <= now]
    waiting = {r["org_id"]: r["n"] for r in conn.execute(
        """SELECT org_id, count(*) AS n FROM jobs WHERE org_id = ANY(%s) AND kind = 'crawl' AND status IN ('queued', 'running')
           AND params @> '{"scheduled": true}' GROUP BY org_id""", (passed,)).fetchall()}
    return [{"org_id": org, "schedules": by_org[org]} for org in passed if is_ready(latest[org], waiting.get(org, 0), now)]


def due_orgs(conn, now: datetime) -> list[dict]:
    """The organizations whose mail of this month may leave, `{org_id, schedules}`. The filter is per ORGANIZATION, in SQL:
    those with the monthly read on whose latest day of the month has come and that are not handled, claimed or given up yet,
    `ORG_LIMIT` per page in `org_id` order, at most `MAX_PAGES` pages, and it stops once `MAX_PER_PASS` are ready. An
    organization is always read whole. (Day only: the hour is checked in Python by `_ready`.)"""
    month, after, ready = date(now.year, now.month, 1), uuid.UUID(int=0), []
    for _ in range(MAX_PAGES):
        ids = [r["org_id"] for r in conn.execute(_CANDIDATES, (after, month, MAX_ATTEMPTS, RESUME_MINUTES, now.day, ORG_LIMIT)).fetchall()]
        if ids:
            ready += _ready(conn, ids, now)
        if len(ids) < ORG_LIMIT or len(ready) >= MAX_PER_PASS:
            break
        after = ids[-1]
    return ready


def claim(conn, org_id: uuid.UUID, month: date) -> Optional[int]:
    """Take the month for this worker: the attempt number, or None when it is sent, skipped, claimed less than 30 minutes ago
    or given up (5 attempts). ONE statement, so that two workers cannot both win."""
    row = conn.execute(
        """INSERT INTO monthly_digests (org_id, month, status, attempts) VALUES (%s, %s, 'sending', 1)
           ON CONFLICT (org_id, month) DO UPDATE SET attempts = monthly_digests.attempts + 1, updated_at = now()
           WHERE monthly_digests.status = 'sending' AND monthly_digests.attempts < %s
             AND monthly_digests.updated_at < now() - make_interval(mins => %s)
           RETURNING attempts""",
        (org_id, month, MAX_ATTEMPTS, RESUME_MINUTES)).fetchone()
    return None if row is None else row["attempts"]


def _mark(conn, org_id: uuid.UUID, month: date, status: str, now: datetime) -> bool:
    """Close the month (`sent` or `skipped`); False when it was not ours any more (already closed by another worker)."""
    return conn.execute(
        "UPDATE monthly_digests SET status = %s, sent_at = %s, updated_at = now() WHERE org_id = %s AND month = %s AND status = 'sending'",
        (status, now if status == "sent" else None, org_id, month)).rowcount == 1


# --- doing it -----------------------------------------------------------------------------------------------------------------

def _skip(database_url: str, org_id: uuid.UUID, month: date, now: datetime, reason: str) -> None:
    with cloud_db.connect(database_url) as conn:
        if _mark(conn, org_id, month, "skipped", now):
            audit.record(conn, org_id, None, "digest.skipped", "monthly_digest", org_id, reason=reason)


def _why(exc: BaseException) -> str:
    """Safe to log: Resend's message is cut and holds no key (`MailError`); anything else could repeat an address."""
    return str(exc) if isinstance(exc, mailer.MailError) else type(exc).__name__


def _close_as_sent(database_url: str, org_id: uuid.UUID, month: date, now: datetime, mail: Mail, sent_to: int, admins: int,
                   recovered: bool) -> None:
    detail = {"recovered": 1} if recovered else {}
    with cloud_db.connect(database_url) as conn:
        if _mark(conn, org_id, month, "sent", now):
            audit.record(conn, org_id, None, "digest.sent", "monthly_digest", org_id, recipients=sent_to, admins=admins, **mail.counts, **detail)


def _send(settings, org_id: uuid.UUID, month: date, now: datetime, mail: Mail, to: list[str], admins: int, attempt: int,
          transport: Optional[httpx.BaseTransport], sleep: Callable[[float], None]) -> int:
    key, recovered, cut = f"monthly:{org_id}:{month:%Y-%m}", False, to[:MAX_ADMINS]
    try:
        # MailNotConfigured cannot come out of here: `run` checked the settings before claiming anything.
        mailer.send(settings, org_id, cut, mail.subject, mail.html, mail.text, idempotency_key=key, transport=transport, sleep=sleep)
    except mailer.MailError as exc:
        if exc.code != ALREADY_SENT:
            return _failed(settings.database_url, org_id, attempt, exc)
        # The key was seen with another body: an earlier attempt sent the mail and died before marking it (the content has
        # changed since). It left; nothing is sent again.
        recovered = True
        log.info("monthly e-mail of organization %s had already left (the content changed since): marked as sent", org_id)
    except (EmailLimit, ValueError) as exc:
        return _failed(settings.database_url, org_id, attempt, exc)
    _close_as_sent(settings.database_url, org_id, month, now, mail, len(cut), admins, recovered)
    if len(to) > len(cut):
        log.warning("monthly e-mail of organization %s: %d administrators receive it, sent to the first %d only", org_id, len(to), len(cut))
    log.info("monthly e-mail of organization %s sent to %d administrator(s)", org_id, len(cut))
    return 1


def _failed(database_url: str, org_id: uuid.UUID, attempt: int, exc: BaseException) -> int:
    log.warning("monthly e-mail of organization %s not sent (attempt %d of %d): %s", org_id, attempt, MAX_ATTEMPTS, _why(exc))
    with cloud_db.connect(database_url) as conn:
        audit.record(conn, org_id, None, "digest.failed", "monthly_digest", org_id, error=type(exc).__name__, attempt=attempt)
    return 0


def _handle(settings, org: dict, month: date, now: datetime, transport: Optional[httpx.BaseTransport],
            sleep: Callable[[float], None]) -> int:
    org_id = org["org_id"]
    with cloud_db.connect(settings.database_url) as conn:  # before the claim: without the columns of migration 35, nothing is claimed
        admins, to = recipients(conn, org_id)
    with cloud_db.connect(settings.database_url) as conn:  # committed at once: the other workers see the claim
        attempt = claim(conn, org_id, month)
    if attempt is None:
        return 0
    with cloud_db.connect(settings.database_url) as conn:
        data = gather(conn, org_id, period_start(conn, org_id, now), now, org["schedules"])
    mail = build(data, settings.public_url)
    if mail is None or not to:
        _skip(settings.database_url, org_id, month, now, "nothing" if mail is None else "no_admin" if not admins else "no_recipient")
        return 0
    return _send(settings, org_id, month, now, mail, to, admins, attempt, transport, sleep)


def _report_given_up(conn, month: date) -> None:
    """Say, once a day and without a trace, that an organization's mail was abandoned after `MAX_ATTEMPTS` attempts."""
    for row in conn.execute("SELECT org_id FROM monthly_digests WHERE month = %s AND status = 'sending' AND attempts >= %s LIMIT %s",
                            (month, MAX_ATTEMPTS, GIVE_UP_LIST)).fetchall():
        if schedule.once_per_hour(f"digest-abandoned:{row['org_id']}", every=DAY_SECONDS):
            log.warning("monthly e-mail of organization %s abandoned after %d attempts this month: it will not be sent", row["org_id"], MAX_ATTEMPTS)


def run(settings: Any, now: Optional[datetime] = None, *, database_url: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep) -> int:
    """One pass of the worker: send the monthly e-mails that are due (at most `MAX_PER_PASS` organizations); how many left.
    `settings` None means the e-mail settings were unusable at start (then `database_url` is needed): the same as not
    configured. One organization's failure is logged and does not stop the others."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    month = date(now.year, now.month, 1)
    with cloud_db.connect(database_url or settings.database_url) as conn:
        _report_given_up(conn, month)
        ready = due_orgs(conn, now)[:MAX_PER_PASS]
    if not ready:
        return 0
    if settings is None or not (settings.resend_api_key and settings.mail_from):
        if schedule.once_per_hour("digest-not-configured"):
            log.warning("monthly e-mails are not sent: e-mail sending is not configured, set both RESEND_API_KEY and NYRA_MAIL_FROM")
        return 0
    if not settings.public_url and schedule.once_per_hour("digest-no-public-url"):
        log.warning("NYRA_PUBLIC_URL is not set: the monthly e-mails leave without links to the application")
    sent = 0
    for org in ready:
        try:
            sent += _handle(settings, org, month, now, transport, sleep)
        except Exception as exc:  # noqa: BLE001 - one organization's failure must not stop the others; its claim is taken again later
            if isinstance(exc, psycopg.errors.UndefinedColumn) and "monthly_email" in str(exc):
                raise  # migration 35 is not applied: every organization would fail the same way, the worker says so once an hour
            if schedule.once_per_hour(f"digest-fail:{org['org_id']}"):
                log.exception("monthly e-mail of organization %s failed; it will be tried again", org["org_id"])
            else:
                log.warning("monthly e-mail of organization %s failed again; it will be tried again", org["org_id"])
    return sent
