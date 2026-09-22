"""SQLite persistence layer.

Tables:
    reference_images  the rights-managed image library (refs/ + refs.csv)
    pages             pages discovered while crawling a site
    site_images       distinct images found on the crawled site
    image_pages       many-to-many: a site image can appear on several pages
    matches           reference_image <-> site_image pairs found by match.py

Every write is an upsert keyed on a natural key (filename, url) so re-running
ingest/crawl/match is idempotent and crawls can resume without duplicating
rows.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    expiry_date TEXT,
    credit TEXT,
    notes TEXT,
    phash TEXT,
    dhash TEXT,
    embedding BLOB,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    http_status INTEGER,
    crawled_at TEXT
);

CREATE TABLE IF NOT EXISTS site_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    local_path TEXT,
    content_hash TEXT,
    width INTEGER,
    height INTEGER,
    phash TEXT,
    dhash TEXT,
    embedding BLOB,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS image_pages (
    image_id INTEGER NOT NULL REFERENCES site_images(id) ON DELETE CASCADE,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    PRIMARY KEY (image_id, page_id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_id INTEGER NOT NULL REFERENCES reference_images(id) ON DELETE CASCADE,
    site_image_id INTEGER NOT NULL REFERENCES site_images(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    score REAL NOT NULL,
    confidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (reference_id, site_image_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_reference ON matches(reference_id);
CREATE INDEX IF NOT EXISTS idx_matches_site_image ON matches(site_image_id);
CREATE INDEX IF NOT EXISTS idx_image_pages_image ON image_pages(image_id);

CREATE TABLE IF NOT EXISTS reviews (
    reference_id INTEGER NOT NULL,
    site_image_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (reference_id, site_image_id)
);

CREATE TABLE IF NOT EXISTS match_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    signature TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS excluded_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    hash_type TEXT NOT NULL CHECK (hash_type IN ('phash', 'dhash')),
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (hash, hash_type)
);
"""

MATCH_STATUSES = ("pending", "confirmed", "rejected")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def init_db(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "reference_images", "compared_at", "TEXT")
        _ensure_column(conn, "site_images", "compared_at", "TEXT")
        _ensure_column(conn, "matches", "status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "matches", "reviewed_at", "TEXT")
        _ensure_column(conn, "matches", "reviewed_note", "TEXT")


def pack_embedding(vector: Optional[np.ndarray]) -> Optional[bytes]:
    if vector is None:
        return None
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


# --- reference_images ---------------------------------------------------

def upsert_reference_image(
    conn: sqlite3.Connection,
    *,
    filename: str,
    path: str,
    expiry_date: Optional[str],
    credit: Optional[str],
    notes: Optional[str],
    phash: Optional[str] = None,
    dhash: Optional[str] = None,
    embedding: Optional[np.ndarray] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO reference_images
            (filename, path, expiry_date, credit, notes, phash, dhash,
             embedding, width, height, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            path=excluded.path,
            expiry_date=excluded.expiry_date,
            credit=excluded.credit,
            notes=excluded.notes,
            phash=excluded.phash,
            dhash=excluded.dhash,
            embedding=excluded.embedding,
            width=excluded.width,
            height=excluded.height,
            updated_at=excluded.updated_at,
            compared_at=CASE
                WHEN reference_images.phash IS NOT excluded.phash
                  OR reference_images.dhash IS NOT excluded.dhash
                  OR reference_images.embedding IS NOT excluded.embedding
                THEN NULL
                ELSE reference_images.compared_at
            END
        """,
        (
            filename, path, expiry_date, credit, notes, phash, dhash,
            pack_embedding(embedding), width, height, ts, ts,
        ),
    )
    row = conn.execute(
        "SELECT id FROM reference_images WHERE filename = ?", (filename,)
    ).fetchone()
    return row["id"]


def get_reference_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM reference_images ORDER BY expiry_date").fetchall()


# --- pages ----------------------------------------------------------------

def upsert_page(
    conn: sqlite3.Connection, url: str, *, status: str = "pending", http_status: Optional[int] = None
) -> int:
    conn.execute(
        """
        INSERT INTO pages (url, status, http_status, crawled_at)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(url) DO NOTHING
        """,
        (url, status, http_status),
    )
    row = conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
    return row["id"]


def mark_page_crawled(conn: sqlite3.Connection, url: str, *, http_status: int) -> None:
    conn.execute(
        "UPDATE pages SET status = 'done', http_status = ?, crawled_at = ? WHERE url = ?",
        (http_status, now_iso(), url),
    )


def get_crawled_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT url FROM pages WHERE status = 'done'").fetchall()
    return {r["url"] for r in rows}


def get_pending_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT url FROM pages WHERE status = 'pending'").fetchall()
    return {r["url"] for r in rows}


# --- site_images ------------------------------------------------------

def upsert_site_image(
    conn: sqlite3.Connection,
    *,
    url: str,
    local_path: Optional[str] = None,
    content_hash: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    phash: Optional[str] = None,
    dhash: Optional[str] = None,
    embedding: Optional[np.ndarray] = None,
) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO site_images
            (url, local_path, content_hash, width, height, phash, dhash,
             embedding, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            local_path=COALESCE(excluded.local_path, site_images.local_path),
            content_hash=COALESCE(excluded.content_hash, site_images.content_hash),
            width=COALESCE(excluded.width, site_images.width),
            height=COALESCE(excluded.height, site_images.height),
            phash=COALESCE(excluded.phash, site_images.phash),
            dhash=COALESCE(excluded.dhash, site_images.dhash),
            embedding=COALESCE(excluded.embedding, site_images.embedding),
            last_seen=excluded.last_seen,
            compared_at=CASE
                WHEN excluded.phash IS NOT NULL AND excluded.phash IS NOT site_images.phash THEN NULL
                WHEN excluded.dhash IS NOT NULL AND excluded.dhash IS NOT site_images.dhash THEN NULL
                WHEN excluded.embedding IS NOT NULL AND excluded.embedding IS NOT site_images.embedding THEN NULL
                ELSE site_images.compared_at
            END
        """,
        (
            url, local_path, content_hash, width, height, phash, dhash,
            pack_embedding(embedding), ts, ts,
        ),
    )
    row = conn.execute("SELECT id FROM site_images WHERE url = ?", (url,)).fetchone()
    return row["id"]


def set_site_image_hashes(
    conn: sqlite3.Connection,
    image_id: int,
    *,
    phash: Optional[str],
    dhash: Optional[str],
    embedding: Optional[np.ndarray] = None,
) -> None:
    conn.execute(
        "UPDATE site_images SET phash = ?, dhash = ?, embedding = COALESCE(?, embedding) WHERE id = ?",
        (phash, dhash, pack_embedding(embedding), image_id),
    )


def get_site_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM site_images").fetchall()


def link_image_page(conn: sqlite3.Connection, image_id: int, page_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO image_pages (image_id, page_id) VALUES (?, ?)",
        (image_id, page_id),
    )


def get_pages_for_image(conn: sqlite3.Connection, image_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT p.url FROM pages p
        JOIN image_pages ip ON ip.page_id = p.id
        WHERE ip.image_id = ?
        ORDER BY p.url
        """,
        (image_id,),
    ).fetchall()
    return [r["url"] for r in rows]


# --- matches ---------------------------------------------------------

def upsert_match(
    conn: sqlite3.Connection,
    *,
    reference_id: int,
    site_image_id: int,
    level: str,
    score: float,
    confidence: str,
) -> None:
    conn.execute(
        """
        INSERT INTO matches (reference_id, site_image_id, level, score, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(reference_id, site_image_id) DO UPDATE SET
            level=excluded.level,
            score=excluded.score,
            confidence=excluded.confidence,
            created_at=excluded.created_at
        """,
        (reference_id, site_image_id, level, score, confidence, now_iso()),
    )


def clear_matches(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM matches")


def delete_matches_for(conn: sqlite3.Connection, *, reference_ids: list[int], site_ids: list[int]) -> None:
    _delete_ids(conn, "reference_id", reference_ids)
    _delete_ids(conn, "site_image_id", site_ids)


def _delete_ids(conn: sqlite3.Connection, column: str, ids: list[int]) -> None:
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM matches WHERE {column} IN ({marks})", chunk)


def write_matches(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO matches (reference_id, site_image_id, level, score, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(reference_id, site_image_id) DO UPDATE SET
            level=excluded.level,
            score=excluded.score,
            confidence=excluded.confidence,
            created_at=excluded.created_at
        """,
        rows,
    )


def stamp_compared(conn: sqlite3.Connection, reference_ids: list[int], site_ids: list[int], signature: str) -> None:
    ts = now_iso()
    for start in range(0, len(reference_ids), 500):
        chunk = reference_ids[start : start + 500]
        conn.executemany(
            "UPDATE reference_images SET compared_at = ? WHERE id = ?",
            [(ts, item) for item in chunk],
        )
    for start in range(0, len(site_ids), 500):
        chunk = site_ids[start : start + 500]
        conn.executemany(
            "UPDATE site_images SET compared_at = ? WHERE id = ?",
            [(ts, item) for item in chunk],
        )
    conn.execute(
        """
        INSERT INTO match_meta (id, signature, finished_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET signature=excluded.signature, finished_at=excluded.finished_at
        """,
        (signature, ts),
    )


def get_match_signature(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT signature FROM match_meta WHERE id = 1").fetchone()
    return None if row is None else row["signature"]


def set_reviews(conn: sqlite3.Connection, reference_id: int, site_image_ids: list[int], decision: str) -> None:
    if not decision:
        for start in range(0, len(site_image_ids), 500):
            chunk = site_image_ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM reviews WHERE reference_id = ? AND site_image_id IN ({marks})",
                [reference_id, *chunk],
            )
        return
    ts = now_iso()
    conn.executemany(
        """
        INSERT INTO reviews (reference_id, site_image_id, decision, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(reference_id, site_image_id) DO UPDATE SET
            decision=excluded.decision,
            updated_at=excluded.updated_at
        """,
        [(reference_id, site_id, decision, ts) for site_id in site_image_ids],
    )


def get_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            m.id AS match_id, m.level, m.score, m.confidence,
            m.status AS match_status, m.reviewed_at, m.reviewed_note,
            r.id AS reference_id, r.filename, r.path AS ref_path,
            r.expiry_date, r.credit, r.notes,
            s.id AS site_image_id, s.url AS site_url, s.local_path AS site_local_path,
            s.content_hash AS content_hash,
            v.decision AS decision
        FROM matches m
        JOIN reference_images r ON r.id = m.reference_id
        JOIN site_images s ON s.id = m.site_image_id
        LEFT JOIN reviews v
            ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
        ORDER BY r.expiry_date IS NULL, r.expiry_date ASC, m.score DESC
        """
    ).fetchall()


# --- match status (review traceability) ------------------------------------

def set_match_status(conn: sqlite3.Connection, match_id: int, status: str, note: Optional[str] = None) -> bool:
    if status not in MATCH_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    cur = conn.execute(
        "UPDATE matches SET status = ?, reviewed_at = ?, reviewed_note = ? WHERE id = ?",
        (status, now_iso(), note, match_id),
    )
    return cur.rowcount > 0


def get_match_site_hashes(conn: sqlite3.Connection, match_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT m.id AS match_id, s.id AS site_image_id, s.phash, s.dhash
        FROM matches m JOIN site_images s ON s.id = m.site_image_id
        WHERE m.id = ?
        """,
        (match_id,),
    ).fetchone()


# --- excluded_hashes (recurring false positives) ----------------------------

def add_excluded_hash(conn: sqlite3.Connection, *, hash: str, hash_type: str, reason: Optional[str] = None) -> int:
    if hash_type not in {"phash", "dhash"}:
        raise ValueError(f"Unknown hash_type: {hash_type!r}")
    conn.execute(
        """
        INSERT INTO excluded_hashes (hash, hash_type, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(hash, hash_type) DO UPDATE SET reason=excluded.reason
        """,
        (hash, hash_type, reason, now_iso()),
    )
    row = conn.execute(
        "SELECT id FROM excluded_hashes WHERE hash = ? AND hash_type = ?", (hash, hash_type)
    ).fetchone()
    return row["id"]


def get_excluded_hashes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM excluded_hashes ORDER BY created_at DESC").fetchall()


def delete_excluded_hash(conn: sqlite3.Connection, excluded_id: int) -> None:
    conn.execute("DELETE FROM excluded_hashes WHERE id = ?", (excluded_id,))


def get_unmatched_references(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """References with zero rows in `matches` — the library items a crawl found nowhere.

    `compared_at IS NOT NULL` tells the caller this is a real "compared, no
    hit" result rather than a reference that just hasn't been through a
    match pass yet (e.g. added after the last `run_matching`).
    """
    return conn.execute(
        """
        SELECT r.id AS reference_id, r.filename, r.path AS ref_path,
               r.expiry_date, r.credit, r.notes, r.compared_at
        FROM reference_images r
        LEFT JOIN matches m ON m.reference_id = r.id
        WHERE m.id IS NULL
        ORDER BY r.expiry_date IS NULL, r.expiry_date ASC
        """
    ).fetchall()


@dataclass(frozen=True)
class Stats:
    reference_images: int
    pages_crawled: int
    site_images: int
    matches: int


def get_stats(conn: sqlite3.Connection) -> Stats:
    def count(table: str, where: str = "") -> int:
        sql = f"SELECT COUNT(*) AS c FROM {table} {where}"
        return conn.execute(sql).fetchone()["c"]

    return Stats(
        reference_images=count("reference_images"),
        pages_crawled=count("pages", "WHERE status = 'done'"),
        site_images=count("site_images"),
        matches=count("matches"),
    )
