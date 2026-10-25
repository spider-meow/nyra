"""Postgres persistence for the hosted (Supabase) product.

Targets the schema in `supabase/migrations/`: uuid ids and an `org_id` on
every row. An organization holds brands; a brand owns a library, its
sites and everything derived from them (see the brands migration). Every
function takes a connection; callers get one from
`connect()`, which hands out connections from a per-URL pool instead of
opening a new TCP + TLS session to Supabase for every request.

The backend connects with a privileged Postgres role (the `DATABASE_URL`
in `.env.example`), which bypasses Row Level Security. Authorization
(does this caller have the right role in this org?) is `cloud.auth`'s job,
checked before these functions are called, and every query here filters
on `org_id` itself, or on a `brand_id` the caller has already checked
belongs to the organization (`get_brand`). RLS (see the row_level_security migration) is the
safety net for anything that queries Postgres a different way.
"""

from __future__ import annotations

import atexit
import json
import os
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
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from nyra.crawl import BASELINE_READS, CrawlStats, read_health, suspect_reason

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
                # Serverless hosts run many small instances: keep each pool small
                # there (NYRA_DB_POOL_MAX=2) so they don't exhaust the pooler.
                min_size=0,
                max_size=int(os.environ.get("NYRA_DB_POOL_MAX", "10")),
                # A database that does not answer fails in seconds instead of hanging a thread (the worker's heartbeat) forever.
                kwargs={"row_factory": dict_row, "connect_timeout": 10, "keepalives_idle": 30, "keepalives_interval": 10,
                        "keepalives_count": 3},
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


def create_organization(conn: psycopg.Connection, *, name: str, slug: str, created_by: Optional[uuid.UUID] = None) -> uuid.UUID:
    """A new organization, with a first brand of the same name."""
    slug = slugify(slug)
    row = conn.execute("INSERT INTO organizations (name, slug) VALUES (%s, %s) RETURNING id", (name, slug)).fetchone()
    create_brand(conn, org_id=row["id"], name=name, slug=slug, created_by=created_by)
    return row["id"]


def organizations_overview(conn: psycopg.Connection) -> tuple[list[Row], list[Row]]:
    """(every organization with its member count, every brand with who created it): the back office's lists."""
    orgs = conn.execute(
        """
        SELECT o.id, o.name, o.slug, o.created_at, (SELECT count(*) FROM memberships m WHERE m.org_id = o.id) AS members
        FROM organizations o ORDER BY lower(o.name)
        """
    ).fetchall()
    brands = conn.execute(
        """
        SELECT b.id, b.org_id, b.name, b.slug, b.created_at, u.email AS created_by
        FROM brands b LEFT JOIN auth.users u ON u.id = b.created_by ORDER BY b.created_at DESC
        """
    ).fetchall()
    return orgs, brands


def users_overview(conn: psycopg.Connection) -> tuple[list[Row], list[Row], set[uuid.UUID]]:
    """(every account, every membership, the platform admins' ids). `to_jsonb` reads the last sign-in whatever the auth schema holds."""
    users = conn.execute(
        "SELECT u.id, u.email, (to_jsonb(u) ->> 'last_sign_in_at')::timestamptz AS last_sign_in_at FROM auth.users u ORDER BY lower(u.email)"
    ).fetchall()
    # The two e-mail switches are read through `to_jsonb` too (migration 35): before it is applied the page still works, with "yes".
    memberships = conn.execute(
        "SELECT m.user_id, m.org_id, m.role, m.created_at AS since, "
        "coalesce((to_jsonb(m) ->> 'monthly_email_allowed')::boolean, true) AS monthly_email_allowed, "
        "coalesce((to_jsonb(m) ->> 'monthly_email_wanted')::boolean, true) AS monthly_email_wanted, "
        "o.name AS org_name FROM memberships m JOIN organizations o ON o.id = m.org_id ORDER BY lower(o.name)"
    ).fetchall()
    staff = {row["user_id"] for row in conn.execute("SELECT user_id FROM platform_staff").fetchall()}
    return users, memberships, staff


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


def list_members(conn: psycopg.Connection, org_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT m.user_id, m.role, u.email
        FROM memberships m JOIN auth.users u ON u.id = m.user_id
        WHERE m.org_id = %s ORDER BY m.role, lower(u.email)
        """,
        (org_id,),
    ).fetchall()


def count_admins(conn: psycopg.Connection, org_id: uuid.UUID) -> int:
    return conn.execute("SELECT count(*) AS n FROM memberships WHERE org_id = %s AND role = 'admin'", (org_id,)).fetchone()["n"]


def set_member_role(conn: psycopg.Connection, *, org_id: uuid.UUID, user_id: uuid.UUID, role: str) -> bool:
    """False when that person is not a member."""
    return conn.execute("UPDATE memberships SET role = %s WHERE org_id = %s AND user_id = %s", (role, org_id, user_id)).rowcount == 1


def remove_member(conn: psycopg.Connection, *, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """False when that person is not a member."""
    return conn.execute("DELETE FROM memberships WHERE org_id = %s AND user_id = %s", (org_id, user_id)).rowcount == 1


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


# --- brands ---------------------------------------------------------------

def create_brand(conn: psycopg.Connection, *, org_id: uuid.UUID, name: str, slug: str,
                 created_by: Optional[uuid.UUID] = None) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO brands (org_id, name, slug, created_by) VALUES (%s, %s, %s, %s) RETURNING id",
        (org_id, name.strip(), slugify(slug), created_by),
    ).fetchone()
    return row["id"]


def get_brand(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID) -> Optional[Row]:
    return conn.execute("SELECT * FROM brands WHERE id = %s AND org_id = %s", (brand_id, org_id)).fetchone()


def list_brands(conn: psycopg.Connection, org_ids: list[uuid.UUID]) -> list[Row]:
    return conn.execute(
        """
        SELECT b.id, b.org_id, b.name, b.slug, b.created_at, l.data_uri AS logo
        FROM brands b LEFT JOIN brand_logos l ON l.brand_id = b.id
        WHERE b.org_id = ANY(%s) ORDER BY lower(b.name)
        """,
        (org_ids,),
    ).fetchall()


def set_brand_logo(conn: psycopg.Connection, brand_id: uuid.UUID, data_uri: str) -> None:
    conn.execute(
        """
        INSERT INTO brand_logos (brand_id, data_uri) VALUES (%s, %s)
        ON CONFLICT (brand_id) DO UPDATE SET data_uri = excluded.data_uri, updated_at = now()
        """,
        (brand_id, data_uri),
    )


def delete_brand_logo(conn: psycopg.Connection, brand_id: uuid.UUID) -> None:
    conn.execute("DELETE FROM brand_logos WHERE brand_id = %s", (brand_id,))


def update_brand(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, *, name: str, slug: str) -> bool:
    result = conn.execute(
        "UPDATE brands SET name = %s, slug = %s WHERE id = %s AND org_id = %s",
        (name.strip(), slugify(slug), brand_id, org_id),
    )
    return result.rowcount > 0


def _unused_ref_paths(conn: psycopg.Connection, org_id: uuid.UUID, paths: list[str]) -> list[str]:
    paths = sorted({path for path in paths if path})
    if not paths:
        return []
    used = conn.execute(
        """SELECT storage_path, thumb_path, work_path FROM reference_images
           WHERE org_id = %s AND (storage_path = ANY(%s) OR thumb_path = ANY(%s) OR work_path = ANY(%s))""",
        (org_id, paths, paths, paths),
    ).fetchall()
    taken = {row[key] for row in used for key in ("storage_path", "thumb_path", "work_path")}
    return [path for path in paths if path not in taken]


def _unused_site_paths(conn: psycopg.Connection, org_id: uuid.UUID, paths: list[str]) -> list[str]:
    """Crawled images are stored once per organization (keyed by content hash) and can be shared by
    several brands' sites: only the files no remaining row points at can go."""
    paths = sorted({path for path in paths if path})
    if not paths:
        return []
    used = conn.execute(
        """SELECT storage_path AS path FROM site_images WHERE org_id = %(org)s AND storage_path = ANY(%(p)s)
           UNION SELECT thumb_path FROM site_images WHERE org_id = %(org)s AND thumb_path = ANY(%(p)s)
           UNION SELECT thumb_path FROM excluded_hashes WHERE org_id = %(org)s AND thumb_path = ANY(%(p)s)""",
        {"org": org_id, "p": paths},
    ).fetchall()
    taken = {row["path"] for row in used}
    return [path for path in paths if path not in taken]


@dataclass(frozen=True)
class OrphanFiles:
    """Storage objects no row points at any more, per bucket."""
    refs: list[str]
    site_images: list[str]
    reports: list[str]


def delete_brand(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID) -> Optional[OrphanFiles]:
    """Delete a brand and everything that belongs to it (library, sites, pages, images, matches,
    reviews, exclusions, reports, jobs). Returns the files to remove from Storage, None if unknown.

    Nothing outside the brand is touched: a crawled image another brand's site also uses keeps its file.
    """
    refs = conn.execute(
        "SELECT storage_path, thumb_path, work_path FROM reference_images WHERE brand_id = %s", (brand_id,)
    ).fetchall()
    images = conn.execute(
        """SELECT si.storage_path, si.thumb_path FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s""",
        (brand_id,),
    ).fetchall()
    exclusions = conn.execute("SELECT thumb_path FROM excluded_hashes WHERE brand_id = %s", (brand_id,)).fetchall()
    reports = conn.execute(
        "SELECT storage_path_html, storage_path_csv, storage_path_not_found_csv FROM reports WHERE brand_id = %s",
        (brand_id,),
    ).fetchall()
    if not conn.execute("DELETE FROM brands WHERE id = %s AND org_id = %s", (brand_id, org_id)).rowcount:
        return None
    return OrphanFiles(
        refs=_unused_ref_paths(conn, org_id, [p for row in refs for p in (row["storage_path"], row["thumb_path"], row["work_path"])]),
        site_images=_unused_site_paths(
            conn, org_id,
            [p for row in images for p in (row["storage_path"], row["thumb_path"])]
            + [row["thumb_path"] for row in exclusions],
        ),
        reports=[path for row in reports for path in row.values() if path],
    )


# --- sites --------------------------------------------------------------

class SiteTaken(Exception):
    """This address already belongs to another brand of the organization."""


def create_site(
    conn: psycopg.Connection, *, org_id: uuid.UUID, brand_id: uuid.UUID, url: str, label: Optional[str] = None
) -> uuid.UUID:
    existing = conn.execute("SELECT id, brand_id FROM sites WHERE org_id = %s AND url = %s", (org_id, url)).fetchone()
    if existing is not None:
        if existing["brand_id"] != brand_id:
            raise SiteTaken(url)
        if label is not None:
            conn.execute("UPDATE sites SET label = %s WHERE id = %s", (label, existing["id"]))
        return existing["id"]
    row = conn.execute(
        "INSERT INTO sites (org_id, brand_id, url, label) VALUES (%s, %s, %s, %s) RETURNING id",
        (org_id, brand_id, url, label),
    ).fetchone()
    return row["id"]


def list_sites(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """
        SELECT s.id, s.url, s.label, s.created_at,
               (SELECT COUNT(*) FROM site_images si WHERE si.site_id = s.id) AS images,
               (SELECT max(c.started_at) FROM crawl_runs c WHERE c.site_id = s.id) AS last_crawled_at
        FROM sites s WHERE s.brand_id = %s ORDER BY s.created_at
        """,
        (brand_id,),
    ).fetchall()


def get_sites(conn: psycopg.Connection, brand_id: uuid.UUID, site_ids: list[uuid.UUID]) -> list[Row]:
    return conn.execute(
        "SELECT * FROM sites WHERE brand_id = %s AND id = ANY(%s) ORDER BY created_at", (brand_id, site_ids)
    ).fetchall()


def update_site_label(conn: psycopg.Connection, brand_id: uuid.UUID, site_id: uuid.UUID, label: Optional[str]) -> bool:
    result = conn.execute("UPDATE sites SET label = %s WHERE id = %s AND brand_id = %s", (label, site_id, brand_id))
    return result.rowcount > 0


def delete_site(
    conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, site_id: uuid.UUID
) -> Optional[list[str]]:
    """Delete a site with its pages, images and their matches. Returns the site-images files to
    remove from Storage, None if unknown."""
    images = conn.execute(
        """SELECT si.storage_path, si.thumb_path FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE si.site_id = %s AND s.brand_id = %s""",
        (site_id, brand_id),
    ).fetchall()
    if not conn.execute("DELETE FROM sites WHERE id = %s AND brand_id = %s", (site_id, brand_id)).rowcount:
        return None
    return _unused_site_paths(conn, org_id, [p for row in images for p in (row["storage_path"], row["thumb_path"])])


# --- reference_images ------------------------------------------------

def upsert_reference_image(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    brand_id: uuid.UUID,
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
    work_path: Optional[str] = None,
    byte_size: Optional[int] = None,
    unlimited_rights: bool = False,
) -> uuid.UUID:
    # A replaced picture is not the one that was searched for: forget what was found for the old one.
    conn.execute(
        """DELETE FROM reference_locations l USING reference_images r
           WHERE l.reference_id = r.id AND r.brand_id = %s AND r.filename = %s
             AND (r.phash IS DISTINCT FROM %s OR r.dhash IS DISTINCT FROM %s OR r.phash_flip IS DISTINCT FROM %s)""",
        (brand_id, filename, phash, dhash, phash_flip),
    )
    row = conn.execute(
        """
        INSERT INTO reference_images
            (org_id, brand_id, filename, storage_path, expiry_date, credit, notes,
             phash, dhash, phash_flip, dhash_flip, embedding, width, height, thumb_path, work_path, byte_size,
             unlimited_rights)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (brand_id, filename) DO UPDATE SET
            storage_path=excluded.storage_path,
            expiry_date=excluded.expiry_date,
            unlimited_rights=excluded.unlimited_rights,
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
            work_path=excluded.work_path,
            byte_size=COALESCE(excluded.byte_size, reference_images.byte_size),
            located_at=CASE
                WHEN reference_images.phash IS DISTINCT FROM excluded.phash
                  OR reference_images.dhash IS DISTINCT FROM excluded.dhash
                  OR reference_images.phash_flip IS DISTINCT FROM excluded.phash_flip
                THEN NULL
                ELSE reference_images.located_at
            END,
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
        (org_id, brand_id, filename, storage_path, expiry_date, credit, notes, phash, dhash, phash_flip, dhash_flip,
         embedding, width, height, thumb_path, work_path, byte_size, unlimited_rights),
    ).fetchone()
    return row["id"]


_REF_LIST_COLUMNS = """id, filename, storage_path, thumb_path, work_path, expiry_date, unlimited_rights, credit, notes, tags, collection_id, width,
    height, phash IS NOT NULL AS hashed, embedding IS NOT NULL AS embedded, compared_at, created_at, updated_at"""


def list_references(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """Library listing: no embeddings, no hashes."""
    return conn.execute(
        f"SELECT {_REF_LIST_COLUMNS} FROM reference_images WHERE brand_id = %s ORDER BY lower(filename)", (brand_id,)
    ).fetchall()


def list_collections(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    return conn.execute("SELECT id, parent_id, name FROM collections WHERE brand_id = %s ORDER BY lower(name)", (brand_id,)).fetchall()


def get_collection(conn: psycopg.Connection, brand_id: uuid.UUID, collection_id: uuid.UUID) -> Optional[Row]:
    return conn.execute("SELECT id, parent_id, name FROM collections WHERE brand_id = %s AND id = %s", (brand_id, collection_id)).fetchone()


def collection_depth(conn: psycopg.Connection, collection_id: uuid.UUID) -> int:
    """Levels from the top down to this collection (a top-level one is 1)."""
    return conn.execute(
        """WITH RECURSIVE up AS (SELECT id, parent_id, 1 AS depth FROM collections WHERE id = %s
               UNION ALL SELECT c.id, c.parent_id, up.depth + 1 FROM collections c JOIN up ON c.id = up.parent_id)
           SELECT max(depth) AS depth FROM up""", (collection_id,)).fetchone()["depth"]


def lock_collections(conn: psycopg.Connection, brand_id: uuid.UUID) -> None:
    """Until the end of the transaction, one change at a time to a brand's collections: two moves checked side by side could otherwise close a loop or pass three levels."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"collections:{brand_id}",))


def collection_subtree(conn: psycopg.Connection, collection_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """The collection and everything below it, each with its level below the collection (the collection itself is 1)."""
    rows = conn.execute(
        """WITH RECURSIVE down AS (SELECT id, 1 AS depth FROM collections WHERE id = %s
               UNION ALL SELECT c.id, down.depth + 1 FROM collections c JOIN down ON c.parent_id = down.id)
           SELECT id, depth FROM down""", (collection_id,)).fetchall()
    return {row["id"]: row["depth"] for row in rows}


def move_collection(conn: psycopg.Connection, brand_id: uuid.UUID, collection_id: uuid.UUID, parent_id: Optional[uuid.UUID]) -> bool:
    return conn.execute("UPDATE collections SET parent_id = %s WHERE brand_id = %s AND id = %s", (parent_id, brand_id, collection_id)).rowcount > 0


def add_collection(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, parent_id: Optional[uuid.UUID], name: str) -> Row:
    """Raises psycopg.errors.UniqueViolation when a sibling already has this name."""
    return conn.execute(
        "INSERT INTO collections (org_id, brand_id, parent_id, name) VALUES (%s, %s, %s, %s) RETURNING id, parent_id, name",
        (org_id, brand_id, parent_id, name),
    ).fetchone()


def rename_collection(conn: psycopg.Connection, brand_id: uuid.UUID, collection_id: uuid.UUID, name: str) -> bool:
    return conn.execute("UPDATE collections SET name = %s WHERE brand_id = %s AND id = %s", (name, brand_id, collection_id)).rowcount > 0


def delete_collection(conn: psycopg.Connection, brand_id: uuid.UUID, collection_id: uuid.UUID) -> bool:
    return conn.execute("DELETE FROM collections WHERE brand_id = %s AND id = %s", (brand_id, collection_id)).rowcount > 0


def set_reference_collection(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str], collection_id: Optional[uuid.UUID]) -> int:
    return conn.execute(
        "UPDATE reference_images SET collection_id = %s WHERE brand_id = %s AND filename = ANY(%s)", (collection_id, brand_id, filenames)
    ).rowcount


def reference_paths(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str]) -> list[Row]:
    """Where the images of the named references are stored (for their thumbnails): no other column."""
    return conn.execute(
        "SELECT filename, storage_path, thumb_path, work_path FROM reference_images"
        " WHERE brand_id = %s AND filename = ANY(%s)", (brand_id, filenames)
    ).fetchall()


def get_reference_by_filename(conn: psycopg.Connection, brand_id: uuid.UUID, filename: str) -> Optional[Row]:
    return conn.execute(
        "SELECT * FROM reference_images WHERE brand_id = %s AND filename = %s", (brand_id, filename)
    ).fetchone()


def existing_filenames(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str]) -> set[str]:
    rows = conn.execute(
        "SELECT filename FROM reference_images WHERE brand_id = %s AND filename = ANY(%s)", (brand_id, filenames)
    ).fetchall()
    return {row["filename"] for row in rows}


def update_reference_meta(
    conn: psycopg.Connection, brand_id: uuid.UUID, filename: str, *,
    expiry_date: Optional[str], credit: Optional[str], notes: Optional[str],
    tags: Optional[list[str]] = None, unlimited_rights: bool = False,
) -> Optional[uuid.UUID]:
    """The reference's id, None if there is none of that name.
    `tags=None` leaves the tags as they are (a CSV without a tags column must not wipe them).
    Unlimited rights and an expiry date exclude each other (the caller passes no date with them)."""
    row = conn.execute(
        """UPDATE reference_images
           SET expiry_date = %s, unlimited_rights = %s, credit = %s, notes = %s, tags = COALESCE(%s::text[], tags)
           WHERE brand_id = %s AND filename = %s RETURNING id""",
        (expiry_date, unlimited_rights, credit, notes, tags, brand_id, filename),
    ).fetchone()
    return row["id"] if row else None


def get_reference_tags(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str]) -> dict[str, list[str]]:
    rows = conn.execute(
        # FOR UPDATE: two bulk edits of the same references wait for each other instead of losing one.
        "SELECT filename, tags FROM reference_images WHERE brand_id = %s AND filename = ANY(%s) FOR UPDATE",
        (brand_id, filenames),
    ).fetchall()
    return {row["filename"]: list(row["tags"]) for row in rows}


def set_reference_tags(conn: psycopg.Connection, brand_id: uuid.UUID, tags_by_filename: dict[str, list[str]]) -> int:
    """Replace the tags of each named reference in one statement; returns how many rows were written."""
    if not tags_by_filename:
        return 0
    rows = [{"filename": filename, "tags": tags} for filename, tags in tags_by_filename.items()]
    return conn.execute(
        """UPDATE reference_images r SET tags = ARRAY(SELECT jsonb_array_elements_text(v.tags))
           FROM jsonb_to_recordset(%s) AS v(filename text, tags jsonb)
           WHERE r.brand_id = %s AND r.filename = v.filename""",
        (Jsonb(rows), brand_id),
    ).rowcount


def set_expiry_for(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str], expiry_date: Optional[str],
                   unlimited_rights: bool = False) -> int:
    result = conn.execute(
        "UPDATE reference_images SET expiry_date = %s, unlimited_rights = %s WHERE brand_id = %s AND filename = ANY(%s)",
        (expiry_date, unlimited_rights, brand_id, filenames),
    )
    return result.rowcount


def delete_references(conn: psycopg.Connection, brand_id: uuid.UUID, filenames: list[str]) -> list[Row]:
    """Delete and return the deleted rows' storage paths, for cleanup in Storage."""
    return conn.execute(
        """DELETE FROM reference_images WHERE brand_id = %s AND filename = ANY(%s)
           RETURNING id, filename, storage_path, thumb_path, work_path""",
        (brand_id, filenames),
    ).fetchall()


# --- site images (see cloud.store for the crawler-facing writes) --------

def link_image_page(conn: psycopg.Connection, image_id: uuid.UUID, page_id: uuid.UUID) -> None:
    conn.execute(
        """INSERT INTO image_pages (image_id, page_id, last_seen) VALUES (%s, %s, now())
           ON CONFLICT (image_id, page_id) DO UPDATE SET last_seen = now()""",
        (image_id, page_id),
    )


def get_pages_for_image(conn: psycopg.Connection, image_id: uuid.UUID) -> list[str]:
    rows = conn.execute(
        """SELECT p.url FROM pages p JOIN image_pages ip ON ip.page_id = p.id
           WHERE ip.image_id = %s ORDER BY p.url""",
        (image_id,),
    ).fetchall()
    return [r["url"] for r in rows]


# --- matches ---------------------------------------------------------------

def clear_matches(conn: psycopg.Connection, brand_id: uuid.UUID) -> None:
    conn.execute(
        "DELETE FROM matches m USING reference_images r WHERE r.id = m.reference_id AND r.brand_id = %s", (brand_id,)
    )


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


def count_matches(conn: psycopg.Connection, brand_id: uuid.UUID) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM matches m JOIN reference_images r ON r.id = m.reference_id WHERE r.brand_id = %s",
        (brand_id,),
    ).fetchone()["c"]


def stamp_compared(conn: psycopg.Connection, reference_ids: list[uuid.UUID], site_ids: list[uuid.UUID]) -> None:
    if reference_ids:
        conn.execute("UPDATE reference_images SET compared_at = now() WHERE id = ANY(%s)", (reference_ids,))
    if site_ids:
        conn.execute("UPDATE site_images SET compared_at = now() WHERE id = ANY(%s)", (site_ids,))


def get_match_signature(conn: psycopg.Connection, brand_id: uuid.UUID) -> Optional[str]:
    row = conn.execute("SELECT signature FROM match_meta WHERE brand_id = %s", (brand_id,)).fetchone()
    return None if row is None else row["signature"]


def report_provenance(conn: psycopg.Connection, brand_id: uuid.UUID) -> dict:
    """What a report states about its matches: the stored signature of the last pass and the finished reads of the sites (the last of each site: oldest and newest)."""
    row = conn.execute(
        """SELECT min(last) AS oldest, max(last) AS newest FROM
             (SELECT max(c.started_at) AS last FROM crawl_runs c JOIN sites s ON s.id = c.site_id
               WHERE s.brand_id = %s AND c.status = 'done' GROUP BY c.site_id) per_site""", (brand_id,)).fetchone()
    return {"signature": get_match_signature(conn, brand_id), "read_at": (row["oldest"], row["newest"])}


def set_match_signature(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, signature: str) -> None:
    conn.execute(
        """
        INSERT INTO match_meta (org_id, brand_id, signature, finished_at) VALUES (%s, %s, %s, now())
        ON CONFLICT (brand_id) DO UPDATE SET signature = excluded.signature, finished_at = excluded.finished_at
        """,
        (org_id, brand_id, signature),
    )


def set_reviews(
    conn: psycopg.Connection,
    *,
    brand_id: uuid.UUID,
    reference_id: uuid.UUID,
    site_image_ids: list[uuid.UUID],
    decision: str,
    reviewed_by: Optional[uuid.UUID],
) -> int:
    """Record a decision on matches of this brand. Returns rows touched.

    Pairs are checked against this brand's `matches`, so a caller can't
    attach a review to another brand's (or organization's) images.
    """
    if not site_image_ids:
        return 0
    if not decision:
        result = conn.execute(
            """DELETE FROM reviews v USING reference_images r
               WHERE r.id = v.reference_id AND r.brand_id = %s
                 AND v.reference_id = %s AND v.site_image_id = ANY(%s)""",
            (brand_id, reference_id, site_image_ids),
        )
        return result.rowcount
    result = conn.execute(
        """
        INSERT INTO reviews (reference_id, site_image_id, org_id, decision, reviewed_by, updated_at)
        SELECT m.reference_id, m.site_image_id, m.org_id, %s, %s, now()
        FROM matches m JOIN reference_images r ON r.id = m.reference_id
        WHERE r.brand_id = %s AND m.reference_id = %s AND m.site_image_id = ANY(%s)
        ON CONFLICT (reference_id, site_image_id) DO UPDATE SET
            decision=excluded.decision, reviewed_by=excluded.reviewed_by, updated_at=excluded.updated_at
        """,
        (decision, reviewed_by, brand_id, reference_id, site_image_ids),
    )
    return result.rowcount


def match_rows(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """Every match of the brand with its reference, image, decision and pages, in one query."""
    rows = conn.execute(
        """
        SELECT
            m.level, m.score, m.confidence,
            r.id AS reference_id, r.filename, r.expiry_date, r.unlimited_rights, r.credit, r.notes,
            r.storage_path AS ref_storage_path, r.thumb_path AS ref_thumb_path, r.work_path AS ref_work_path,
            s.id AS site_image_id, s.url AS site_url, s.content_hash, s.variant_group, COALESCE(st.label, '') AS site_label,
            s.storage_path AS site_storage_path, s.thumb_path AS site_thumb_path,
            v.decision,
            -- What the last finished read of the image's site says about a removal (`report._removal`).
            COALESCE(rd.read_at > v.updated_at, false) AS read_after,
            COALESCE(s.last_seen >= rd.read_at, false) AS seen,
            COALESCE(rd.suspect, false) AS suspect_read,
            COALESCE(pg.unread, 0) AS unread_pages,
            COALESCE(pg.urls, ARRAY[]::text[]) AS pages
        FROM matches m
        JOIN reference_images r ON r.id = m.reference_id
        JOIN site_images s ON s.id = m.site_image_id
        JOIN sites st ON st.id = s.site_id
        LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
        LEFT JOIN LATERAL (
            SELECT c.started_at AS read_at, c.suspect FROM crawl_runs c
            WHERE c.site_id = s.site_id AND c.status = 'done' ORDER BY c.started_at DESC LIMIT 1
        ) rd ON true
        LEFT JOIN LATERAL (
            SELECT array_agg(p.url ORDER BY p.url) AS urls,
                   -- a page that failed to load (or was never read) says nothing about what it shows
                   count(*) FILTER (WHERE p.crawled_at IS NULL OR p.crawled_at < rd.read_at
                                      OR p.http_status IS NULL OR p.http_status NOT BETWEEN 200 AND 299
                                      OR """ + interrupted_sql("s.site_id", "rd.read_at") + """) AS unread
            FROM image_pages ip JOIN pages p ON p.id = ip.page_id
            WHERE ip.image_id = s.id
        ) pg ON true
        WHERE r.brand_id = %s
        ORDER BY r.expiry_date NULLS LAST, m.score DESC
        """,
        (brand_id,),
    ).fetchall()
    for row in rows:
        row["expiry_date"] = row["expiry_date"].isoformat() if row["expiry_date"] else None
        row["pages"] = list(row["pages"])
        row["variant_group"] = str(row["variant_group"]) if row["variant_group"] else None
    return rows


def unmatched_rows(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    rows = conn.execute(
        """
        SELECT r.id AS reference_id, r.filename, r.expiry_date, r.unlimited_rights, r.credit, r.notes,
               r.storage_path AS ref_storage_path, r.thumb_path AS ref_thumb_path,
               r.compared_at IS NOT NULL AS compared
        FROM reference_images r
        WHERE r.brand_id = %s AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.reference_id = r.id)
        ORDER BY r.expiry_date NULLS LAST
        """,
        (brand_id,),
    ).fetchall()
    for row in rows:
        row["expiry_date"] = row["expiry_date"].isoformat() if row["expiry_date"] else None
    return rows


# The images of one reference: the ones the `locate` job found and the ones the comparison matched (a high
# confidence is "same", the rest "review"), minus those a person set aside. One row per image, best tier.
_REFERENCE_HITS = """
    SELECT h.site_image_id, CASE WHEN bool_or(h.tier = 'same') THEN 'same' ELSE 'review' END AS tier
    FROM (
        SELECT l.site_image_id, l.tier FROM reference_locations l WHERE l.reference_id = %(ref)s
        UNION ALL
        SELECT m.site_image_id, CASE WHEN m.confidence = 'haut' THEN 'same' ELSE 'review' END
        FROM matches m WHERE m.reference_id = %(ref)s
    ) h
    WHERE NOT EXISTS (SELECT 1 FROM reviews v WHERE v.reference_id = %(ref)s AND v.site_image_id = h.site_image_id
                      AND v.decision = 'ecarte')
    GROUP BY h.site_image_id"""


# Images kept per page (a page showing more crops of one picture keeps its count, not the rest).
OCCURRENCE_IMAGES_PER_PAGE = 20


def occurrence_pages(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID, *,
                     limit: int, offset: int, tier: Optional[str] = None) -> list[Row]:
    """The pages of the brand's sites that show the reference (or a crop of it), `limit` of them from `offset`, by
    address; only the images of `tier` ("same" or "review") when given. Each has `image_count` and its first `OCCURRENCE_IMAGES_PER_PAGE` images (`site_image_id`, `url`,
    `tier`, `thumb_path`, `storage_path`)."""
    return conn.execute(
        f"""
        SELECT page_url, max(n) AS image_count,
               jsonb_agg(jsonb_build_object('site_image_id', id, 'url', url, 'tier', tier,
                                            'thumb_path', thumb_path, 'storage_path', storage_path)
                         ORDER BY rn) FILTER (WHERE rn <= %(per_page)s) AS images
        FROM (
            SELECT p.id AS page_id, p.url AS page_url, si.id, si.url, h.tier, si.thumb_path, si.storage_path,
                   row_number() OVER (PARTITION BY p.id ORDER BY si.url, si.id) AS rn,
                   count(*) OVER (PARTITION BY p.id) AS n
            FROM ({_REFERENCE_HITS}) h
            JOIN site_images si ON si.id = h.site_image_id
            JOIN sites s ON s.id = si.site_id AND s.brand_id = %(brand)s
            JOIN image_pages ip ON ip.image_id = si.id
            JOIN pages p ON p.id = ip.page_id
            WHERE (%(tier)s::text IS NULL OR h.tier = %(tier)s)
        ) found
        GROUP BY page_id, page_url ORDER BY page_url LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"ref": reference_id, "brand": brand_id, "limit": limit, "offset": offset, "per_page": OCCURRENCE_IMAGES_PER_PAGE,
         "tier": tier},
    ).fetchall()


def occurrence_summary(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID, *,
                       tier: Optional[str] = None) -> Row:
    """What the occurrences stand on: images and pages found, when the reference was last searched for, the last read of
    the brand's sites and the pages read, so "nothing found" is not taken for "not there". `images` and `pages` follow
    `tier` when given; `review_images` is always the number of images for a person to check."""
    return conn.execute(
        f"""
        SELECT (SELECT count(*) FROM ({_REFERENCE_HITS}) h JOIN site_images si ON si.id = h.site_image_id
                 JOIN sites s ON s.id = si.site_id AND s.brand_id = %(brand)s
                 WHERE (%(tier)s::text IS NULL OR h.tier = %(tier)s)) AS images,
               (SELECT count(DISTINCT ip.page_id) FROM ({_REFERENCE_HITS}) h JOIN site_images si ON si.id = h.site_image_id
                 JOIN sites s ON s.id = si.site_id AND s.brand_id = %(brand)s
                 JOIN image_pages ip ON ip.image_id = si.id
                 WHERE (%(tier)s::text IS NULL OR h.tier = %(tier)s)) AS pages,
               (SELECT count(*) FROM ({_REFERENCE_HITS}) h JOIN site_images si ON si.id = h.site_image_id
                 JOIN sites s ON s.id = si.site_id AND s.brand_id = %(brand)s WHERE h.tier = 'review') AS review_images,
               (SELECT located_at FROM reference_images WHERE id = %(ref)s AND brand_id = %(brand)s) AS located_at,
               (SELECT max(c.started_at) FROM crawl_runs c JOIN sites s ON s.id = c.site_id
                 WHERE s.brand_id = %(brand)s) AS last_crawled_at,
               (SELECT count(*) FROM pages p JOIN sites s ON s.id = p.site_id
                 WHERE s.brand_id = %(brand)s AND p.status = 'done') AS pages_crawled
        """,
        {"ref": reference_id, "brand": brand_id, "tier": tier},
    ).fetchone()


# Pages listed per image: a logo is on every page of a site, and a screen only shows a few.
SITE_IMAGE_PAGE_SAMPLE = 20


def site_images_with_pages(conn: psycopg.Connection, brand_id: uuid.UUID, *, matched: Optional[bool] = None) -> list[Row]:
    """The images read on the brand's sites (the ones that were stored), with their pages, newest first: every one, or only
    those where a visual of the library was found (`matched=True`) or was not (`False`).

    The same bytes found under several URLs come back once per URL; callers
    group them by `variant_group` (crops of one photo), else `content_hash`.
    Each row carries the first `SITE_IMAGE_PAGE_SAMPLE` pages (`pages`) and the exact number of pages
    (`page_count`): the database sends the sample, not the thousands of addresses behind a logo.
    The `matched` key of a row says whether a visual of the library was found in it; `logo_score` / `logo_kept`: see `cloud/logos.py`.
    """
    rows = conn.execute(
        """
        SELECT si.id, si.url, si.content_hash, si.storage_path, si.thumb_path, si.width, si.height,
               si.phash, si.dhash, si.variant_group, si.compared_at IS NOT NULL AS compared, si.first_seen, si.last_seen,
               si.logo_score, si.logo_kept, EXISTS (SELECT 1 FROM matches m WHERE m.site_image_id = si.id) AS matched,
               s.id AS site_id, s.url AS site_url, s.label AS site_label,
               COALESCE(pg.urls, ARRAY[]::text[]) AS pages, pg.n AS page_count
        FROM site_images si
        JOIN sites s ON s.id = si.site_id
        LEFT JOIN LATERAL (
            SELECT (array_agg(p.url ORDER BY p.url))[1:%(sample)s] AS urls, count(*) AS n
            FROM image_pages ip JOIN pages p ON p.id = ip.page_id
            WHERE ip.image_id = si.id
        ) pg ON true
        WHERE s.brand_id = %(brand)s AND si.phash IS NOT NULL AND si.storage_path IS NOT NULL
          AND (%(matched)s::boolean IS NULL
               OR EXISTS (SELECT 1 FROM matches m WHERE m.site_image_id = si.id) = %(matched)s::boolean)
        ORDER BY si.first_seen DESC, si.url
        """,
        {"sample": SITE_IMAGE_PAGE_SAMPLE, "brand": brand_id, "matched": matched},
    ).fetchall()
    for row in rows:
        row["pages"] = list(row["pages"])
    return rows


def unmatched_site_images(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """The images of the brand's sites that match nothing in its library, with their pages."""
    return site_images_with_pages(conn, brand_id, matched=False)


def unmatched_site_image_keys(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """The same images as `unmatched_site_images`, in the same order, with only what counting them takes:
    no address, no path, no pages (a tenth of the weight)."""
    return conn.execute(
        """
        SELECT si.id, si.content_hash, si.variant_group, si.width, si.height, si.phash, si.dhash
        FROM site_images si
        JOIN sites s ON s.id = si.site_id
        WHERE s.brand_id = %s AND si.phash IS NOT NULL AND si.storage_path IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.site_image_id = si.id)
        ORDER BY si.first_seen DESC, si.url
        """,
        (brand_id,),
    ).fetchall()


def unreferenced_signature(conn: psycopg.Connection, brand_id: uuid.UUID) -> str:
    """A short fingerprint that changes whenever the set of unreferenced images does: an image read, matched,
    regrouped or excluded. Computed inside the database, so only a few dozen characters come out."""
    row = conn.execute(
        """
        SELECT
            (SELECT md5(COALESCE(string_agg(
                        si.id::text || ':' || COALESCE(si.content_hash, '') || ':' || COALESCE(si.variant_group::text, '') || ':'
                        || COALESCE(si.phash, '') || ':' || COALESCE(si.dhash, '') || ':'
                        || EXISTS (SELECT 1 FROM matches m WHERE m.site_image_id = si.id)::text, ',' ORDER BY si.id), ''))
               FROM site_images si JOIN sites s ON s.id = si.site_id
              WHERE s.brand_id = %(brand)s AND si.phash IS NOT NULL AND si.storage_path IS NOT NULL) AS images,
            (SELECT md5(COALESCE(string_agg(e.hash || ':' || e.hash_type, ',' ORDER BY e.hash, e.hash_type), ''))
               FROM excluded_hashes e WHERE e.brand_id = %(brand)s) AS exclusions
        """,
        {"brand": brand_id},
    ).fetchone()
    return f"{row['images']}{row['exclusions']}"


def site_images_by_id(conn: psycopg.Connection, brand_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Row]:
    return conn.execute(
        """SELECT si.* FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.id = ANY(%s)""",
        (brand_id, ids),
    ).fetchall()


def same_image_ids(conn: psycopg.Connection, brand_id: uuid.UUID, content_hash: Optional[str], image_id: uuid.UUID) -> list[uuid.UUID]:
    """This image and every other URL of the brand's sites serving the same bytes."""
    rows = conn.execute(
        """SELECT si.id FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND (si.id = %s OR (%s::text IS NOT NULL AND si.content_hash = %s))""",
        (brand_id, image_id, content_hash, content_hash),
    ).fetchall()
    return [row["id"] for row in rows]


# --- exclusions -----------------------------------------------------------

def add_exclusion(
    conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, site_image_id: uuid.UUID, *,
    reason: Optional[str], created_by: Optional[uuid.UUID],
) -> Optional[uuid.UUID]:
    """Exclude an image of this brand's sites by its hashes. Returns the exclusion's group id,
    None if the image is unknown."""
    image = conn.execute(
        """SELECT si.phash, si.dhash, si.thumb_path, si.url FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE si.id = %s AND s.brand_id = %s""",
        (site_image_id, brand_id),
    ).fetchone()
    if image is None or not (image["phash"] or image["dhash"]):
        return None
    group_id = uuid.uuid4()
    for kind in ("phash", "dhash"):
        if image[kind]:
            conn.execute(
                """INSERT INTO excluded_hashes
                       (org_id, brand_id, hash, hash_type, reason, group_id, thumb_path, site_url, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (brand_id, hash, hash_type) DO UPDATE SET reason = excluded.reason,
                       group_id = excluded.group_id, thumb_path = excluded.thumb_path, site_url = excluded.site_url""",
                (org_id, brand_id, image[kind], kind, reason, group_id, image["thumb_path"], image["url"], created_by),
            )
    return group_id


def list_exclusions(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """SELECT COALESCE(group_id, id) AS id, max(reason) AS reason, max(thumb_path) AS thumb_path,
                  max(site_url) AS site_url, min(created_at) AS created_at
           FROM excluded_hashes WHERE brand_id = %s
           GROUP BY COALESCE(group_id, id) ORDER BY min(created_at) DESC""",
        (brand_id,),
    ).fetchall()


def delete_exclusion(conn: psycopg.Connection, brand_id: uuid.UUID, exclusion_id: uuid.UUID) -> int:
    result = conn.execute(
        "DELETE FROM excluded_hashes WHERE brand_id = %s AND (group_id = %s OR id = %s)",
        (brand_id, exclusion_id, exclusion_id),
    )
    return result.rowcount


def site_hashes(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    return conn.execute(
        """SELECT si.id, si.phash, si.dhash FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s""",
        (brand_id,),
    ).fetchall()


def load_exclusions(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT hash, hash_type FROM excluded_hashes WHERE brand_id = %s", (brand_id,)).fetchall()
    return [(row["hash"], row["hash_type"]) for row in rows]


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
               images_new = %s, blocked_by_robots = %s, metrics = %s WHERE id = %s""",
        (stats.pages_visited, stats.images_found, stats.images_stored, stats.images_new,
         stats.blocked_by_robots, json.dumps(stats.metrics()), run_id),
    )


def interrupted_sql(site: str, read_at: str) -> str:
    """SQL condition on a page `p`: a run of `site` that did not end properly (running, cancelled, error) began after the
    last finished read (`read_at`) and was going while `p` was read. Such a page was read by a run that was never judged,
    so it cannot be taken for gone. A page checked alone after a clean read has no such run."""
    return f"""EXISTS (SELECT 1 FROM crawl_runs i WHERE i.site_id = {site} AND i.status <> 'done' AND i.started_at > {read_at}
                      AND i.started_at <= p.crawled_at AND (i.finished_at IS NULL OR p.crawled_at <= i.finished_at))"""


def record_read_health(conn: psycopg.Connection, run_id: uuid.UUID, stats: CrawlStats) -> Optional[str]:
    """Work out the health of a read that ended (`crawl.read_health`), compare it with the best of the site's last
    reads (`crawl.suspect_reason`), store both on the run and return why it is suspect, or None. Call it before the run
    is marked done, so no one reads a finished run that was not judged. (`nyra.crawl` is already imported by the worker
    and the routes: the pure functions stay beside the crawler they measure.)"""
    health = read_health(stats)
    row = conn.execute(
        """SELECT r.started_at, r.site_id,
                  count(s.id) FILTER (WHERE s.last_seen >= r.started_at) AS seen,
                  count(s.id) FILTER (WHERE s.last_seen >= r.started_at AND s.first_seen < r.started_at) AS known_seen
           FROM crawl_runs r LEFT JOIN site_images s ON s.site_id = r.site_id WHERE r.id = %s GROUP BY r.id""",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"crawl run {run_id} not found")
    # The best of the last reads, not the last one alone: a broken read followed by another broken one is still caught.
    # A read with no page read (an empty resume) says nothing about the site: it does not count.
    baseline = conn.execute(
        """SELECT max(images_known_seen) AS seen FROM (SELECT images_known_seen FROM crawl_runs
               WHERE site_id = %s AND status = 'done' AND pages_ok > 0 AND started_at < %s ORDER BY started_at DESC LIMIT %s) last""",
        (row["site_id"], row["started_at"], BASELINE_READS),
    ).fetchone()["seen"]
    reason = suspect_reason(health, row["known_seen"], baseline)
    conn.execute(
        """UPDATE crawl_runs SET pages_ok = %s, error_share = %s, images_seen = %s,
               images_known_seen = %s, suspect = %s, suspect_reason = %s WHERE id = %s""",
        (health["pages_ok"], health["error_share"], row["seen"], row["known_seen"],
         reason is not None, reason, run_id),
    )
    return reason


def finish_crawl_run(conn: psycopg.Connection, run_id: uuid.UUID, *, status: str, errors: list[str]) -> None:
    conn.execute(
        "UPDATE crawl_runs SET status = %s, finished_at = now(), errors = %s WHERE id = %s",
        (status, json.dumps(errors[:50]), run_id),
    )


def crawl_history(conn: psycopg.Connection, site_ids: list[uuid.UUID], per_site: int = 5) -> list[Row]:
    """The last finished reads of each site, newest first (the figures `nyra.estimate` works from)."""
    return conn.execute(
        """SELECT site_id, pages_visited, seconds FROM (
               SELECT site_id, pages_visited, started_at,
                      COALESCE(NULLIF((metrics ->> 'duration_seconds')::float8, 0),
                               EXTRACT(epoch FROM finished_at - started_at)) AS seconds,
                      row_number() OVER (PARTITION BY site_id ORDER BY started_at DESC) AS rank
               FROM crawl_runs WHERE site_id = ANY(%s) AND status = 'done') last
           WHERE rank <= %s ORDER BY site_id, started_at DESC""",
        (site_ids, per_site),
    ).fetchall()


def match_history(conn: psycopg.Connection, brand_id: uuid.UUID, limit: int = 5) -> list[float]:
    """How long the brand's last comparisons took, in seconds."""
    rows = conn.execute(
        """SELECT (result -> 'match_metrics' ->> 'seconds')::float8 AS seconds FROM jobs
           WHERE brand_id = %s AND status = 'done' AND (result -> 'match_metrics' ->> 'seconds') IS NOT NULL
           ORDER BY finished_at DESC LIMIT %s""",
        (brand_id, limit),
    ).fetchall()
    return [row["seconds"] for row in rows if row["seconds"]]


def list_crawl_runs(conn: psycopg.Connection, brand_id: uuid.UUID, limit: int = 20) -> list[Row]:
    return conn.execute(
        """SELECT c.*, s.url AS site_url, s.label AS site_label FROM crawl_runs c JOIN sites s ON s.id = c.site_id
           WHERE s.brand_id = %s ORDER BY c.started_at DESC LIMIT %s""",
        (brand_id, limit),
    ).fetchall()


# --- reports ---------------------------------------------------------------

def create_report(
    conn: psycopg.Connection,
    *,
    org_id: uuid.UUID,
    brand_id: uuid.UUID,
    within_days: int,
    storage_path_html: str,
    storage_path_csv: str,
    storage_path_not_found_csv: str,
    stats: dict,
    generated_by: Optional[uuid.UUID],
) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO reports (org_id, brand_id, within_days, storage_path_html, storage_path_csv,
                             storage_path_not_found_csv, stats, generated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (org_id, brand_id, within_days, storage_path_html, storage_path_csv, storage_path_not_found_csv,
         json.dumps(stats, default=str), generated_by),
    ).fetchone()
    return row["id"]


def list_reports(conn: psycopg.Connection, brand_id: uuid.UUID, limit: int = 50) -> list[Row]:
    return conn.execute(
        "SELECT * FROM reports WHERE brand_id = %s ORDER BY generated_at DESC LIMIT %s", (brand_id, limit)
    ).fetchall()


def get_report(conn: psycopg.Connection, brand_id: uuid.UUID, report_id: uuid.UUID) -> Optional[Row]:
    return conn.execute("SELECT * FROM reports WHERE id = %s AND brand_id = %s", (report_id, brand_id)).fetchone()


# --- stats -------------------------------------------------------------

@dataclass(frozen=True)
class Stats:
    reference_images: int
    pages_crawled: int
    site_images: int
    matches: int


def get_stats(conn: psycopg.Connection, brand_id: uuid.UUID) -> Stats:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM reference_images WHERE brand_id = %(brand)s) AS reference_images,
            (SELECT COUNT(*) FROM pages p JOIN sites s ON s.id = p.site_id
              WHERE s.brand_id = %(brand)s AND p.status = 'done') AS pages_crawled,
            (SELECT COUNT(*) FROM site_images si JOIN sites s ON s.id = si.site_id
              WHERE s.brand_id = %(brand)s) AS site_images,
            (SELECT COUNT(*) FROM matches m JOIN reference_images r ON r.id = m.reference_id
              WHERE r.brand_id = %(brand)s) AS matches
        """,
        {"brand": brand_id},
    ).fetchone()
    return Stats(**row)
