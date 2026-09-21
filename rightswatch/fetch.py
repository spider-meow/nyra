"""Downloading, normalizing, and caching images discovered while crawling.

One function, `fetch_and_store`, is the whole public surface used by
crawl.py: given an image URL it downloads (unless already cached), filters
out anything too small to be a real content image, computes hashes (and
optionally a CLIP embedding), stores the bytes on disk, and upserts the row
in `site_images`. It's idempotent on URL so re-crawls don't re-download.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, UnidentifiedImageError

from rightswatch import db
from rightswatch.config import Config
from rightswatch.match import compute_hashes, compute_clip_embedding


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_path_for(cache_dir: Path, digest: str, content_type: Optional[str]) -> Path:
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }.get((content_type or "").split(";")[0].strip().lower(), ".bin")
    return cache_dir / digest[:2] / f"{digest}{ext}"


def download(url: str, client: httpx.Client, timeout: float) -> Optional[tuple[bytes, Optional[str]]]:
    try:
        resp = client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return resp.content, resp.headers.get("content-type")


def meets_min_size(img: Image.Image, min_side_px: int) -> bool:
    width, height = img.size
    return min(width, height) >= min_side_px


def fetch_and_store(
    conn,
    *,
    url: str,
    page_id: int,
    cache_dir: Path,
    client: httpx.Client,
    config: Config,
    compute_embeddings: bool = True,
) -> Optional[int]:
    """Fetch (or reuse) a site image, filter by size, hash it, and link it to a page.

    Returns (site_images.id, is_new). is_new is false when the URL was already
    hashed. The id is None when the image was skipped (too small, unreachable,
    or unreadable).
    """
    existing = conn.execute("SELECT * FROM site_images WHERE url = ?", (url,)).fetchone()
    if existing is not None and existing["phash"] is not None:
        db.link_image_page(conn, existing["id"], page_id)
        return existing["id"], False

    fetched = download(url, client, timeout=config.crawl.request_timeout_seconds)
    if fetched is None:
        return None, False
    data, content_type = fetched

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except UnidentifiedImageError:
        return None, False
    except OSError:
        return None, False

    if not meets_min_size(img, config.crawl.min_image_side_px):
        return None, False

    digest = content_hash(data)
    local_path = cache_path_for(cache_dir, digest, content_type)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.exists():
        local_path.write_bytes(data)

    rgb = img.convert("RGB")
    phash, dhash = compute_hashes(rgb)
    embedding = compute_clip_embedding(rgb, config.match) if compute_embeddings else None

    image_id = db.upsert_site_image(
        conn,
        url=url,
        local_path=str(local_path),
        content_hash=digest,
        width=img.size[0],
        height=img.size[1],
        phash=phash,
        dhash=dhash,
        embedding=embedding,
    )
    db.link_image_page(conn, image_id, page_id)
    return image_id, True
