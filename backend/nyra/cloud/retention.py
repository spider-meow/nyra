"""How long the images read on the sites are kept (AXE-533).

A crawl stores, for each image, a working copy (a JPEG of at most 1,024 px) and a thumbnail. Rows are never deleted when an image leaves a site (the state
of a visual is worked out from dates, see `presence.py`), so Storage only grows. The rule: **an image not seen for two months is considered no longer on the
site, and its working copy is deleted.** The row stays, with its hashes, its embedding, its dates and its thumbnail; only `storage_path` is emptied.

What is never purged: an image that matches a visual of the library, that was judged (`reviews`) or located (`reference_locations`); an image of a site whose
last finished read is older than the window (a site nobody read for two months says nothing about what is on it); the library itself (another bucket) and
the reports. An image that comes back on the site is read again like a new one (`known_images` ignores rows without a working copy), so the file returns.

Bounded: `BATCH` images at a time, `MAX_BATCHES` a run, the worker runs it once a day. Each batch is committed (and traced in the audit log, one row per
organization) before its files are deleted: a failure leaves orphan files, never a row pointing at a missing file.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from . import audit as cloud_audit
from . import db as cloud_db
from . import storage as cloud_storage

log = logging.getLogger("nyra.retention")

IMAGE_MONTHS = 2  # not seen for this long: no longer on the site
BATCH = 500
MAX_BATCHES = 20  # 10,000 images a run; the next run takes the rest

_CANDIDATES = """
    SELECT si.id, si.org_id, si.storage_path FROM site_images si
    WHERE si.storage_path IS NOT NULL
      AND si.last_seen < now() - make_interval(months => %(months)s)
      AND EXISTS (SELECT 1 FROM crawl_runs c WHERE c.site_id = si.site_id AND c.status = 'done'
                  AND c.finished_at >= now() - make_interval(months => %(months)s))
      AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.site_image_id = si.id)
      AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.site_image_id = si.id)
      AND NOT EXISTS (SELECT 1 FROM reference_locations l WHERE l.site_image_id = si.id)
    ORDER BY si.last_seen LIMIT %(limit)s"""


def purge_batch(conn, months: int = IMAGE_MONTHS, limit: Optional[int] = None) -> dict[uuid.UUID, list[str]]:
    """Empty the working copy of the next images not seen for `months`: {organization: files no row points at any more}. Committed with the caller's transaction."""
    rows = conn.execute(_CANDIDATES, {"months": months, "limit": limit or BATCH}).fetchall()
    by_org: dict[uuid.UUID, list[dict]] = {}
    for row in rows:
        by_org.setdefault(row["org_id"], []).append(row)
    freed: dict[uuid.UUID, list[str]] = {}
    for org_id, images in by_org.items():
        conn.execute("UPDATE site_images SET storage_path = NULL WHERE org_id = %s AND id = ANY(%s)", (org_id, [row["id"] for row in images]))
        # A working copy is shared by every address serving the same bytes: it goes only when no row points at it.
        freed[org_id] = cloud_db._unused_site_paths(conn, org_id, [row["storage_path"] for row in images])
        cloud_audit.record(conn, org_id, None, "images.purge", "site_image", images=len(images), files=len(freed[org_id]), months=months)
    return freed


def purge_images(database_url: str, storage: Any, *, months: int = IMAGE_MONTHS, between: Optional[Callable[[], None]] = None) -> dict:
    """Run the purge: batches until nothing is left or `MAX_BATCHES` is reached. What it did, for the log."""
    done = {"batches": 0, "files": 0}
    for _ in range(MAX_BATCHES):
        with cloud_db.connect(database_url) as conn:
            freed = purge_batch(conn, months)
        if not freed:
            break
        done["batches"] += 1
        for paths in freed.values():
            try:
                cloud_storage.delete(storage, cloud_storage.BUCKET_SITE_IMAGES, paths)
                done["files"] += len(paths)
            except Exception:  # noqa: BLE001 - the rows are committed; an orphan file is harmless and costs only its size
                log.warning("deleting %d file(s) of purged images failed; orphans left behind", len(paths), exc_info=True)
        if between:
            between()
    return done
