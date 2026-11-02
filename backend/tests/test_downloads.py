"""Downloading a library image at a chosen size (AXE-677): the sizes offered, the file made, and the route."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from nyra.cloud import downloads


def _jpeg(width: int, height: int, **save) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((width, height), 40).convert("RGB").save(buf, format="JPEG", **save)
    return buf.getvalue()


def _sizes(offered):
    return [(o["key"], o["width"], o["height"]) for o in offered]


def test_sizes_never_enlarge_and_start_from_what_is_stored():
    # 4000 x 6000 on arrival is stored at 2048 on its long side: that is the original that can be given.
    assert _sizes(downloads.choices(4000, 6000, 3_200_000)) == [
        ("original", 1365, 2048), ("large", 1333, 2000), ("medium", 800, 1200), ("web", 533, 800)]
    assert _sizes(downloads.choices(1500, 1000, 90_000)) == [("original", 1500, 1000), ("medium", 1200, 800), ("web", 800, 533)]
    assert _sizes(downloads.choices(800, 600, 1)) == [("original", 800, 600)]  # a small image has only « Original »: 800 is not under 800
    assert _sizes(downloads.choices(None, None, 10)) == [("original", None, None)]  # a row from before the size was kept
    assert downloads.choices(1500, 1000, 90_000)[0]["bytes"] == 90_000 and downloads.choices(1500, 1000, 1)[1]["bytes"] is None


def test_a_download_name_has_no_dangerous_character():
    assert downloads.download_name("photo été (1).jpg", "medium", 1200, ".jpg") == "photo_t_(1)_1200w.jpg"
    assert downloads.download_name("a.jpg", "original", 0, ".jpg") == "a.jpg"
    assert downloads.download_name('..";x.jpg', "web", 800, ".png") == "x_800w.png"


@pytest.mark.parametrize("fmt, mode, kept", [("JPEG", "RGB", "image/jpeg"), ("PNG", "RGBA", "image/png"), ("WEBP", "RGB", "image/webp")])
def test_the_format_of_the_file_and_its_transparency_are_kept(fmt, mode, kept):
    source = Image.new(mode, (3000, 2000), (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    buf = io.BytesIO()
    source.save(buf, format=fmt)
    img = Image.open(io.BytesIO(buf.getvalue()))
    img.load()
    data, width, extension, content_type = downloads.resized(img, 1200)
    out = Image.open(io.BytesIO(data))
    assert (out.format, out.size, width, content_type) == (fmt, (1200, 800), 1200, kept) and extension.lstrip(".") in (fmt.lower(), "jpg")
    assert out.mode == mode


def test_the_profile_and_the_orientation_are_kept_and_nothing_is_enlarged():
    profile = b"profile-bytes"
    img = Image.new("RGB", (1000, 2000))
    exif = Image.Exif()
    exif[0x0112] = 6  # turned a quarter: upright it is 2000 wide
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif, icc_profile=profile)
    loaded = Image.open(io.BytesIO(buf.getvalue()))
    loaded.load()
    data, width, _, _ = downloads.resized(loaded, 5000)  # asked bigger than it is: left alone, only turned upright
    out = Image.open(io.BytesIO(data))
    assert (width, out.size) == (2000, (2000, 1000)) and out.info.get("icc_profile") == profile
