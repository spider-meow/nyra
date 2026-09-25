"""Postgres + Supabase Storage behind the pipeline's store interfaces.

`crawl.crawl_site` and `match.run_matching` are the same code for the CLI
and the hosted product; these two classes are the hosted side of the
`CrawlStore` / `MatchStore` protocols (the CLI side is `db.LocalStore`).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

import numpy as np

from nyra import fetch

from . import db as cloud_db
from . import storage as cloud_storage


def _vector(value) -> Optional[np.ndarray]:
    if value is None:
        return None
    if hasattr(value, "to_numpy"):
        return value.to_numpy().astype(np.float32)
    return np.asarray(value, dtype=np.float32)


class CloudCrawlStore:
    def __init__(self, *, org_id: uuid.UUID, site_id: uuid.UUID, database_url: str, storage_client):
        self.org_id = org_id
        self.site_id = site_id
        self.database_url = database_url
        self.storage = storage_client

    def crawled_urls(self) -> set[str]:
        with cloud_db.connect(self.database_url) as conn:
            rows = conn.execute(
                "SELECT url FROM pages WHERE site_id = %s AND status = 'done'", (self.site_id,)
            ).fetchall()
        return {row["url"] for row in rows}

    def upsert_page(self, url: str) -> uuid.UUID:
        with cloud_db.connect(self.database_url) as conn:
            row = conn.execute(
                """INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'pending')
                   ON CONFLICT (site_id, url) DO UPDATE SET url = excluded.url RETURNING id""",
                (self.org_id, self.site_id, url),
            ).fetchone()
        return row["id"]

    def mark_page(self, page_id: uuid.UUID, http_status: int) -> None:
        with cloud_db.connect(self.database_url) as conn:
            conn.execute(
                "UPDATE pages SET status = 'done', http_status = %s, crawled_at = now() WHERE id = %s",
                (http_status, page_id),
            )

    def known_images(self, urls: Sequence[str]) -> dict[str, uuid.UUID]:
        if not urls:
            return {}
        with cloud_db.connect(self.database_url) as conn:
            rows = conn.execute(
                "SELECT id, url FROM site_images WHERE site_id = %s AND phash IS NOT NULL AND url = ANY(%s)",
                (self.site_id, list(urls)),
            ).fetchall()
            if rows:
                conn.execute(
                    "UPDATE site_images SET last_seen = now() WHERE id = ANY(%s)", ([row["id"] for row in rows],)
                )
        return {row["url"]: row["id"] for row in rows}

    def link(self, image_id: uuid.UUID, page_id: uuid.UUID) -> None:
        with cloud_db.connect(self.database_url) as conn:
            cloud_db.link_image_page(conn, image_id, page_id)

    def image_by_content_hash(self, digest: str) -> Optional[dict]:
        with cloud_db.connect(self.database_url) as conn:
            return conn.execute(
                """SELECT storage_path, thumb_path, content_hash, width, height, phash, dhash, embedding
                   FROM site_images WHERE org_id = %s AND content_hash = %s AND phash IS NOT NULL LIMIT 1""",
                (self.org_id, digest),
            ).fetchone()

    def _upsert(self, conn, *, url, storage_path, thumb_path, content_hash, width, height, phash, dhash, embedding):
        row = conn.execute(
            """
            INSERT INTO site_images
                (org_id, site_id, url, storage_path, thumb_path, content_hash, width, height, phash, dhash, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (site_id, url) DO UPDATE SET
                storage_path=excluded.storage_path,
                thumb_path=excluded.thumb_path,
                content_hash=excluded.content_hash,
                width=excluded.width,
                height=excluded.height,
                phash=excluded.phash,
                dhash=excluded.dhash,
                embedding=COALESCE(excluded.embedding, site_images.embedding),
                last_seen=now(),
                compared_at=CASE
                    WHEN site_images.content_hash IS DISTINCT FROM excluded.content_hash
                      OR (excluded.embedding IS NOT NULL AND site_images.embedding IS NULL)
                    THEN NULL ELSE site_images.compared_at END
            RETURNING id
            """,
            (self.org_id, self.site_id, url, storage_path, thumb_path, content_hash, width, height, phash, dhash,
             embedding),
        ).fetchone()
        return row["id"]

    def save_duplicate(self, *, url: str, page_id: uuid.UUID, existing: dict) -> uuid.UUID:
        with cloud_db.connect(self.database_url) as conn:
            image_id = self._upsert(
                conn, url=url, storage_path=existing["storage_path"], thumb_path=existing["thumb_path"],
                content_hash=existing["content_hash"], width=existing["width"], height=existing["height"],
                phash=existing["phash"], dhash=existing["dhash"], embedding=existing["embedding"],
            )
            cloud_db.link_image_page(conn, image_id, page_id)
        return image_id

    def save_image(self, *, url, page_id, data, content_type, processed: fetch.ProcessedImage, embedding) -> uuid.UUID:
        # The original stays on the site: only a working copy and a thumbnail are stored.
        storage_path = cloud_storage.site_work_path(self.org_id, processed.content_hash)
        thumb_path = cloud_storage.site_thumb_path(self.org_id, processed.content_hash)
        cloud_storage.upload(self.storage, cloud_storage.BUCKET_SITE_IMAGES, storage_path,
                             fetch.make_working_copy(processed.rgb), content_type="image/jpeg")
        cloud_storage.upload(self.storage, cloud_storage.BUCKET_SITE_IMAGES, thumb_path, processed.thumbnail,
                             content_type="image/jpeg")
        with cloud_db.connect(self.database_url) as conn:
            image_id = self._upsert(
                conn, url=url, storage_path=storage_path, thumb_path=thumb_path,
                content_hash=processed.content_hash, width=processed.width, height=processed.height,
                phash=processed.phash, dhash=processed.dhash, embedding=embedding,
            )
            cloud_db.link_image_page(conn, image_id, page_id)
        return image_id


class CloudMatchStore:
    """One brand: its library against the images of its own sites, nothing else."""

    def __init__(self, *, org_id: uuid.UUID, brand_id: uuid.UUID, database_url: str, storage_client=None,
                 max_image_pixels: int = 60_000_000):
        self.org_id = org_id
        self.brand_id = brand_id
        self.database_url = database_url
        self.storage = storage_client
        self.max_image_pixels = max_image_pixels
        self._paths: dict[tuple[str, Any], tuple[Optional[str], Optional[str]]] = {}

    def load_features(self, use_clip: bool) -> tuple[list[dict], list[dict]]:
        embedding = "embedding" if use_clip else "NULL::vector AS embedding"
        with cloud_db.connect(self.database_url) as conn:
            refs = conn.execute(
                f"""SELECT id, phash, dhash, phash_flip, dhash_flip, {embedding}, compared_at,
                           COALESCE(work_path, storage_path) AS storage_path
                    FROM reference_images WHERE brand_id = %s""",
                (self.brand_id,),
            ).fetchall()
            sites = conn.execute(
                f"""SELECT si.id, si.phash, si.dhash, {"si." + embedding if use_clip else embedding}, si.compared_at,
                           si.storage_path
                    FROM site_images si JOIN sites s ON s.id = si.site_id WHERE s.brand_id = %s""",
                (self.brand_id,),
            ).fetchall()
        for row in (*refs, *sites):
            row["embedding"] = _vector(row["embedding"])
        self._paths = {**{("ref", row["id"]): (row["storage_path"], row["phash"]) for row in refs},
                       **{("site", row["id"]): (row["storage_path"], row["phash"]) for row in sites}}
        return refs, sites

    def load_image(self, side: str, image_id: Any):
        """The working copy (or, for older rows, the original), for the keypoint check of CLIP
        candidates; None when it can't be read. Read through the worker's disk cache."""
        path, version = self._paths.get((side, image_id), (None, None))
        if self.storage is None or not path:
            return None
        bucket = cloud_storage.BUCKET_REFS if side == "ref" else cloud_storage.BUCKET_SITE_IMAGES
        try:
            data = cloud_storage.cached_download(self.storage, bucket, path, version=version or "")
        except Exception:  # noqa: BLE001 - a missing object leaves the pair "to verify"
            return None
        if side == "ref":
            return fetch.decode_reference(data, self.max_image_pixels)[0]
        return fetch.decode(data, self.max_image_pixels)

    def get_signature(self) -> Optional[str]:
        with cloud_db.connect(self.database_url) as conn:
            return cloud_db.get_match_signature(conn, self.brand_id)

    def load_exclusions(self) -> list[tuple[str, str]]:
        with cloud_db.connect(self.database_url) as conn:
            return cloud_db.load_exclusions(conn, self.brand_id)

    def save_matches(self, *, full, clear_ref_ids, clear_site_ids, hits, signature) -> int:
        with cloud_db.connect(self.database_url) as conn:
            if full:
                cloud_db.clear_matches(conn, self.brand_id)
            else:
                cloud_db.delete_matches_for(conn, org_id=self.org_id, reference_ids=clear_ref_ids, site_ids=clear_site_ids)
            cloud_db.write_matches(conn, self.org_id, hits)
            cloud_db.stamp_compared(conn, clear_ref_ids, clear_site_ids)
            cloud_db.set_match_signature(conn, self.org_id, self.brand_id, signature)
            return cloud_db.count_matches(conn, self.brand_id)


def feature_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "embedding": _vector(row.get("embedding"))}
