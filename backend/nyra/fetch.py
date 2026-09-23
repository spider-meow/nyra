"""Downloading and normalizing images found while crawling.

Everything that turns raw bytes into what the pipeline stores lives here:
a size-capped download (through `netguard`, so it can't reach private
addresses), a decoder that refuses decompression bombs, the size filter
that drops icons and tracking pixels, the perceptual hashes, and a small
JPEG thumbnail that the interface and reports show instead of the
original. Persistence is the caller's job (see `crawl.CrawlStore`).
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Optional

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from nyra import netguard
from nyra.match import compute_hashes

THUMB_SIZE = 320

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
}


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extension_for(content_type: Optional[str], image: Optional[Image.Image] = None) -> str:
    ext = _EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())
    if ext:
        return ext
    if image is not None and image.format:
        return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(image.format, ".bin")
    return ".bin"


def decode(data: bytes, max_pixels: int) -> Optional[Image.Image]:
    """Open and fully load an image, or None if it's unreadable or too large."""
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
        return img
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def meets_min_size(img: Image.Image, min_side_px: int) -> bool:
    width, height = img.size
    return min(width, height) >= min_side_px


def make_thumbnail(img: Image.Image, size: int = THUMB_SIZE) -> bytes:
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    rgb.thumbnail((size, size))
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=78, optimize=True)
    return buf.getvalue()


@dataclass
class ProcessedImage:
    content_hash: str
    width: int
    height: int
    phash: str
    dhash: str
    thumbnail: bytes
    extension: str
    rgb: Image.Image  # kept in memory only, for the embedding step


def process_image(
    data: bytes,
    content_type: Optional[str],
    *,
    min_side_px: int,
    max_pixels: int,
) -> Optional[ProcessedImage]:
    """Decode, filter and hash one downloaded image. None means "skip it"."""
    img = decode(data, max_pixels)
    if img is None or not meets_min_size(img, min_side_px):
        return None
    rgb = img.convert("RGB")
    phash, dhash = compute_hashes(rgb)
    return ProcessedImage(
        content_hash=content_hash(data),
        width=img.size[0],
        height=img.size[1],
        phash=phash,
        dhash=dhash,
        thumbnail=make_thumbnail(img),
        extension=extension_for(content_type, img),
        rgb=rgb,
    )


def download(url: str, client: httpx.Client, *, timeout: float, max_bytes: int) -> Optional[tuple[bytes, Optional[str]]]:
    """Body and content type, or None on any HTTP error, refusal, or oversize body."""
    try:
        with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return None
            data = netguard.read_capped(resp, max_bytes)
            content_type = resp.headers.get("content-type")
    except httpx.HTTPError:
        return None
    if not data:
        return None
    return data, content_type


async def adownload(
    url: str, client: httpx.AsyncClient, *, timeout: float, max_bytes: int
) -> Optional[tuple[bytes, Optional[str]]]:
    try:
        async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return None
            data = await netguard.aread_capped(resp, max_bytes)
            content_type = resp.headers.get("content-type")
    except httpx.HTTPError:
        return None
    if not data:
        return None
    return data, content_type
