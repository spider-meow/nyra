"""Postgres persistence for the hosted (Supabase) product.

Targets the schema in `supabase/migrations/`: uuid ids and an `org_id` on
every row. Every function takes a connection; callers get one from
`connect()`, which hands out connections from a per-URL pool instead of
opening a new TCP + TLS session to Supabase for every request.

The backend connects with a privileged Postgres role (the `DATABASE_URL`
in `.env.example`), which bypasses Row Level Security. Authorization
(does this caller have the right role in this org?) is `cloud.auth`'s job,
checked before these functions are called, and every query here filters
on `org_id` itself. RLS (see the row_level_security migration) is the
safety net for anything that queries Postgres a different way.
"""

from __future__ import annotations

import atexit
import json
import re
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Row = dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# user:password@host — the password may itself contain "@". The first "@"
# would otherwise be read as the end of the user info, and the rest of the
# password would be treated as the hostname.
_DATABASE_URL = re.compile(
    r"^(?P<scheme>postgres(?:ql)?://)"
    r"(?P<user>[^:@/]+):"
    r"(?P<password>.*)"
    r"@"
    r"(?P<host>(?:[a-zA-Z0-9.-]+))"
    r"(?P<rest>:\d+(?:/.*)?)$"
)


def normalize_database_url(database_url: str) -> str:
    url = database_url.strip()
    match = _DATABASE_URL.match(url)
    if match is None:
        return url
    password = match.group("password")
    if not any(char in password for char in "@/?# "):
        return url
    return (
        f"{match.group('scheme')}{quote(match.group('user'), safe='.')}:"
        f"{quote(password, safe='')}@"
        f"{match.group('host')}{match.group('rest')}"
    )


_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)
    conn.commit()


def pool(database_url: str) -> ConnectionPool:
    url = normalize_database_url(database_url)
    with _pools_lock:
        existing = _pools.get(url)
        if existing is None:
            existing = ConnectionPool(
                url,
                min_size=1,
                max_size=10,
                kwargs={"row_factory": dict_row},
                configure=_configure,
                open=True,
                name="nyra",
            )
            _pools[url] = existing
        return existing


def close_pools() -> None:
    with _pools_lock:
        for existing in _pools.values():
            existing.close()
        _pools.clear()


atexit.register(close_pools)


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    """A pooled connection, committed on success and rolled back on error."""
    with pool(database_url).connection() as conn:
        yield conn


def ping(database_url: str) -> bool:
    with connect(database_url) as conn:
        conn.execute("SELECT 1")
    return True


# --- organizations / memberships -------------------------------------

# Lowercase ASCII, digits, single hyphens. No leading or trailing hyphen.
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")
SLUG_MAX_LENGTH = 63


def slugify(value: str) -> str:
    """Canonical organization slug: `Rémy Martin` and `remy--martin` both become `remy-martin`."""
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _SLUG_UNSAFE.sub("-", text).strip("-")
    text = text[:SLUG_MAX_LENGTH].strip("-")
    if not text:
        raise ValueError("Slug is empty after normalization. Use letters or digits, for example remy-martin.")
    return text


def create_organization(conn: psycopg.Connection, *, name: str, slug: str) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO organizations (name, slug) VALUES (%s, %s) RETURNING id", (name, slugify(slug))
    ).fetchone()
    return row["id"]


def get_organization(conn: psycopg.Connection, org_id: uuid.UUID) -> Optional[Row]:
    return conn.execute("SELECT * FROM organizations WHERE id = %s", (org_id,)).fetchone()


def get_organization_by_slug(conn: psycopg.Connection, slug: str) -> Optional[Row]:
    try:
        slug = slugify(slug)
    except ValueError:
        return None
    return conn.execute("SELECT * FROM organizations WHERE slug = %s", (slug,)).fetchone()


def add_membership(conn: psycopg.Connection, *, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO memberships (user_id, org_id, role) VALUES (%s, %s, %s)
        ON CONFLICT (user_id, org_id) DO UPDATE SET role = excluded.role
        RETURNING id
        """,
        (user_id, org_id, role),
    ).fetchone()
    return row["id"]


def get_membership(conn: psycopg.Connection, *, user_id: uuid.UUID, org_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM memberships WHERE user_id = %s AND org_id = %s", (user_id, org_id)
    ).fetchone()


def list_memberships_for_user(conn: psycopg.Connection, user_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT m.org_id, m.role, o.name AS org_name, o.slug AS org_slug
        FROM memberships m JOIN organizations o ON o.id = m.org_id
        WHERE m.user_id = %s ORDER BY o.name
        """,
        (user_id,),
    ).fetchall()


# --- settings -------------------------------------------------------------

def get_overrides(conn: psycopg.Connection, org_id: uuid.UUID) -> dict:
    row = conn.execute("SELECT overrides FROM org_settings WHERE org_id = %s", (org_id,)).fetchone()
    return dict(row["overrides"]) if row else {}


def set_overrides(conn: psycopg.Connection, org_id: uuid.UUID, overrides: dict, updated_by: Optional[uuid.UUID]) -> None:
    conn.execute(
        """
        INSERT INTO org_settings (org_id, overrides, updated_by) VALUES (%s, %s, %s)
        ON CONFLICT (org_id) DO UPDATE SET overrides = excluded.overrides,
            updated_by = excluded.updated_by, updated_at = now()
        """,
        (org_id, json.dumps(overrides), updated_by),
    )


# --- sites --------------------------------------------------------------

def upsert_site(conn: psycopg.Connection, *, org_id: uuid.UUID, url: str, label: Optional[str] = None) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO sites (org_id, url, label) VALUES (%s, %s, %s)
        ON CONFLICT (org_id, url) DO UPDATE SET label = COALESCE(excluded.label, sites.label)
        RETURNING id
        """,
        (org_id, url, label),
    ).fetchone()
    return row["id"]


def list_sites(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute("SELECT * FROM sites WHERE org_id = %s ORDER BY created_at DESC", (org_id,)).fetchall()


# --- reference_images ------------------------------------------------

def upsert_reference_image(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    filename: str,
    storage_path: str,
    expiry_date: Optional[str],
    credit: Optional[str],
    notes: Optional[str],
    phash: Optional[str] = None,
    dhash: Optional[str] = None,
    phash_flip: Optional[str] = None,
    dhash_flip: Optional[str] = None,
    embedding=None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    thumb_path: Optional[str] = None,
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO reference_images
            (org_id, filename, storage_path, expiry_date, credit, notes,
             phash, dhash, phash_flip, dhash_flip, embedding, width, height, thumb_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (org_id, filename) DO UPDATE SET
            storage_path=excluded.storage_path,
            expiry_date=excluded.expiry_date,
            credit=excluded.credit,
            notes=excluded.notes,
            phash=excluded.phash,
            dhash=excluded.dhash,
            phash_flip=excluded.phash_flip,
            dhash_flip=excluded.dhash_flip,
            embedding=excluded.embedding,
            width=excluded.width,
            height=excluded.height,
            thumb_path=excluded.thumb_path,
            compared_at=CASE
                WHEN reference_images.phash IS DISTINCT FROM excluded.phash
                  OR reference_images.dhash IS DISTINCT FROM excluded.dhash
                  OR reference_images.phash_flip IS DISTINCT FROM excluded.phash_flip
                  OR reference_images.embedding IS DISTINCT FROM excluded.embedding
                THEN NULL
                ELSE reference_images.compared_at
            END
        RETURNING id
        """,
        (org_id, filename, storage_path, expiry_date, credit, notes, phash, dhash, phash_flip, dhash_flip,
         embedding, width, height, thumb_path),
    ).fetchone()
    return row["id"]


_REF_LIST_COLUMNS = """id, filename, storage_path, thumb_path, expiry_date, credit, notes, width, height,
    phash IS NOT NULL AS hashed, embedding IS NOT NULL AS embedded, compared_at, created_at, updated_at"""


def list_references(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    """Library listing: no embeddings, no hashes."""
    return conn.execute(
        f"SELECT {_REF_LIST_COLUMNS} FROM reference_images WHERE org_id = %s ORDER BY lower(filename)", (org_id,)
    ).fetchall()


def get_reference_images(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        "SELECT * FROM reference_images WHERE org_id = %s ORDER BY expiry_date NULLS LAST", (org_id,)
    ).fetchall()


def get_reference_by_filename(conn: psycopg.Connection, org_id: uuid.UUID, filename: str) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM reference_images WHERE org_id = %s AND filename = %s", (org_id, filename)
    ).fetchone()


def existing_filenames(conn: psycopg.Connection, org_id: uuid.UUID, filenames: list[str]) -> set[str]:
    rows = conn.execute(
        "SELECT filename FROM reference_images WHERE org_id = %s AND filename = ANY(%s)", (org_id, filenames)
    ).fetchall()
    return {row["filename"] for row in rows}


def update_reference_meta(
    conn: psycopg.Connection, org_id: uuid.UUID, filename: str, *,
    expiry_date: Optional[str], credit: Optional[str], notes: Optional[str],
) -> bool:
    result = conn.execute(
        """UPDATE reference_images SET expiry_date = %s, credit = %s, notes = %s
           WHERE org_id = %s AND filename = %s""",
        (expiry_date, credit, notes, org_id, filename),
    )
    return result.rowcount > 0


def set_expiry_for(conn: psycopg.Connection, org_id: uuid.UUID, filenames: list[str], expiry_date: Optional[str]) -> int:
    result = conn.execute(
        "UPDATE reference_images SET expiry_date = %s WHERE org_id = %s AND filename = ANY(%s)",
        (expiry_date, org_id, filenames),
    )
    return result.rowcount


def delete_references(conn: psycopg.Connection, org_id: uuid.UUID, filenames: list[str]) -> list[Row]:
    """Delete and return the deleted rows' storage paths, for cleanup in Storage."""
    return conn.execute(
        """DELETE FROM reference_images WHERE org_id = %s AND filename = ANY(%s)
           RETURNING filename, storage_path, thumb_path""",
        (org_id, filenames),
    ).fetchall()


# --- site images (see cloud.store for the crawler-facing writes) --------

def get_site_images(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute("SELECT * FROM site_images WHERE org_id = %s", (org_id,)).fetchall()


def link_image_page(conn: psycopg.Connection, image_id: uuid.UUID, page_id: uuid.UUID) -> None:
    conn.execute(
        "INSERT INTO image_pages (image_id, page_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (image_id, page_id)
    )


def get_pages_for_image(conn: psycopg.Connection, image_id: uuid.UUID) -> list[str]:
    rows = conn.execute(
        """SELECT p.url FROM pages p JOIN image_pages ip ON ip.page_id = p.id
           WHERE ip.image_id = %s ORDER BY p.url""",
        (image_id,),
    ).fetchall()
    return [r["url"] for r in rows]


# --- matches ---------------------------------------------------------------

def clear_matches(conn: psycopg.Connection, org_id: uuid.UUID) -> None:
    conn.execute("DELETE FROM matches WHERE org_id = %s", (org_id,))


def delete_matches_for(
    conn: psycopg.Connection, *, org_id: uuid.UUID, reference_ids: list[uuid.UUID], site_ids: list[uuid.UUID]
) -> None:
    if reference_ids:
        conn.execute("DELETE FROM matches WHERE org_id = %s AND reference_id = ANY(%s)", (org_id, reference_ids))
    if site_ids:
        conn.execute("DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)", (org_id, site_ids))


def write_matches(conn: psycopg.Connection, org_id: uuid.UUID, rows: list[tuple]) -> None:
    """`rows` is (reference_id, site_image_id, level, score, confidence)."""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO matches (org_id, reference_id, site_image_id, level, score, confidence)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (reference_id, site_image_id) DO UPDATE SET
                level=excluded.level, score=excluded.score, confidence=excluded.confidence, created_at=now()
            """,
            [(org_id, *row) for row in rows],
        )


def stamp_compared(conn: psycopg.Connection, reference_ids: list[uuid.UUID], site_ids: list[uuid.UUID]) -> None:
    if reference_ids:
        conn.execute("UPDATE reference_images SET compared_at = now() WHERE id = ANY(%s)", (reference_ids,))
    if site_ids:
        conn.execute("UPDATE site_images SET compared_at = now() WHERE id = ANY(%s)", (site_ids,))


def get_match_signature(conn: psycopg.Connection, org_id: uuid.UUID) -> Optional[str]:
    row = conn.execute("SELECT signature FROM match_meta WHERE org_id = %s", (org_id,)).fetchone()
    return None if row is None else row["signature"]


def set_match_signature(conn: psycopg.Connection, org_id: uuid.UUID, signature: str) -> None:
    conn.execute(
        """
        INSERT INTO match_meta (org_id, signature, finished_at) VALUES (%s, %s, now())
        ON CONFLICT (org_id) DO UPDATE SET signature = excluded.signature, finished_at = excluded.finished_at
        """,
        (org_id, signature),
    )


def set_reviews(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    reference_id: uuid.UUID,
    site_image_ids: list[uuid.UUID],
    decision: str,
    reviewed_by: Optional[uuid.UUID],
) -> int:
    """Record a decision on matches of this organization. Returns rows touched.

    Pairs are checked against `matches` for this org, so a caller can't
    attach a review to another organization's images.
    """
    if not site_image_ids:
        return 0
    if not decision:
        result = conn.execute(
            "DELETE FROM reviews WHERE org_id = %s AND reference_id = %s AND site_image_id = ANY(%s)",
            (org_id, reference_id, site_image_ids),
        )
        return result.rowcount
    result = conn.execute(
        """
        INSERT INTO reviews (reference_id, site_image_id, org_id, decision, reviewed_by, updated_at)
        SELECT m.reference_id, m.site_image_id, m.org_id, %s, %s, now()
        FROM matches m
        WHERE m.org_id = %s AND m.reference_id = %s AND m.site_image_id = ANY(%s)
        ON CONFLICT (reference_id, site_image_id) DO UPDATE SET
            decision=excluded.decision, reviewed_by=excluded.reviewed_by, updated_at=excluded.updated_at
        """,
        (decision, reviewed_by, org_id, reference_id, site_image_ids),
    )
    return result.rowcount


def match_rows(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    """Every match with its reference, image, decision and pages, in one query."""
    rows = conn.execute(
        """
        SELECT
            m.level, m.score, m.confidence,
            r.id AS reference_id, r.filename, r.expiry_date, r.credit, r.notes,
            r.storage_path AS ref_storage_path, r.thumb_path AS ref_thumb_path,
            s.id AS site_image_id, s.url AS site_url, s.content_hash,
            s.storage_path AS site_storage_path, s.thumb_path AS site_thumb_path,
            v.decision,
            COALESCE(pg.urls, ARRAY[]::text[]) AS pages
        FROM matches m
        JOIN reference_images r ON r.id = m.reference_id
        JOIN site_images s ON s.id = m.site_image_id
        LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
        LEFT JOIN LATERAL (
            SELECT array_agg(p.url ORDER BY p.url) AS urls
            FROM image_pages ip JOIN pages p ON p.id = ip.page_id
            WHERE ip.image_id = s.id
        ) pg ON true
        WHERE m.org_id = %s
        ORDER BY r.expiry_date NULLS LAST, m.score DESC
        """,
        (org_id,),
    ).fetchall()
    for row in rows:
        row["expiry_date"] = row["expiry_date"].isoformat() if row["expiry_date"] else None
        row["pages"] = list(row["pages"])
    return rows


def unmatched_rows(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    rows = conn.execute(
        """
        SELECT r.id AS reference_id, r.filename, r.expiry_date, r.credit, r.notes,
               r.storage_path AS ref_storage_path, r.thumb_path AS ref_thumb_path,
               r.compared_at IS NOT NULL AS compared
        FROM reference_images r
        WHERE r.org_id = %s AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.reference_id = r.id)
        ORDER BY r.expiry_date NULLS LAST
        """,
        (org_id,),
    ).fetchall()
    for row in rows:
        row["expiry_date"] = row["expiry_date"].isoformat() if row["expiry_date"] else None
    return rows


# --- crawl_runs -------------------------------------------------------------

def start_crawl_run(
    conn: psycopg.Connection, *, org_id: uuid.UUID, site_id: uuid.UUID,
    triggered_by: Optional[uuid.UUID], job_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    row = conn.execute(
        """INSERT INTO crawl_runs (org_id, site_id, status, triggered_by, job_id)
           VALUES (%s, %s, 'running', %s, %s) RETURNING id""",
        (org_id, site_id, triggered_by, job_id),
    ).fetchone()
    return row["id"]


def update_crawl_run_progress(conn: psycopg.Connection, run_id: uuid.UUID, stats) -> None:
    conn.execute(
        """UPDATE crawl_runs SET pages_visited = %s, images_found = %s, images_stored = %s,
               images_new = %s, blocked_by_robots = %s WHERE id = %s""",
        (stats.pages_visited, stats.images_found, stats.images_stored, stats.images_new,
         stats.blocked_by_robots, run_id),
    )


def finish_crawl_run(conn: psycopg.Connection, run_id: uuid.UUID, *, status: str, errors: list[str]) -> None:
    conn.execute(
        "UPDATE crawl_runs SET status = %s, finished_at = now(), errors = %s WHERE id = %s",
        (status, json.dumps(errors[:50]), run_id),
    )


def list_crawl_runs(conn: psycopg.Connection, org_id: uuid.UUID, limit: int = 20) -> list[Row]:
    return conn.execute(
        """SELECT c.*, s.url AS site_url FROM crawl_runs c JOIN sites s ON s.id = c.site_id
           WHERE c.org_id = %s ORDER BY c.started_at DESC LIMIT %s""",
        (org_id, limit),
    ).fetchall()


# --- reports ---------------------------------------------------------------

def create_report(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    within_days: int,
    storage_path_html: str,
    storage_path_csv: str,
    storage_path_not_found_csv: str,
    stats: dict,
    generated_by: Optional[uuid.UUID],
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO reports (org_id, within_days, storage_path_html, storage_path_csv,
                             storage_path_not_found_csv, stats, generated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (org_id, within_days, storage_path_html, storage_path_csv, storage_path_not_found_csv,
         json.dumps(stats, default=str), generated_by),
    ).fetchone()
    return row["id"]


def list_reports(conn: psycopg.Connection, org_id: uuid.UUID, limit: int = 50) -> list[Row]:
    return conn.execute(
        "SELECT * FROM reports WHERE org_id = %s ORDER BY generated_at DESC LIMIT %s", (org_id, limit)
    ).fetchall()


def get_report(conn: psycopg.Connection, org_id: uuid.UUID, report_id: uuid.UUID) -> Optional[Row]:
    return conn.execute("SELECT * FROM reports WHERE id = %s AND org_id = %s", (report_id, org_id)).fetchone()


# --- stats -------------------------------------------------------------

@dataclass(frozen=True)
class Stats:
    reference_images: int
    pages_crawled: int
    site_images: int
    matches: int


def get_stats(conn: psycopg.Connection, org_id: uuid.UUID) -> Stats:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM reference_images WHERE org_id = %(org)s) AS reference_images,
            (SELECT COUNT(*) FROM pages WHERE org_id = %(org)s AND status = 'done') AS pages_crawled,
            (SELECT COUNT(*) FROM site_images WHERE org_id = %(org)s) AS site_images,
            (SELECT COUNT(*) FROM matches WHERE org_id = %(org)s) AS matches
        """,
        {"org": org_id},
    ).fetchone()
    return Stats(**row)
