"""Downloading a library image at a chosen size (AXE-677): the sizes offered, and the file made on the fly.

Nothing is stored for a download: the menu is worked out from the row (`width`, `height`, `byte_size`) and the
smaller sizes are cut from the stored original when asked.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from .. import fetch

ORIGINAL = "original"
# (key, label, long side in px), largest first. An image is never enlarged: a size not under the original's long side is not offered.
STEPS = (("large", "Grand", 2000), ("medium", "Moyen", 1200), ("web", "Web", 800))
JPEG_QUALITY = 90
WEBP_QUALITY = 90
# Pillow saves these as they are; any other format (a GIF, a TIFF kept as sent...) is saved as PNG.
SAVED_AS = {"JPEG": ("JPEG", ".jpg", "image/jpeg"), "PNG": ("PNG", ".png", "image/png"), "WEBP": ("WEBP", ".webp", "image/webp")}


def _scaled(width: int, height: int, side: int) -> tuple[int, int]:
    """`width` × `height` with the long side brought to `side`, same ratio (the same rounding as `Image.thumbnail`)."""
    ratio = side / max(width, height)
    return max(1, round(width * ratio)), max(1, round(height * ratio))


def choices(width: Optional[int], height: Optional[int], byte_size: Optional[int]) -> list[dict]:
    """The sizes offered for an image of `width` × `height` px, the original first.

    The stored original is at most `fetch.STORED_SIDE` px on its long side (a bigger file was reduced on arrival), so that is
    what « Original » is. The others are listed only when smaller than it; `byte_size` is the original's, the others have none.
    """
    if not (width and height):  # a row from before the size was kept: the file as it is, nothing to compute the others from
        return [{"key": ORIGINAL, "label": "Original", "width": None, "height": None, "bytes": byte_size}]
    long_side = min(max(width, height), fetch.STORED_SIDE)
    stored_width, stored_height = _scaled(width, height, long_side)
    offered = [{"key": ORIGINAL, "label": "Original", "width": stored_width, "height": stored_height, "bytes": byte_size}]
    for key, label, side in STEPS:
        if side < long_side:
            step_width, step_height = _scaled(width, height, side)
            offered.append({"key": key, "label": label, "width": step_width, "height": step_height, "bytes": None})
    return offered


def known_size(key: str) -> Optional[int]:
    """The long side of a size key (`None` for the original); raises KeyError for a key that is not offered at all."""
    if key == ORIGINAL:
        return None
    return {name: side for name, _, side in STEPS}[key]


def download_name(filename: str, key: str, width: int, extension: str) -> str:
    """`photo_1200w.jpg`: the stored name without its extension, the width made, the format's extension. Only letters, digits, `.`, `_`, `-` and `()`; the rest becomes `_`."""
    stem = re.sub(r"[^\w.()\-]+", "_", Path(filename).stem, flags=re.ASCII).strip("._") or "image"
    return f"{stem}{'' if key == ORIGINAL else f'_{width}w'}{extension}"


def resized(img: Image.Image, side: int) -> tuple[bytes, int, str, str]:
    """(bytes, width, extension, content type) of `img` with its long side at `side` px, in the format of the file it was read from.

    LANCZOS, the colour profile and the orientation kept (the pixels are turned upright, no EXIF is needed after), JPEG at quality 90
    optimized and progressive, PNG optimized with its transparency. The caller holds the image-processing place (`heavy_image`).
    """
    fmt, extension, content_type = SAVED_AS.get(fetch.source_format(img) or "", SAVED_AS["PNG"])
    profile = img.info.get("icc_profile")
    upright = ImageOps.exif_transpose(img)
    if fmt == "JPEG" and upright.mode != "RGB":
        upright = upright.convert("RGB")
    if side < max(upright.size):
        upright.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    extras = {"JPEG": {"quality": JPEG_QUALITY, "optimize": True, "progressive": True}, "PNG": {"optimize": True}, "WEBP": {"quality": WEBP_QUALITY, "method": 4}}[fmt]
    upright.save(buf, format=fmt, **({"icc_profile": profile} if profile else {}), **extras)
    return buf.getvalue(), upright.size[0], extension, content_type
