"""Find every crawled image of a brand that is a crop, resize or copy of one library reference (the `locate` job).

Searches only what the brand's crawls already stored (no new read of a site). Every image gets the keypoint
check (`verify.locate`), then the images grouped with a hit by `variant_group` join it: a crop too small to
match the reference on its own is still the same photo as a larger crop that did. The result replaces the
reference's previous one in one transaction, so a stop or a crash keeps the old result.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from nyra import fetch, verify
from nyra import match as match_module
from nyra.config import Config
from nyra.match import MatchStopped

from . import db as cloud_db
from . import storage as cloud_storage

log = logging.getLogger("nyra.locate")

_SELECT = """SELECT si.id, si.content_hash, si.storage_path, si.phash, si.dhash, si.variant_group
    FROM site_images si JOIN sites s ON s.id = si.site_id
    WHERE s.brand_id = %s AND si.storage_path IS NOT NULL ORDER BY si.id"""
_INSERT = """INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage)
    VALUES (%s, %s, %s, %s, %s, %s, %s)"""


class NotSearchable(Exception):
    """The reference can't be searched for; the message is fit for the interface."""


def distinct_files(rows: list[dict]) -> list[dict]:
    """One entry per distinct file (rows sharing a `content_hash` are one), with the ids it stands for."""
    nodes: dict[Any, dict] = {}
    for row in rows:
        nodes.setdefault(row["content_hash"] or row["id"], {**row, "ids": []})["ids"].append(row["id"])
    return list(nodes.values())


def with_group_mates(hits: dict[uuid.UUID, tuple], rows: list[dict]) -> dict[uuid.UUID, tuple]:
    """`hits` (image id -> (tier, inliers, ref coverage, site coverage)) plus every image that shares a
    `variant_group` with a hit: same photo as a hit, so it takes the best tier of its group's hits, with no figures."""
    best: dict[Any, str] = {}
    for row in rows:
        hit = hits.get(row["id"])
        if hit and row["variant_group"]:
            best[row["variant_group"]] = verify.SAME if verify.SAME in (hit[0], best.get(row["variant_group"])) else hit[0]
    out = dict(hits)
    for row in rows:
        if row["variant_group"] in best and row["id"] not in out:
            out[row["id"]] = (best[row["variant_group"]], 0, 0.0, 0.0)
    return out


def _load_reference(storage: Any, row: dict, max_pixels: int):
    path = row["work_path"] or row["storage_path"]
    img, refused = fetch.decode_reference(cloud_storage.download(storage, cloud_storage.BUCKET_REFS, path), max_pixels)
    if img is None:
        raise NotSearchable(refused or "L'image de référence est illisible.")
    return img


def locate_reference(database_url: str, storage: Any, org_id: uuid.UUID, brand_id: uuid.UUID, reference_id: uuid.UUID,
                     config: Config, *, progress: Optional[Callable[[int, int], None]] = None,
                     should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Search the brand's crawled images for the reference and save what is found; the figures of the job."""
    with cloud_db.connect(database_url) as conn:
        reference = conn.execute("SELECT id, storage_path, work_path, phash FROM reference_images WHERE id = %s AND brand_id = %s",
                                 (reference_id, brand_id)).fetchone()
        rows = conn.execute(_SELECT, (brand_id,)).fetchall()
        excluded = match_module.excluded_site_ids(rows, cloud_db.load_exclusions(conn, brand_id), config.match)
    if reference is None:
        raise NotSearchable("Référence introuvable.")
    rows = [row for row in rows if row["id"] not in excluded]  # images set aside as recurring false positives
    if not verify.available():
        raise NotSearchable("OpenCV manque sur ce worker : la recherche est impossible.")
    files = distinct_files(rows)
    unreadable = 0

    def load(index: int):
        nonlocal unreadable
        file = files[index]
        try:
            decoded = fetch.decode(cloud_storage.cached_download(storage, cloud_storage.BUCKET_SITE_IMAGES,
                                                                 file["storage_path"],
                                                                 version=file["content_hash"] or file["phash"] or ""),
                                   config.crawl.max_image_pixels)
        except Exception:  # noqa: BLE001 - a missing object only leaves this image out of the search (counted below)
            log.warning("reading %s failed; the image is not searched", file["storage_path"])
            decoded = None
        unreadable += decoded is None
        return decoded

    try:
        found = verify.locate(_load_reference(storage, reference, config.crawl.max_image_pixels), list(range(len(files))),
                              load, config.match, progress=progress, should_stop=should_stop)
    except verify.NoDetail as exc:
        raise NotSearchable("Cette image a trop peu de détails pour être recherchée.") from exc
    if should_stop and should_stop():
        raise MatchStopped()
    hits = {image_id: (tier, verdict.inliers, verdict.coverage_a, verdict.coverage_b)
            for index, tier, verdict in found for image_id in files[index]["ids"]}
    saved = with_group_mates(hits, rows)
    with cloud_db.connect(database_url) as conn, conn.cursor() as cur:
        # The search is long: the reference may have been deleted or replaced meanwhile. Locked, so it cannot change under the write.
        current = cur.execute("SELECT phash FROM reference_images WHERE id = %s AND org_id = %s FOR SHARE",
                              (reference_id, org_id)).fetchone()
        if current is None or current["phash"] != reference["phash"]:
            raise NotSearchable("L'image a été supprimée ou remplacée pendant la recherche : relancez-la.")
        cur.execute("DELETE FROM reference_locations WHERE reference_id = %s AND org_id = %s", (reference_id, org_id))
        cur.executemany(_INSERT, [(reference_id, image_id, org_id, *values) for image_id, values in saved.items()])
        cur.execute("UPDATE reference_images SET located_at = now() WHERE id = %s AND org_id = %s", (reference_id, org_id))
    return {"searched": len(files), "unreadable": unreadable, "found": len(saved), "found_direct": len(hits)}
