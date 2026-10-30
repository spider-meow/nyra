"""The database side of the packshot category of "Images en ligne": the per-brand setting, the measures kept on each site image, and which images are packshots.

A brand that turns `detect_packshots` on gets a Packshots tab with the images `nyra.packshots` recognises, and "À qualifier" leaves them out. Nothing is hidden from
the comparison (`set_aside_ids` of `cloud/logos.py` is not touched). Off by default; nothing here changes what is shown or compared until it is on.

Migration 20261030000044 adds the columns. Until it is applied everything here answers "off": one cached look at the catalogue, no query on a column that is not there.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

import numpy as np
import psycopg

from .. import packshots as detector

log = logging.getLogger("nyra.packshots")
Row = dict
SCORE_CHUNK = 500
BACKGROUND_CHUNK = 200

# ponytail: shared by the process (that is the point): true for good once seen, false asked again after a minute, so a migration applied while the server runs is noticed.
_COLUMNS = {"seen": False, "asked_at": float("-inf")}
_RECHECK_SECONDS = 60


def has_columns(conn: psycopg.Connection) -> bool:
    """Whether migration 20261030000044 is applied."""
    if not _COLUMNS["seen"] and time.monotonic() - _COLUMNS["asked_at"] > _RECHECK_SECONDS:
        _COLUMNS["asked_at"] = time.monotonic()
        row = conn.execute(
            """SELECT count(*) AS n FROM information_schema.columns WHERE table_schema = 'public'
               AND ((table_name = 'site_images' AND column_name IN ('packshot_score', 'bg_uniform')) OR (table_name = 'brands' AND column_name = 'detect_packshots'))"""
        ).fetchone()
        _COLUMNS["seen"] = row["n"] == 3
    return bool(_COLUMNS["seen"])


def is_enabled(conn: psycopg.Connection, brand_id: uuid.UUID) -> bool:
    if not has_columns(conn):
        return False
    row = conn.execute("SELECT detect_packshots FROM brands WHERE id = %s", (brand_id,)).fetchone()
    return bool(row and row["detect_packshots"])


def set_enabled(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, enabled: bool) -> bool:
    """False when the brand is not found (or the migration is not applied)."""
    if not has_columns(conn):
        return False
    return conn.execute("UPDATE brands SET detect_packshots = %s WHERE id = %s AND org_id = %s", (enabled, brand_id, org_id)).rowcount > 0


def _array(value) -> np.ndarray:
    """A stored embedding (pgvector's type, or already a list) as a float32 array."""
    return np.asarray(value.to_numpy() if hasattr(value, "to_numpy") else value, dtype=np.float32)


def product_scores(embeddings: np.ndarray) -> list[float]:
    """CLIP's product probability of each embedding (rows), as plain floats (a number that is not one becomes 0)."""
    vectors = embeddings.astype(np.float32)
    vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
    return [float(score) for score in np.nan_to_num(detector.product_probability(vectors), nan=0.0)]


def score_pending(conn: psycopg.Connection, brand_id: uuid.UUID) -> int:
    """Score the brand's site images that have an embedding and no product score yet, a chunk at a time, when the setting is on; how many were scored."""
    if not is_enabled(conn, brand_id):
        return 0
    scored, last = 0, None
    while True:  # keyset on id: each pass moves on, and the rows are finite
        rows = conn.execute(
            """SELECT si.id, si.embedding FROM site_images si JOIN sites s ON s.id = si.site_id
               WHERE s.brand_id = %s AND si.embedding IS NOT NULL AND si.packshot_score IS NULL AND (%s::uuid IS NULL OR si.id > %s)
               ORDER BY si.id LIMIT %s""",
            (brand_id, last, last, SCORE_CHUNK),
        ).fetchall()
        if not rows:
            return scored
        scores = product_scores(np.stack([_array(row["embedding"]) for row in rows]))
        with conn.cursor() as cur:
            cur.executemany("UPDATE site_images SET packshot_score = %s WHERE id = %s", [(score, row["id"]) for score, row in zip(scores, rows)])
        scored += len(rows)
        last = rows[-1]["id"]


def pending_background(conn: psycopg.Connection, brand_id: uuid.UUID, after: uuid.UUID, limit: int = BACKGROUND_CHUNK) -> list[Row]:
    """The brand's site images whose border was not measured yet and that have a picture to measure (the thumbnail, else the working copy), after `after`, by id."""
    return conn.execute(
        """SELECT si.id, COALESCE(si.thumb_path, si.storage_path) AS path FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.bg_uniform IS NULL AND COALESCE(si.thumb_path, si.storage_path) IS NOT NULL AND si.id > %s
           ORDER BY si.id LIMIT %s""",
        (brand_id, after, limit),
    ).fetchall()


def ids(conn: psycopg.Connection, brand_id: uuid.UUID) -> set[uuid.UUID]:
    """The brand's site images that are packshots: none unless the setting is on."""
    if not is_enabled(conn, brand_id):
        return set()
    rows = conn.execute(
        """SELECT si.id FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.packshot_score >= %s::real AND si.bg_uniform >= %s::real""",
        (brand_id, detector.PRODUCT_THRESHOLD, detector.UNIFORM_THRESHOLD),
    ).fetchall()
    return {row["id"] for row in rows}


def fingerprint(conn: psycopg.Connection, brand_id: uuid.UUID) -> str:
    """Changes whenever the set of packshots does (the setting, a measure): part of the cache key of the unreferenced count."""
    return hashlib.md5(",".join(sorted(str(item) for item in ids(conn, brand_id))).encode()).hexdigest()


def measures(conn: psycopg.Connection, site_image_id: uuid.UUID, embedding: np.ndarray | None, uniform: float | None) -> None:
    """Keep the two measures of a site image just stored (a crawl knows both at once). Nothing before the migration. A failure here (a descriptions file
    out of date, an embedding of another model) is logged and the image is kept without them: reading a site must never lose an image for a category
    that is off by default; the next `index` job of a brand that turned it on measures what is missing."""
    if not has_columns(conn):
        return
    try:
        score = product_scores(np.asarray([embedding]))[0] if embedding is not None else None
        with conn.transaction():  # a failed statement must not leave the crawl's transaction aborted
            conn.execute("UPDATE site_images SET packshot_score = %s, bg_uniform = %s WHERE id = %s", (score, uniform, site_image_id))
    except Exception:  # noqa: BLE001 - see above: logged, the image is already stored
        log.exception("keeping the packshot measures of image %s failed", site_image_id)


def copy_measures(conn: psycopg.Connection, site_image_id: uuid.UUID) -> None:
    """A copy of a file already stored (the same bytes under another address, `save_duplicate`) has the measures of its twin."""
    if not has_columns(conn):
        return
    conn.execute(
        """UPDATE site_images d SET packshot_score = s.packshot_score, bg_uniform = s.bg_uniform FROM site_images s
           WHERE d.id = %s AND s.org_id = d.org_id AND s.content_hash = d.content_hash AND s.id <> d.id AND s.bg_uniform IS NOT NULL""",
        (site_image_id,),
    )
