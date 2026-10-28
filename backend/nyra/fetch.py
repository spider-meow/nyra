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
import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

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


def _to_8bit(img: Image.Image) -> Image.Image:
    """A 16-bit (`I;16`, `I;16B`...), 32-bit (`I`) or float (`F`) grey image as 8-bit grey. A plain `convert` clips everything above
    255, so a 16-bit scan turns white. 16-bit data keeps its exposure (the value over 256); the others are stretched to their own range."""
    values = np.asarray(img).astype(np.float64)
    if img.mode.startswith("I;16"):
        scaled = values / 256.0
    else:
        low, high = values.min(), values.max()
        scaled = (values - low) / (high - low or 1.0) * 255.0
    return Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), "L")


def _cmyk_to_rgb(img: Image.Image) -> Image.Image:
    """CMYK as sRGB through the file's own colour profile when it has a usable one, else Pillow's plain conversion (duller colours)."""
    profile = img.info.get("icc_profile")
    if profile:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            return ImageCms.profileToProfile(img, source, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):  # a broken or mismatched profile: the plain conversion below
            pass
    return img.convert("RGB")


def normalize_mode(img: Image.Image) -> Image.Image:
    """The image in a mode every later step can `.convert("RGB")` correctly: 16-bit and float greys as 8-bit grey, CMYK as RGB.
    Any other image is returned as it is. The result remembers the format of the file (`source_format`) and `original_size`."""
    if img.mode.startswith("I;16") or img.mode in ("I", "F"):
        out = _to_8bit(img)
    elif img.mode == "CMYK":
        out = _cmyk_to_rgb(img)
    else:
        return img
    out.info["source_format"] = img.format
    if "original_size" in img.info:
        out.info["original_size"] = img.info["original_size"]
    return out


def source_format(img: Image.Image) -> Optional[str]:
    """The format of the file the image was read from (`TIFF`, `JPEG`...), kept through `normalize_mode`."""
    return img.info.get("source_format") or img.format


def decode(data: bytes, max_pixels: int) -> Optional[Image.Image]:
    """Open and fully load an image, or None if it's unreadable or too large."""
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            img.load()
        return normalize_mode(img)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def decode_reference(data: bytes, max_pixels: int) -> tuple[Optional[Image.Image], str]:
    """A library image, or None and why not, in words an admin can act on.

    Only hashes, a CLIP embedding and a thumbnail come out of a reference,
    so a JPEG above `max_pixels` (a studio export) is decoded at a reduced
    scale instead of being refused; its real size is kept in
    `img.info["original_size"]`. Other formats can't be decoded smaller and
    stay capped.
    """
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None  # the size is checked here, before anything is decoded
    try:
        try:
            img = Image.open(io.BytesIO(data))
        except UnidentifiedImageError:
            return None, "Format non reconnu : le fichier n'est pas une image lisible (JPEG, PNG, WebP…)."
        width, height = img.size
        if width * height > max_pixels:
            if img.format != "JPEG":
                return None, (
                    f"Image trop grande : {width} × {height} px, soit {width * height / 1e6:.0f} Mpx "
                    f"({max_pixels / 1e6:.0f} Mpx au plus pour un {img.format or 'fichier de ce format'}). "
                    "Réduisez-la ou exportez-la en JPEG, puis renvoyez-la."
                )
            # JPEG decodes at 1/2, 1/4 or 1/8 scale: the first one under the cap.
            scale = next((s for s in (2, 4, 8) if (width // s) * (height // s) <= max_pixels), 8)
            img.draft("RGB", (width // scale, height // scale))
        img.load()
        img.info["original_size"] = (width, height)
        return normalize_mode(img), ""
    except OSError:
        return None, "Fichier incomplet ou endommagé : l'image ne se lit pas jusqu'au bout. Réexportez-la puis renvoyez-la."
    except (ValueError, Image.DecompressionBombError):
        return None, "Image illisible : son contenu ne correspond pas à son format."
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


WORKING_SIDE = 1024


def make_working_copy(img: Image.Image, side: int = WORKING_SIDE) -> bytes:
    """What the pipeline reads instead of an original: JPEG, at most `side` px on the long side.

    Enough for CLIP (224 px), the keypoint check (1024 px) and the comparison
    screen, at a fraction of the original's weight.
    """
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    rgb.thumbnail((side, side))
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


STORED_SIDE = 2048
STORED_BYTES = 1_500_000


def make_stored_original(img: Image.Image, data: bytes, content_type: Optional[str]) -> tuple[bytes, Optional[str]]:
    """What is kept of a reference's file: the file itself when it is light, else a copy at most `STORED_SIDE` px.

    A 15 MB studio export is not worth its weight: matching reads the 1024 px working copy, and the
    edit screen only shows the image. JPEG, or PNG when there is transparency to keep. Animations and
    anything the copy would not make lighter stay as sent. A TIFF is always converted, light or not: browsers do not
    display it (the first page is kept).
    """
    web_copy = source_format(img) == "TIFF"
    if not web_copy and ((max(img.size) <= STORED_SIDE and len(data) <= STORED_BYTES) or getattr(img, "n_frames", 1) > 1):
        return data, content_type
    flat = ImageOps.exif_transpose(img)
    alpha = flat.mode in ("RGBA", "LA", "PA") or "transparency" in flat.info
    flat = flat.convert("RGBA" if alpha else "RGB")
    flat.thumbnail((STORED_SIDE, STORED_SIDE))
    buf = io.BytesIO()
    if alpha:
        flat.save(buf, format="PNG", optimize=True)
    else:
        flat.save(buf, format="JPEG", quality=88, optimize=True)
    if not web_copy and len(buf.getvalue()) >= len(data):
        return data, content_type
    return buf.getvalue(), "image/png" if alpha else "image/jpeg"


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
    except (httpx.HTTPError, ValueError):  # ValueError: a malformed address ("unknown url type"); skip that one image
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
    except (httpx.HTTPError, ValueError):  # ValueError: a malformed address ("unknown url type"); skip that one image
        return None
    if not data:
        return None
    return data, content_type
