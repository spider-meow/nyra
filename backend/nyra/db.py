"""SQLite persistence for the CLI (debugging, calibration, offline runs).

Tables:
    reference_images  the rights-managed image library (refs/ + refs.csv)
    pages             pages discovered while crawling a site
    site_images       distinct images found on the crawled site
    image_pages       many-to-many: a site image can appear on several pages
    matches           reference_image <-> site_image pairs found by match.py
    reviews           decisions taken on matches
    match_meta        signature of the last completed match pass

Every write is an upsert keyed on a natural key (filename, url) so re-running
ingest/crawl/match is idempotent and crawls can resume without duplicating
rows. `LocalStore` adapts this module to the crawler's and the matcher's
store interfaces; the hosted product has the same thing on Postgres
(`cloud.store`).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

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
CREATE INDEX IF NOT EXISTS idx_site_images_content_hash ON site_images(content_hash);

CREATE TABLE IF NOT EXISTS reviews (
    reference_id INTEGER NOT NULL,
    site_image_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (reference_id, site_image_id)
);

CREATE TABLE IF NOT EXISTS excluded_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    hash_type TEXT NOT NULL CHECK (hash_type IN ('phash', 'dhash')),
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (hash, hash_type)
);

CREATE TABLE IF NOT EXISTS match_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    signature TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
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
        _ensure_column(conn, "reference_images", "phash_flip", "TEXT")
        _ensure_column(conn, "reference_images", "dhash_flip", "TEXT")
        _ensure_column(conn, "site_images", "compared_at", "TEXT")


def pack_embedding(vector: Optional[np.ndarray]) -> Optional[bytes]:
    if vector is None:
        return None
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def feature_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "phash": row["phash"],
        "dhash": row["dhash"],
        "phash_flip": row["phash_flip"] if "phash_flip" in keys else None,
        "dhash_flip": row["dhash_flip"] if "dhash_flip" in keys else None,
        "embedding": unpack_embedding(row["embedding"]),
        "compared_at": row["compared_at"] if "compared_at" in keys else None,
    }


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
    phash_flip: Optional[str] = None,
    dhash_flip: Optional[str] = None,
    embedding: Optional[np.ndarray] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO reference_images
            (filename, path, expiry_date, credit, notes, phash, dhash, phash_flip, dhash_flip,
             embedding, width, height, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            path=excluded.path,
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
            updated_at=excluded.updated_at,
            compared_at=CASE
                WHEN reference_images.phash IS NOT excluded.phash
                  OR reference_images.dhash IS NOT excluded.dhash
                  OR reference_images.phash_flip IS NOT excluded.phash_flip
                  OR reference_images.embedding IS NOT excluded.embedding
                THEN NULL
                ELSE reference_images.compared_at
            END
        """,
        (
            filename, path, expiry_date, credit, notes, phash, dhash, phash_flip, dhash_flip,
            pack_embedding(embedding), width, height, ts, ts,
        ),
    )
    row = conn.execute("SELECT id FROM reference_images WHERE filename = ?", (filename,)).fetchone()
    return row["id"]


def get_reference_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM reference_images ORDER BY expiry_date").fetchall()


# --- pages ----------------------------------------------------------------

def upsert_page(conn: sqlite3.Connection, url: str, *, status: str = "pending", http_status: Optional[int] = None) -> int:
    conn.execute(
        "INSERT INTO pages (url, status, http_status, crawled_at) VALUES (?, ?, ?, NULL) ON CONFLICT(url) DO NOTHING",
        (url, status, http_status),
    )
    return conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()["id"]


def mark_page_crawled(conn: sqlite3.Connection, url: str, *, http_status: int) -> None:
    conn.execute(
        "UPDATE pages SET status = 'done', http_status = ?, crawled_at = ? WHERE url = ?",
        (http_status, now_iso(), url),
    )


def get_crawled_urls(conn: sqlite3.Connection) -> set[str]:
    return {r["url"] for r in conn.execute("SELECT url FROM pages WHERE status = 'done'").fetchall()}


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
        (url, local_path, content_hash, width, height, phash, dhash, pack_embedding(embedding), ts, ts),
    )
    return conn.execute("SELECT id FROM site_images WHERE url = ?", (url,)).fetchone()["id"]


def get_site_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM site_images").fetchall()


def link_image_page(conn: sqlite3.Connection, image_id: int, page_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO image_pages (image_id, page_id) VALUES (?, ?)", (image_id, page_id))


def pages_by_image(conn: sqlite3.Connection, image_ids: Sequence[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    ids = list(image_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"""SELECT ip.image_id, p.url FROM image_pages ip JOIN pages p ON p.id = ip.page_id
                WHERE ip.image_id IN ({marks}) ORDER BY p.url""",
            chunk,
        ):
            out.setdefault(row["image_id"], []).append(row["url"])
    return out


def get_pages_for_image(conn: sqlite3.Connection, image_id: int) -> list[str]:
    return pages_by_image(conn, [image_id]).get(image_id, [])


# --- matches ---------------------------------------------------------

def clear_matches(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM matches")


def _delete_ids(conn: sqlite3.Connection, column: str, ids: list[int]) -> None:
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM matches WHERE {column} IN ({marks})", chunk)


def delete_matches_for(conn: sqlite3.Connection, *, reference_ids: list[int], site_ids: list[int]) -> None:
    _delete_ids(conn, "reference_id", reference_ids)
    _delete_ids(conn, "site_image_id", site_ids)


def write_matches(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO matches (reference_id, site_image_id, level, score, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(reference_id, site_image_id) DO UPDATE SET
            level=excluded.level, score=excluded.score,
            confidence=excluded.confidence, created_at=excluded.created_at
        """,
        rows,
    )


def stamp_compared(conn: sqlite3.Connection, reference_ids: list[int], site_ids: list[int], signature: str) -> None:
    ts = now_iso()
    conn.executemany("UPDATE reference_images SET compared_at = ? WHERE id = ?", [(ts, item) for item in reference_ids])
    conn.executemany("UPDATE site_images SET compared_at = ? WHERE id = ?", [(ts, item) for item in site_ids])
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
                f"DELETE FROM reviews WHERE reference_id = ? AND site_image_id IN ({marks})", [reference_id, *chunk]
            )
        return
    ts = now_iso()
    conn.executemany(
        """
        INSERT INTO reviews (reference_id, site_image_id, decision, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(reference_id, site_image_id) DO UPDATE SET decision=excluded.decision, updated_at=excluded.updated_at
        """,
        [(reference_id, site_id, decision, ts) for site_id in site_image_ids],
    )


def get_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            m.id AS match_id, m.level, m.score, m.confidence,
            r.id AS reference_id, r.filename, r.path AS ref_path,
            r.expiry_date, r.credit, r.notes,
            s.id AS site_image_id, s.url AS site_url, s.local_path AS site_local_path,
            s.content_hash AS content_hash,
            v.decision AS decision
        FROM matches m
        JOIN reference_images r ON r.id = m.reference_id
        JOIN site_images s ON s.id = m.site_image_id
        LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
        ORDER BY r.expiry_date IS NULL, r.expiry_date ASC, m.score DESC
        """
    ).fetchall()


def get_unmatched_references(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """References with zero rows in `matches`.

    `compared_at IS NOT NULL` tells the caller this is a real "compared, no
    hit" result rather than a reference that just hasn't been through a
    match pass yet.
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


def match_rows(conn: sqlite3.Connection) -> list[dict]:
    """`get_matches` shaped for `report.group_matches` / `build_report`."""
    raw = get_matches(conn)
    pages = pages_by_image(conn, {row["site_image_id"] for row in raw})
    return [
        {
            "reference_id": row["reference_id"], "filename": row["filename"], "expiry_date": row["expiry_date"],
            "credit": row["credit"], "notes": row["notes"], "site_image_id": row["site_image_id"],
            "site_url": row["site_url"], "content_hash": row["content_hash"], "level": row["level"],
            "score": row["score"], "confidence": row["confidence"], "decision": row["decision"],
            "pages": pages.get(row["site_image_id"], []),
            "ref_thumb": row["ref_path"], "site_thumb": row["site_local_path"],
        }
        for row in raw
    ]


def unmatched_rows(conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "reference_id": row["reference_id"], "filename": row["filename"], "expiry_date": row["expiry_date"],
            "credit": row["credit"], "notes": row["notes"], "compared": row["compared_at"] is not None,
            "ref_thumb": row["ref_path"],
        }
        for row in get_unmatched_references(conn)
    ]


@dataclass(frozen=True)
class Stats:
    reference_images: int
    pages_crawled: int
    site_images: int
    matches: int


def get_stats(conn: sqlite3.Connection) -> Stats:
    def count(table: str, where: str = "") -> int:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table} {where}").fetchone()["c"]

    return Stats(
        reference_images=count("reference_images"),
        pages_crawled=count("pages", "WHERE status = 'done'"),
        site_images=count("site_images"),
        matches=count("matches"),
    )


# --- store adapter -------------------------------------------------------

class LocalStore:
    """SQLite + a local image cache, behind the crawler's and matcher's store interfaces."""

    def __init__(self, db_path: Path | str, cache_dir: Path | str = "data/site_images"):
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        init_db(self.db_path)

    @contextmanager
    def _conn(self):
        with connect(self.db_path) as conn:
            yield conn

    # CrawlStore
    def crawled_urls(self) -> set[str]:
        with self._conn() as conn:
            return get_crawled_urls(conn)

    def upsert_page(self, url: str) -> int:
        with self._conn() as conn:
            return upsert_page(conn, url)

    def mark_page(self, page_id: int, http_status: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE pages SET status = 'done', http_status = ?, crawled_at = ? WHERE id = ?",
                (http_status, now_iso(), page_id),
            )

    def known_images(self, urls: Sequence[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        urls = list(urls)
        with self._conn() as conn:
            for start in range(0, len(urls), 500):
                chunk = urls[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                for row in conn.execute(
                    f"SELECT id, url FROM site_images WHERE phash IS NOT NULL AND url IN ({marks})", chunk
                ):
                    out[row["url"]] = row["id"]
        return out

    def link(self, image_id: int, page_id: int) -> None:
        with self._conn() as conn:
            link_image_page(conn, image_id, page_id)

    def image_by_content_hash(self, digest: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM site_images WHERE content_hash = ? AND phash IS NOT NULL LIMIT 1", (digest,)
            ).fetchone()
        return dict(row) if row else None

    def save_duplicate(self, *, url: str, page_id: int, existing: dict) -> int:
        with self._conn() as conn:
            image_id = upsert_site_image(
                conn, url=url, local_path=existing["local_path"], content_hash=existing["content_hash"],
                width=existing["width"], height=existing["height"], phash=existing["phash"],
                dhash=existing["dhash"], embedding=unpack_embedding(existing["embedding"]),
            )
            link_image_page(conn, image_id, page_id)
        return image_id

    def save_image(self, *, url, page_id, data, content_type, processed, embedding) -> int:
        path = self.cache_dir / processed.content_hash[:2] / f"{processed.content_hash}{processed.extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        with self._conn() as conn:
            image_id = upsert_site_image(
                conn, url=url, local_path=str(path), content_hash=processed.content_hash,
                width=processed.width, height=processed.height, phash=processed.phash,
                dhash=processed.dhash, embedding=embedding,
            )
            link_image_page(conn, image_id, page_id)
        return image_id

    # MatchStore
    def load_features(self, use_clip: bool) -> tuple[list[dict], list[dict]]:
        with self._conn() as conn:
            refs = [feature_dict(row) for row in conn.execute("SELECT * FROM reference_images")]
            sites = [feature_dict(row) for row in conn.execute("SELECT * FROM site_images")]
        if not use_clip:
            for row in (*refs, *sites):
                row["embedding"] = None
        return refs, sites

    def get_signature(self) -> Optional[str]:
        with self._conn() as conn:
            return get_match_signature(conn)

    def load_exclusions(self) -> list[tuple[str, str]]:
        with self._conn() as conn:
            return [(row["hash"], row["hash_type"]) for row in conn.execute("SELECT hash, hash_type FROM excluded_hashes")]

    def save_matches(self, *, full, clear_ref_ids, clear_site_ids, hits, signature) -> int:
        created = now_iso()
        with self._conn() as conn:
            if full:
                clear_matches(conn)
            else:
                delete_matches_for(conn, reference_ids=clear_ref_ids, site_ids=clear_site_ids)
            write_matches(conn, [(*hit, created) for hit in hits])
            stamp_compared(conn, clear_ref_ids, clear_site_ids, signature)
            return conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
