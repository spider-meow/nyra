"""What an organization uses, and what it may use (quotas).

Counters live in `usage_counters` (one row per organization, period and kind): the worker adds the pages it read,
the images it found and the bytes it downloaded to the current month; the API counts invitation e-mails per day.
The rest (brands, addresses, library, storage, members) is counted from the tables when asked, so it can't drift.

Why a table and not a sum over `crawl_runs`: a page check opens pages without recording a run, and the table is
the single figure the cap, the organization's page and the back office all read. The limits are
`config.QuotaConfig`; the Nyra team overrides them per organization in `organizations.quota_overrides`.
Nothing here speaks of prices: it is "usage" and "limit".
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from typing import Any, Optional

import psycopg

from nyra.config import QuotaConfig, validate_quota

from . import db as cloud_db

Row = dict[str, Any]

log = logging.getLogger("nyra.usage")

GB = 1_000_000_000
WARNING_RATIO = 0.8
# Organizations listed in the back office.
ORGS_LIMIT = 200

CAPPED = ("Limite d'usage atteinte (pages du mois ou stockage) : la lecture s'est arrêtée avant la fin. Elle pourra "
          "reprendre le mois prochain, ou dès que l'équipe Nyra relève la limite.")

# key, label shown, column of `_OVERVIEW`, field of QuotaConfig
ITEMS = (
    ("pages", "Pages lues ce mois-ci", "pages_read", "pages_per_month"),
    ("brands", "Marques", "brands", "brands"),
    ("sites", "Adresses (marque la plus fournie)", "max_sites", "sites_per_brand"),
    ("references", "Visuels dans la bibliothèque", "refs", "references"),
    ("storage", "Stockage", "storage_bytes", "storage_gb"),
    ("members", "Membres", "members", "members"),
    ("emails", "Invitations envoyées aujourd'hui", "emails_today", "emails_per_day"),
)

_OVERVIEW = """
WITH counters AS (
    SELECT org_id,
           COALESCE(SUM(amount) FILTER (WHERE kind = 'pages_read' AND period = %(month)s), 0)::bigint AS pages_read,
           COALESCE(SUM(amount) FILTER (WHERE kind = 'images_new' AND period = %(month)s), 0)::bigint AS images_new,
           COALESCE(SUM(amount) FILTER (WHERE kind = 'bytes_downloaded' AND period = %(month)s), 0)::bigint AS bytes_downloaded,
           COALESCE(SUM(amount) FILTER (WHERE kind = 'emails' AND period = %(today)s), 0)::bigint AS emails_today
    FROM usage_counters WHERE period IN (%(month)s, %(today)s) {only} GROUP BY org_id),
library AS (
    SELECT org_id, COUNT(*) AS refs, COALESCE(SUM(byte_size), 0)::bigint AS bytes
    FROM reference_images WHERE TRUE {only} GROUP BY org_id),
files AS (  -- an image read is stored once per organization, whatever the number of addresses that show it
    SELECT org_id, COALESCE(SUM(stored_bytes), 0)::bigint AS bytes FROM (
        SELECT DISTINCT ON (org_id, content_hash) org_id, stored_bytes FROM site_images
        WHERE content_hash IS NOT NULL {only} ORDER BY org_id, content_hash) one_per_file
    GROUP BY org_id),
brand_counts AS (SELECT org_id, COUNT(*) AS n FROM brands WHERE TRUE {only} GROUP BY org_id),
site_counts AS (
    SELECT org_id, MAX(n) AS n FROM (SELECT org_id, brand_id, COUNT(*) AS n FROM sites WHERE TRUE {only}
                                     GROUP BY org_id, brand_id) per_brand GROUP BY org_id),
member_counts AS (SELECT org_id, COUNT(*) AS n FROM memberships WHERE TRUE {only} GROUP BY org_id)
SELECT o.id, o.name, o.slug, o.quota_overrides,
       COALESCE(c.pages_read, 0) AS pages_read, COALESCE(c.images_new, 0) AS images_new,
       COALESCE(c.bytes_downloaded, 0) AS bytes_downloaded, COALESCE(c.emails_today, 0) AS emails_today,
       COALESCE(b.n, 0) AS brands, COALESCE(s.n, 0) AS max_sites, COALESCE(l.refs, 0) AS refs,
       COALESCE(m.n, 0) AS members, COALESCE(l.bytes, 0) + COALESCE(f.bytes, 0) AS storage_bytes
FROM organizations o
LEFT JOIN counters c ON c.org_id = o.id LEFT JOIN library l ON l.org_id = o.id LEFT JOIN files f ON f.org_id = o.id
LEFT JOIN brand_counts b ON b.org_id = o.id LEFT JOIN site_counts s ON s.org_id = o.id
LEFT JOIN member_counts m ON m.org_id = o.id
WHERE TRUE {only_org} ORDER BY lower(o.name) LIMIT %(limit)s"""

_RECORD = """
INSERT INTO usage_counters (org_id, period, kind, amount)
SELECT %s, %s, kind, amount FROM unnest(%s::text[], %s::bigint[]) AS t(kind, amount) WHERE amount > 0
ON CONFLICT (org_id, period, kind) DO UPDATE SET amount = usage_counters.amount + excluded.amount"""

_CLAIM_EMAIL = """
INSERT INTO usage_counters (org_id, period, kind, amount) VALUES (%s, %s, 'emails', 1)
ON CONFLICT (org_id, period, kind) DO UPDATE SET amount = usage_counters.amount + 1
WHERE usage_counters.amount < %s RETURNING amount"""


def today() -> date:
    return datetime.now(timezone.utc).date()


def month_start() -> date:
    return today().replace(day=1)


def level(used: float, limit: float) -> str:
    """ok, warning (80 % of the limit or more) or reached."""
    if used >= limit:
        return "reached"
    return "warning" if used >= WARNING_RATIO * limit else "ok"


# --- writing -------------------------------------------------------------------------

def record(conn: psycopg.Connection, org_id: uuid.UUID, *, pages: int = 0, images: int = 0, nbytes: int = 0) -> None:
    """Add what the worker did to the current month."""
    conn.execute(_RECORD, (org_id, month_start(), ["pages_read", "images_new", "bytes_downloaded"], [pages, images, nbytes]))


def claim_email(conn: psycopg.Connection, org_id: uuid.UUID, limit: int) -> bool:
    """Take one of the organization's e-mails of the day: False when the day's limit is used up.

    Call it before sending; on False, keep the message and try again tomorrow (invitations ask the person to retry;
    the summaries and alerts of AXE-523 will keep theirs pending). Atomic: two senders can't both take the last one."""
    if limit <= 0:
        return False
    return conn.execute(_CLAIM_EMAIL, (org_id, today(), limit)).fetchone() is not None


class Meter:
    """Writes what a read did since the last `flush` (the difference, so each call adds only what is new)."""

    def __init__(self, database_url: str, org_id: uuid.UUID):
        self.database_url, self.org_id, self._sent = database_url, org_id, (0, 0, 0)

    def flush(self, stats: Any) -> None:
        now = (stats.pages_visited, stats.images_new, stats.bytes_downloaded)
        pages, images, nbytes = (after - before for after, before in zip(now, self._sent))
        if pages or images or nbytes:
            with cloud_db.connect(self.database_url) as conn:
                record(conn, self.org_id, pages=pages, images=images, nbytes=nbytes)
        self._sent = now


def release_email(conn: psycopg.Connection, org_id: uuid.UUID) -> None:
    """Give back an e-mail of the day taken by `claim_email` when the provider then failed to send it."""
    conn.execute("UPDATE usage_counters SET amount = amount - 1 WHERE org_id = %s AND period = %s AND kind = 'emails' AND amount > 0",
                 (org_id, today()))


def set_overrides(conn: psycopg.Connection, org_id: uuid.UUID, raw: dict[str, Any], by: Optional[uuid.UUID] = None) -> Optional[dict[str, Any]]:
    """Replace the limits the Nyra team set for one organization (empty = the defaults). Returns what was kept,
    None when the organization doesn't exist. Raises ValueError (French message) on a bad value.
    Who changed what is logged (`by` = the platform admin)."""
    clean = validate_quota(raw)
    row = conn.execute("""UPDATE organizations o SET quota_overrides = %s FROM (SELECT id, quota_overrides AS old FROM organizations
                          WHERE id = %s) p WHERE o.id = p.id RETURNING p.old""", (json.dumps(clean), org_id)).fetchone()
    if row:
        log.info("limits of organization %s changed by %s: %s -> %s", org_id, by, row["old"], clean)
    return clean if row else None


def _overrides(raw: Any, org_id: Any) -> dict[str, Any]:
    """The limits stored for an organization; the defaults (and an error in the log) if what is stored is not valid,
    so one bad row cannot stop the back office or the organization's jobs."""
    try:
        return validate_quota(raw)
    except ValueError:
        log.error("limits stored for organization %s are not valid, defaults used: %r", org_id, raw, exc_info=True)
        return {}


# --- reading -------------------------------------------------------------------------

def _items(row: Row, quota: QuotaConfig) -> list[Row]:
    out = []
    for key, label, column, field in ITEMS:
        limit = getattr(quota, field)
        limit = int(limit * GB) if key == "storage" else limit
        out.append({"key": key, "label": label, "used": int(row[column]), "limit": limit,
                    "level": level(row[column], limit)})
    return out


def overview(conn: psycopg.Connection, base: QuotaConfig, org_id: Optional[uuid.UUID] = None,
             limit: int = ORGS_LIMIT) -> list[Row]:
    """Usage against limits, for one organization or (back office) for all, at most `limit`, by name."""
    only = "AND org_id = %(org)s" if org_id else ""
    sql = _OVERVIEW.format(only=only, only_org="AND o.id = %(org)s" if org_id else "")
    rows = conn.execute(sql, {"month": month_start(), "today": today(), "org": org_id, "limit": limit}).fetchall()
    return [_entry(row, base) for row in rows]


def _entry(row: Row, base: QuotaConfig) -> Row:
    kept = _overrides(row["quota_overrides"], row["id"])
    return {
        "id": str(row["id"]), "name": row["name"], "slug": row["slug"], "overrides": kept,
        "items": _items(row, replace(base, **kept)),
        "counters": {"images_new": int(row["images_new"]), "bytes_downloaded": int(row["bytes_downloaded"])},
    }


def defaults(base: QuotaConfig) -> dict[str, Any]:
    return asdict(base)


def quota_for(conn: psycopg.Connection, base: QuotaConfig, org_id: uuid.UUID) -> QuotaConfig:
    row = conn.execute("SELECT quota_overrides FROM organizations WHERE id = %s", (org_id,)).fetchone()
    return replace(base, **_overrides(row["quota_overrides"], org_id)) if row else base


def pages_used(conn: psycopg.Connection, base: QuotaConfig, org_id: uuid.UUID) -> tuple[int, int]:
    """(pages read this month, the month's limit)."""
    row = conn.execute("SELECT amount FROM usage_counters WHERE org_id = %s AND period = %s AND kind = 'pages_read'",
                       (org_id, month_start())).fetchone()
    return (row["amount"] if row else 0), quota_for(conn, base, org_id).pages_per_month


def pages_note(used: int, limit: int) -> str:
    """The sentence a finished read adds once the month is at 80 % of its limit (nothing before)."""
    if level(used, limit) == "ok":
        return ""
    return f"Usage du mois : {used:,} pages lues sur {limit:,}.".replace(",", " ")


def check(conn: psycopg.Connection, base: QuotaConfig, org_id: uuid.UUID, key: str, adding: int = 1,
          brand_id: Optional[uuid.UUID] = None) -> Optional[str]:
    """The sentence that refuses adding `adding` to `key` (an ITEMS key) when it would pass the limit; None if there is room.
    `adding` is in bytes for the storage. For the addresses, pass the brand: the limit is per brand."""
    if adding <= 0:  # replacing a visual of the same name adds nothing, even above a limit lowered under the usage
        return None
    entries = overview(conn, base, org_id)
    if not entries:
        return None
    item = next(item for item in entries[0]["items"] if item["key"] == key)
    used = item["used"]
    if key == "sites":
        used = conn.execute("SELECT COUNT(*) AS n FROM sites WHERE brand_id = %s", (brand_id,)).fetchone()["n"]
    if used + adding <= item["limit"]:
        return None
    shown = (lambda n: f"{n / GB:.1f} Go") if key == "storage" else (lambda n: f"{n:,}".replace(",", " "))
    return (f"Limite atteinte ({item['label'].lower()}) : {shown(used)} sur {shown(item['limit'])}. "
            "Contactez l'équipe Nyra pour relever la limite de votre organisation.")
