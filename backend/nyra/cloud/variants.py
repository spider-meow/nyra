"""Group a brand's site images that are crops or resizes of one photo (the worker's step after a comparison).

Only images not looked at yet (`variants_checked_at` NULL) are searched for twins, among all of the
brand's images; the groups found are written in one transaction, so a stop or a crash keeps the old ones.
The same bytes under several addresses are one image here (they already share a `content_hash`).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

import numpy as np

from nyra import fetch, verify
from nyra.config import Config
from nyra.match import MatchStopped
from nyra.variants import candidate_pairs, link_groups

from . import db as cloud_db
from . import storage as cloud_storage
from .store import feature_dict

log = logging.getLogger("nyra.variants")

# New images searched for twins per saved step.
FRESH_BATCH = 200

_SELECT = """SELECT si.id, si.content_hash, si.embedding, si.phash, si.storage_path, si.variant_group,
        si.variants_checked_at IS NULL AS fresh
    FROM site_images si JOIN sites s ON s.id = si.site_id
    WHERE s.brand_id = %s AND si.storage_path IS NOT NULL AND si.embedding IS NOT NULL
    ORDER BY si.id"""
_UPDATE = """UPDATE site_images SET variant_group = %s,
        variants_checked_at = CASE WHEN %s THEN now() ELSE variants_checked_at END
    WHERE org_id = %s AND id = ANY(%s)"""


def _distinct_images(rows: list[dict]) -> list[dict]:
    """One entry per distinct file (rows sharing a `content_hash` are one), with the ids it stands for."""
    nodes: dict[Any, dict] = {}
    for row in map(feature_dict, rows):
        node = nodes.setdefault(row["content_hash"] or row["id"], {**row, "ids": [], "fresh": False, "group": None})
        node["ids"].append(row["id"])
        node["fresh"] = node["fresh"] or row["fresh"]
        node["group"] = node["group"] or row["variant_group"]
    return list(nodes.values())


def _image_loader(storage: Any, images: list[dict], max_pixels: int, unreadable: set[int]) -> Callable[[int], Optional[Any]]:
    """Reads an image by index; the ones that can't be read are added to `unreadable`."""
    def load(index: int):
        image = images[index]
        try:
            data = cloud_storage.cached_download(storage, cloud_storage.BUCKET_SITE_IMAGES, image["storage_path"],
                                                 version=image["content_hash"] or image["phash"] or "")
            decoded = fetch.decode(data, max_pixels)
        except Exception:  # noqa: BLE001 - a missing object only leaves this image unchecked until the next run
            log.warning("reading %s failed; the image is not grouped yet", image["storage_path"], exc_info=True)
            decoded = None
        if decoded is None:
            unreadable.add(index)
        return decoded

    return load


def _check_batch(batch: list[int], images: list[dict], matrix: np.ndarray, storage: Any, config: Config,
                 should_stop: Optional[Callable[[], bool]]) -> tuple[list[tuple[int, int]], int, set[int]]:
    """Look for the twins of `batch`: the pairs that are one photo, how many were checked, and the images
    that could not be checked because they or a candidate twin could not be read."""
    pairs = candidate_pairs(matrix, batch, config.match.clip_similarity_floor)
    unreadable: set[int] = set()
    links = verify.same_photo_pairs(pairs, _image_loader(storage, images, config.crawl.max_image_pixels, unreadable),
                                    config.match, should_stop=should_stop)
    if should_stop and should_stop():
        raise MatchStopped()
    unchecked = unreadable | {index for pair in pairs if unreadable.intersection(pair) for index in pair}
    return links, len(pairs), unchecked


def update_variants(database_url: str, storage: Any, org_id: uuid.UUID, brand_id: uuid.UUID, config: Config, *,
                    progress: Optional[Callable[[int, int], None]] = None,
                    should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Search the brand's new site images for twins and save the groups; what it did, for the job's figures."""
    if not verify.available():
        log.warning("OpenCV is missing: site images are not grouped")
        return {"variant_checked": 0}
    with cloud_db.connect(database_url) as conn:
        images = _distinct_images(conn.execute(_SELECT, (brand_id,)).fetchall())
    fresh = [index for index, image in enumerate(images) if image["fresh"]]
    stats = {"variant_images": len(images), "variant_checked": 0, "variant_pairs": 0, "variant_links": 0}
    if not fresh:
        return stats
    matrix = np.stack([image["embedding"] for image in images])
    # A batch at a time, each saved on its own: a stop or a restart keeps what was done, and a first run over a
    # large site is not one all-or-nothing job.
    for start in range(0, len(fresh), FRESH_BATCH):
        batch = fresh[start : start + FRESH_BATCH]
        links, pairs, unchecked = _check_batch(batch, images, matrix, storage, config, should_stop)
        groups = link_groups([image["group"] for image in images], links)
        done = set(batch) - unchecked  # checked now: their search for twins is finished
        updates = [(groups[index], index in done, org_id, image["ids"]) for index, image in enumerate(images)
                   if index in done or groups[index] != image["group"]]
        with cloud_db.connect(database_url) as conn, conn.cursor() as cur:
            cur.executemany(_UPDATE, updates)
        for index, image in enumerate(images):
            image["group"] = groups[index]
            image["fresh"] = image["fresh"] and index not in done
        stats["variant_checked"] += len(done)
        stats["variant_pairs"] += pairs
        stats["variant_links"] += len(links)
        if progress:
            progress(min(start + FRESH_BATCH, len(fresh)), len(fresh))
    return stats
