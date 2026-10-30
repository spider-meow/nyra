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
from typing import BinaryIO, Optional, Union

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


_BAND_VALUES = 4_000_000  # values converted at a time: 32 MB of float64


def _to_8bit(img: Image.Image) -> Image.Image:
    """A 16-bit (`I;16`, `I;16B`...), 32-bit (`I`) or float (`F`) grey image as 8-bit grey. A plain `convert` clips everything above
    255, so a 16-bit scan turns white. 16-bit data keeps its exposure (the value over 256); the others are stretched to their own range.
    Done a band of rows at a time: the same operations on each value, but never the three 8-byte-a-pixel arrays of the whole image (780 MB for 30 Mpx)."""
    values = np.asarray(img)
    sixteen = img.mode.startswith("I;16")
    if not sixteen:
        low, high = np.float64(values.min()), np.float64(values.max())
    out = np.empty(values.shape, dtype=np.uint8)
    band = max(1, _BAND_VALUES // max(values.shape[1], 1))
    for start in range(0, values.shape[0], band):
        part = values[start:start + band].astype(np.float64)
        scaled = part / 256.0 if sixteen else (part - low) / (high - low or 1.0) * 255.0
        out[start:start + band] = np.clip(scaled, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "L")


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


Source = Union[bytes, BinaryIO]  # a file's bytes, or the file itself (an upload spooled to disk): a heavy TIFF is never held in memory as bytes


def _opened(data: Source) -> BinaryIO:
    """The source as a file to read from the start: bytes in memory, or the spooled file rewound (Pillow reads what it needs from it)."""
    if isinstance(data, (bytes, bytearray)):
        return io.BytesIO(data)
    data.seek(0)
    return data


def sha256_of(data: Source) -> str:
    """The SHA-256 of a file, whole or read in 1 MiB pieces from its start: the same value as `hashlib.sha256(bytes).hexdigest()`."""
    if isinstance(data, (bytes, bytearray)):
        return hashlib.sha256(data).hexdigest()
    data.seek(0)
    digest = hashlib.sha256()
    for piece in iter(lambda: data.read(1 << 20), b""):
        digest.update(piece)
    return digest.hexdigest()


def decode_reference(data: Source, max_pixels: int) -> tuple[Optional[Image.Image], str]:
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
            img = Image.open(_opened(data))
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


def _turned(img: Image.Image, mode: str = "RGB") -> Image.Image:
    """A copy of the image upright, in `mode`. `exif_transpose` already returns a copy: converting it to the mode it is in would copy it a
    second time (a 60 Mpx image is 180 MB each). The caller may change the result, which is never the image itself."""
    turned = ImageOps.exif_transpose(img)
    return turned if turned.mode == mode else turned.convert(mode)


def make_thumbnail(img: Image.Image, size: int = THUMB_SIZE) -> bytes:
    rgb = _turned(img)
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
    rgb = _turned(img)
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
    mode = "RGBA" if alpha else "RGB"
    if flat.mode != mode:
        flat = flat.convert(mode)
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
    rgb: Image.Image  # kept in memory only, for the embedding step: at most `WORKING_SIDE` px on its long side


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
    phash, dhash = compute_hashes(img)  # of the full image, as before; the embedding now reads the working size (see below)
    processed = ProcessedImage(
        content_hash=content_hash(data),
        width=img.size[0],
        height=img.size[1],
        phash=phash,
        dhash=dhash,
        thumbnail=make_thumbnail(img),
        extension=extension_for(content_type, img),
        rgb=img if img.mode == "RGB" else img.convert("RGB"),
    )
    # Only the embedding and the working copy are cut from `rgb` (CLIP reads 224 px, the working copy is `WORKING_SIDE`): keep that
    # size, not the 3 bytes a pixel of the full image, which a page of large banners made 10 to 17 MiB a megapixel (AXE-643).
    processed.rgb.thumbnail((WORKING_SIDE, WORKING_SIDE))
    return processed


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
