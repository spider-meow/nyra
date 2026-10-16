"""The database side of setting logos aside: score the site images, say which ones are set aside, put one back.

A brand that turns `set_aside_logos` on keeps the images `nyra.logos` recognises as logos out of the comparison and out of
the "À qualifier" tab of "Images en ligne". Off by default; nothing here changes what is shown or compared until it is on.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Optional

import numpy as np
import psycopg

from .. import logos as detector
from . import db as cloud_db

Row = dict
SCORE_CHUNK = 500
LIST_LIMIT = 500

# An image of the brand is set aside when the setting is on, it scores above the threshold and nobody put it back.
_SET_ASIDE = """si.logo_score >= %(threshold)s::real AND NOT si.logo_kept
                AND EXISTS (SELECT 1 FROM brands b WHERE b.id = s.brand_id AND b.set_aside_logos)"""


def is_enabled(conn: psycopg.Connection, brand_id: uuid.UUID) -> bool:
    row = conn.execute("SELECT set_aside_logos FROM brands WHERE id = %s", (brand_id,)).fetchone()
    return bool(row and row["set_aside_logos"])


def set_enabled(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, enabled: bool) -> bool:
    return conn.execute("UPDATE brands SET set_aside_logos = %s WHERE id = %s AND org_id = %s", (enabled, brand_id, org_id)).rowcount > 0


def score_pending(conn: psycopg.Connection, brand_id: uuid.UUID) -> int:
    """Score the brand's site images that have an embedding and no logo score yet, a chunk at a time; how many were scored."""
    scored, last = 0, None
    while True:  # keyset on id: each pass moves on, and the rows are finite
        rows = conn.execute(
            """SELECT si.id, si.embedding FROM site_images si JOIN sites s ON s.id = si.site_id
               WHERE s.brand_id = %s AND si.embedding IS NOT NULL AND si.logo_score IS NULL AND (%s::uuid IS NULL OR si.id > %s)
               ORDER BY si.id LIMIT %s""",
            (brand_id, last, last, SCORE_CHUNK),
        ).fetchall()
        if not rows:
            return scored
        vectors = np.stack([np.asarray(row["embedding"].to_numpy() if hasattr(row["embedding"], "to_numpy") else row["embedding"],
                                       dtype=np.float32) for row in rows])
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
        with conn.cursor() as cur:
            cur.executemany("UPDATE site_images SET logo_score = %s WHERE id = %s",
                            [(float(score), row["id"]) for score, row in zip(np.nan_to_num(detector.logo_probability(vectors), nan=0.0), rows)])
        scored += len(rows)
        last = rows[-1]["id"]


def set_aside_ids(conn: psycopg.Connection, brand_id: uuid.UUID) -> set[uuid.UUID]:
    """The brand's site images to leave out: none unless the setting is on."""
    rows = conn.execute(
        f"SELECT si.id FROM site_images si JOIN sites s ON s.id = si.site_id WHERE s.brand_id = %(brand)s AND {_SET_ASIDE}",
        {"brand": brand_id, "threshold": detector.THRESHOLD},
    ).fetchall()
    return {row["id"] for row in rows}


def fingerprint(conn: psycopg.Connection, brand_id: uuid.UUID) -> str:
    """Changes whenever the set of images set aside does (the setting, a score, an image put back): part of the cache key
    of the unreferenced count."""
    return hashlib.md5(",".join(sorted(str(item) for item in set_aside_ids(conn, brand_id))).encode()).hexdigest()


def list_recognised(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """The brand's images above the threshold, one per file (the same bytes under several addresses count once), best score
    first, whether or not they are set aside: the list a person goes through to put one back."""
    rows = conn.execute(
        """SELECT DISTINCT ON (coalesce(si.content_hash, si.id::text))
                  si.id, si.url, si.thumb_path, si.width, si.height, si.logo_score,
                  -- put back only when every address of the file is: a new address read since is still set aside
                  bool_and(si.logo_kept) OVER (PARTITION BY coalesce(si.content_hash, si.id::text)) AS logo_kept,
                  (SELECT count(*) FROM image_pages ip WHERE ip.image_id = si.id) AS pages
           FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.logo_score >= %s::real
           ORDER BY coalesce(si.content_hash, si.id::text), si.id""",
        (brand_id, detector.THRESHOLD),
    ).fetchall()
    return sorted(rows, key=lambda row: -row["logo_score"])[:LIST_LIMIT]


def keep(conn: psycopg.Connection, brand_id: uuid.UUID, site_image_ids: list[uuid.UUID], kept: bool) -> Optional[int]:
    """Put images back into the comparison, or set them aside again: each file named and every address of the brand serving
    the same bytes (one photo can be several files: crops, resizes). The number of rows changed; None when none of the
    images is one of this brand's."""
    rows = conn.execute(
        """SELECT si.id, si.content_hash FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.id = ANY(%s)""",
        (brand_id, site_image_ids),
    ).fetchall()
    if not rows:
        return None
    ids = {uuid_ for row in rows for uuid_ in cloud_db.same_image_ids(conn, brand_id, row["content_hash"], row["id"])}
    return conn.execute("UPDATE site_images SET logo_kept = %s WHERE id = ANY(%s)", (kept, list(ids))).rowcount
