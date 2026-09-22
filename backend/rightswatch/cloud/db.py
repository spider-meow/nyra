"""Postgres persistence for the multi-tenant (Supabase) product.

Mirrors `rightswatch.db`'s shape (same kind of upsert-on-natural-key
functions, same tables) but targets the schema in `supabase/migrations/`:
uuid ids, an `org_id` on every row, and a few tables the local SQLite
product doesn't have (`organizations`, `memberships`, `sites`,
`crawl_runs`, `reports`) because they only make sense once there's more
than one tenant and job state needs to survive a server restart.

Every function here takes a connection (like `rightswatch.db`) rather
than opening its own — callers use `connect()` as a context manager.
The backend always connects with a privileged Postgres role (the
`DATABASE_URL` in `.env.example`), which bypasses Row Level Security by
virtue of being a normal table owner/superuser-ish role, not one of
Supabase's `anon`/`authenticated` API roles — so every write here is
implicitly trusted. Authorization (does this caller have the right role
in this org?) is `cloud.auth`'s job, checked before these functions are
ever called, not something RLS enforces for this connection. RLS (see
`supabase/migrations/0005`) is the safety net for anything that ever
queries Postgres a different way.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

Row = dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(database_url, row_factory=dict_row)
    register_vector(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping(database_url: str) -> bool:
    """Cheap connectivity check for /api/healthz."""
    with connect(database_url) as conn:
        conn.execute("SELECT 1")
    return True


# --- organizations / memberships -------------------------------------

def create_organization(conn: psycopg.Connection, *, name: str, slug: str) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO organizations (name, slug) VALUES (%s, %s) RETURNING id",
        (name, slug),
    ).fetchone()
    return row["id"]


def get_organization_by_slug(conn: psycopg.Connection, slug: str) -> Optional[Row]:
    return conn.execute("SELECT * FROM organizations WHERE slug = %s", (slug,)).fetchone()


def add_membership(conn: psycopg.Connection, *, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO memberships (user_id, org_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, org_id) DO UPDATE SET role = excluded.role
        RETURNING id
        """,
        (user_id, org_id, role),
    ).fetchone()
    return row["id"]


def get_membership(conn: psycopg.Connection, *, user_id: uuid.UUID, org_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM memberships WHERE user_id = %s AND org_id = %s",
        (user_id, org_id),
    ).fetchone()


def list_memberships_for_user(conn: psycopg.Connection, user_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT m.org_id, m.role, o.name AS org_name, o.slug AS org_slug
        FROM memberships m
        JOIN organizations o ON o.id = m.org_id
        WHERE m.user_id = %s
        ORDER BY o.name
        """,
        (user_id,),
    ).fetchall()


# --- sites --------------------------------------------------------------

def upsert_site(conn: psycopg.Connection, *, org_id: uuid.UUID, url: str, label: Optional[str] = None) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO sites (org_id, url, label)
        VALUES (%s, %s, %s)
        ON CONFLICT (org_id, url) DO UPDATE SET label = COALESCE(excluded.label, sites.label)
        RETURNING id
        """,
        (org_id, url, label),
    ).fetchone()
    return row["id"]


def get_site(conn: psycopg.Connection, *, org_id: uuid.UUID, site_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)
    ).fetchone()


def list_sites(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        "SELECT * FROM sites WHERE org_id = %s ORDER BY created_at DESC", (org_id,)
    ).fetchall()


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
    embedding=None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO reference_images
            (org_id, filename, storage_path, expiry_date, credit, notes,
             phash, dhash, embedding, width, height)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (org_id, filename) DO UPDATE SET
            storage_path=excluded.storage_path,
            expiry_date=excluded.expiry_date,
            credit=excluded.credit,
            notes=excluded.notes,
            phash=excluded.phash,
            dhash=excluded.dhash,
            embedding=excluded.embedding,
            width=excluded.width,
            height=excluded.height,
            compared_at=CASE
                WHEN reference_images.phash IS DISTINCT FROM excluded.phash
                  OR reference_images.dhash IS DISTINCT FROM excluded.dhash
                  OR reference_images.embedding IS DISTINCT FROM excluded.embedding
                THEN NULL
                ELSE reference_images.compared_at
            END
        RETURNING id
        """,
        (org_id, filename, storage_path, expiry_date, credit, notes, phash, dhash, embedding, width, height),
    ).fetchone()
    return row["id"]


def get_reference_images(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        "SELECT * FROM reference_images WHERE org_id = %s ORDER BY expiry_date NULLS LAST",
        (org_id,),
    ).fetchall()


def delete_reference_image(conn: psycopg.Connection, *, org_id: uuid.UUID, filename: str) -> None:
    conn.execute(
        "DELETE FROM reference_images WHERE org_id = %s AND filename = %s", (org_id, filename)
    )


# --- pages ----------------------------------------------------------------

def upsert_page(
    conn: psycopg.Connection, *, org_id: uuid.UUID, site_id: uuid.UUID, url: str, status: str = "pending"
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO pages (org_id, site_id, url, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (site_id, url) DO UPDATE SET url = excluded.url
        RETURNING id
        """,
        (org_id, site_id, url, status),
    ).fetchone()
    return row["id"]


def mark_page_crawled(conn: psycopg.Connection, page_id: uuid.UUID, *, http_status: int) -> None:
    conn.execute(
        "UPDATE pages SET status = 'done', http_status = %s, crawled_at = now() WHERE id = %s",
        (http_status, page_id),
    )


def get_crawled_urls(conn: psycopg.Connection, site_id: uuid.UUID) -> set[str]:
    rows = conn.execute(
        "SELECT url FROM pages WHERE site_id = %s AND status = 'done'", (site_id,)
    ).fetchall()
    return {r["url"] for r in rows}


# --- site_images ------------------------------------------------------

def upsert_site_image(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    url: str,
    storage_path: Optional[str] = None,
    content_hash: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    phash: Optional[str] = None,
    dhash: Optional[str] = None,
    embedding=None,
) -> tuple[uuid.UUID, bool]:
    """Returns (id, is_new) — `is_new` mirrors the local fetch.py's tracking
    of freshly-discovered vs. already-cached images for crawl stats."""
    row = conn.execute(
        """
        INSERT INTO site_images
            (org_id, site_id, url, storage_path, content_hash, width, height, phash, dhash, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (site_id, url) DO UPDATE SET
            storage_path=COALESCE(excluded.storage_path, site_images.storage_path),
            content_hash=COALESCE(excluded.content_hash, site_images.content_hash),
            width=COALESCE(excluded.width, site_images.width),
            height=COALESCE(excluded.height, site_images.height),
            phash=COALESCE(excluded.phash, site_images.phash),
            dhash=COALESCE(excluded.dhash, site_images.dhash),
            embedding=COALESCE(excluded.embedding, site_images.embedding),
            last_seen=now()
        RETURNING id, (xmax = 0) AS is_new
        """,
        (org_id, site_id, url, storage_path, content_hash, width, height, phash, dhash, embedding),
    ).fetchone()
    return row["id"], row["is_new"]


def get_site_images(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute("SELECT * FROM site_images WHERE org_id = %s", (org_id,)).fetchall()


def link_image_page(conn: psycopg.Connection, image_id: uuid.UUID, page_id: uuid.UUID) -> None:
    conn.execute(
        "INSERT INTO image_pages (image_id, page_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (image_id, page_id),
    )


def get_pages_for_image(conn: psycopg.Connection, image_id: uuid.UUID) -> list[str]:
    rows = conn.execute(
        """
        SELECT p.url FROM pages p
        JOIN image_pages ip ON ip.page_id = p.id
        WHERE ip.image_id = %s
        ORDER BY p.url
        """,
        (image_id,),
    ).fetchall()
    return [r["url"] for r in rows]


# --- matches (see rightswatch.match for the incremental-cache logic that
# calls these) -----------------------------------------------------------

def clear_matches(conn: psycopg.Connection, org_id: uuid.UUID) -> None:
    conn.execute("DELETE FROM matches WHERE org_id = %s", (org_id,))


def delete_matches_for(
    conn: psycopg.Connection, *, org_id: uuid.UUID, reference_ids: list[uuid.UUID], site_ids: list[uuid.UUID]
) -> None:
    if reference_ids:
        conn.execute(
            "DELETE FROM matches WHERE org_id = %s AND reference_id = ANY(%s)",
            (org_id, reference_ids),
        )
    if site_ids:
        conn.execute(
            "DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)",
            (org_id, site_ids),
        )


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
                level=excluded.level, score=excluded.score, confidence=excluded.confidence,
                created_at=now()
            """,
            [(org_id, *row) for row in rows],
        )


def stamp_compared(
    conn: psycopg.Connection, reference_ids: list[uuid.UUID], site_ids: list[uuid.UUID]
) -> None:
    if reference_ids:
        conn.execute(
            "UPDATE reference_images SET compared_at = now() WHERE id = ANY(%s)", (reference_ids,)
        )
    if site_ids:
        conn.execute("UPDATE site_images SET compared_at = now() WHERE id = ANY(%s)", (site_ids,))


def get_match_signature(conn: psycopg.Connection, org_id: uuid.UUID) -> Optional[str]:
    row = conn.execute("SELECT signature FROM match_meta WHERE org_id = %s", (org_id,)).fetchone()
    return None if row is None else row["signature"]


def set_match_signature(conn: psycopg.Connection, org_id: uuid.UUID, signature: str) -> None:
    conn.execute(
        """
        INSERT INTO match_meta (org_id, signature, finished_at)
        VALUES (%s, %s, now())
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
) -> None:
    if not site_image_ids:
        return
    if not decision:
        conn.execute(
            "DELETE FROM reviews WHERE reference_id = %s AND site_image_id = ANY(%s)",
            (reference_id, site_image_ids),
        )
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO reviews (reference_id, site_image_id, org_id, decision, reviewed_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (reference_id, site_image_id) DO UPDATE SET
                decision=excluded.decision, reviewed_by=excluded.reviewed_by, updated_at=excluded.updated_at
            """,
            [(reference_id, site_id, org_id, decision, reviewed_by) for site_id in site_image_ids],
        )


def get_matches(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT
            m.id AS match_id, m.level, m.score, m.confidence,
            r.id AS reference_id, r.filename, r.storage_path AS ref_storage_path,
            r.expiry_date, r.credit, r.notes,
            s.id AS site_image_id, s.url AS site_url, s.storage_path AS site_storage_path,
            s.content_hash AS content_hash,
            v.decision AS decision
        FROM matches m
        JOIN reference_images r ON r.id = m.reference_id
        JOIN site_images s ON s.id = m.site_image_id
        LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
        WHERE m.org_id = %s
        ORDER BY r.expiry_date NULLS LAST, m.score DESC
        """,
        (org_id,),
    ).fetchall()


def get_unmatched_references(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT r.id AS reference_id, r.filename, r.storage_path AS ref_storage_path,
               r.expiry_date, r.credit, r.notes, r.compared_at
        FROM reference_images r
        LEFT JOIN matches m ON m.reference_id = r.id
        WHERE r.org_id = %s AND m.id IS NULL
        ORDER BY r.expiry_date NULLS LAST
        """,
        (org_id,),
    ).fetchall()


# --- crawl_runs (persisted job state — survives a server restart, unlike
# the local product's in-memory JobRunner) -------------------------------

def start_crawl_run(
    conn: psycopg.Connection, *, org_id: uuid.UUID, site_id: uuid.UUID, triggered_by: Optional[uuid.UUID]
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO crawl_runs (org_id, site_id, status, triggered_by)
        VALUES (%s, %s, 'running', %s)
        RETURNING id
        """,
        (org_id, site_id, triggered_by),
    ).fetchone()
    return row["id"]


def update_crawl_run_progress(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    pages_visited: int,
    images_found: int,
    images_stored: int,
    images_new: int,
    blocked_by_robots: int,
) -> None:
    conn.execute(
        """
        UPDATE crawl_runs SET
            pages_visited = %s, images_found = %s, images_stored = %s,
            images_new = %s, blocked_by_robots = %s
        WHERE id = %s
        """,
        (pages_visited, images_found, images_stored, images_new, blocked_by_robots, run_id),
    )


def finish_crawl_run(conn: psycopg.Connection, run_id: uuid.UUID, *, status: str, errors: list[str]) -> None:
    import json

    conn.execute(
        "UPDATE crawl_runs SET status = %s, finished_at = now(), errors = %s WHERE id = %s",
        (status, json.dumps(errors), run_id),
    )


def get_crawl_run(conn: psycopg.Connection, org_id: uuid.UUID, run_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM crawl_runs WHERE id = %s AND org_id = %s", (run_id, org_id)
    ).fetchone()


def get_latest_crawl_run(conn: psycopg.Connection, org_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM crawl_runs WHERE org_id = %s ORDER BY started_at DESC LIMIT 1", (org_id,)
    ).fetchone()


# --- reports ---------------------------------------------------------

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
    import json

    row = conn.execute(
        """
        INSERT INTO reports
            (org_id, within_days, storage_path_html, storage_path_csv,
             storage_path_not_found_csv, stats, generated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (org_id, within_days, storage_path_html, storage_path_csv, storage_path_not_found_csv,
         json.dumps(stats), generated_by),
    ).fetchone()
    return row["id"]


def list_reports(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        "SELECT * FROM reports WHERE org_id = %s ORDER BY generated_at DESC", (org_id,)
    ).fetchall()


def get_report(conn: psycopg.Connection, org_id: uuid.UUID, report_id: uuid.UUID) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM reports WHERE id = %s AND org_id = %s", (report_id, org_id)
    ).fetchone()


# --- stats -------------------------------------------------------------

@dataclass(frozen=True)
class Stats:
    reference_images: int
    pages_crawled: int
    site_images: int
    matches: int


def get_stats(conn: psycopg.Connection, org_id: uuid.UUID) -> Stats:
    def count(table: str, extra: str = "") -> int:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE org_id = %s {extra}", (org_id,)).fetchone()
        return row["c"]

    return Stats(
        reference_images=count("reference_images"),
        pages_crawled=count("pages", "AND status = 'done'"),
        site_images=count("site_images"),
        matches=count("matches"),
    )
