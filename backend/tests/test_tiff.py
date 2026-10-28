"""TIFF references (AXE-620, AXE-621): a TIFF is stored as a picture a browser shows, and 16-bit greys, CMYK and multipage files are read right."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from nyra import fetch
from nyra.refs import reference_features


def _tiff(img: Image.Image, **options) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="TIFF", **options)
    return buf.getvalue()


def _ramp16(width: int = 300, height: int = 200) -> bytes:
    """A 16-bit grey ramp, black on the left to white on the right."""
    ramp = np.tile(np.linspace(0, 65535, width).astype("uint16"), (height, 1))
    return _tiff(Image.fromarray(ramp))


def _read(data: bytes):
    img, why = fetch.decode_reference(data, 50_000_000)
    assert img is not None, why
    return img


def test_a_16_bit_grey_tiff_is_not_read_as_white():
    img = _read(_ramp16())
    assert img.mode == "L"
    left, right = img.getpixel((2, 100)), img.getpixel((297, 100))
    assert left < 10 and right > 245  # clipping at 255 made every pixel white
    thumb = Image.open(io.BytesIO(fetch.make_thumbnail(img))).convert("RGB")
    assert thumb.getpixel((2, thumb.height // 2))[0] < 40 and thumb.getpixel((thumb.width - 3, thumb.height // 2))[0] > 215
    work = Image.open(io.BytesIO(fetch.make_working_copy(img))).convert("L")
    assert work.getpixel((2, work.height // 2)) < 40 and work.getpixel((work.width - 3, work.height // 2)) > 215


def test_the_hashes_of_a_16_bit_tiff_match_those_of_its_8_bit_jpeg():
    ramp8 = Image.fromarray(np.tile(np.linspace(0, 255, 300).astype("uint8"), (200, 1)))
    jpeg = io.BytesIO()
    ramp8.save(jpeg, format="JPEG", quality=95)
    from_tiff = reference_features(_read(_ramp16()))
    from_jpeg = reference_features(_read(jpeg.getvalue()))
    different = reference_features(_read(_tiff(Image.fromarray(np.tile(np.linspace(65535, 0, 300).astype("uint16"), (200, 1))))))
    distance = lambda a, b: bin(int(a, 16) ^ int(b, 16)).count("1")  # noqa: E731 - a one-line helper for this test
    assert distance(from_tiff.phash, from_jpeg.phash) <= 6 and distance(from_tiff.dhash, from_jpeg.dhash) <= 6
    assert distance(from_tiff.dhash, different.dhash) > 20  # the reversed ramp is another picture


def test_every_16_bit_and_float_grey_mode_ends_as_8_bit_grey():
    images = {
        "I;16": Image.frombytes("I;16", (2, 1), np.array([0, 65535], dtype="<u2").tobytes()),
        "I;16B": Image.frombytes("I;16B", (2, 1), np.array([0, 65535], dtype=">u2").tobytes()),
        "I": Image.fromarray(np.array([[-1000, 5000]], dtype="int32")),
        "F": Image.fromarray(np.array([[0.0, 1.0]], dtype="float32")),
    }
    for mode, img in images.items():
        assert img.mode == mode
        out = fetch.normalize_mode(img)
        assert out.mode == "L" and out.getpixel((0, 0)) <= 1 and out.getpixel((1, 0)) >= 254, mode


def test_a_cmyk_tiff_becomes_rgb_with_or_without_a_usable_profile():
    cyan = Image.new("CMYK", (300, 200), (255, 0, 0, 0))
    plain = _read(_tiff(cyan))
    assert plain.mode == "RGB" and plain.getpixel((10, 10))[2] > plain.getpixel((10, 10))[0]  # cyan: more blue than red
    cyan.info["icc_profile"] = b"not a profile"  # a broken profile must not stop the import
    assert fetch.normalize_mode(cyan).mode == "RGB"


def test_a_multipage_tiff_keeps_its_first_page():
    first, second = Image.new("RGB", (300, 200), (255, 0, 0)), Image.new("RGB", (300, 200), (0, 255, 0))
    data = _tiff(first, save_all=True, append_images=[second])
    img = _read(data)
    kept, kind = fetch.make_stored_original(img, data, "image/tiff")
    shown = Image.open(io.BytesIO(kept)).convert("RGB")
    assert kind == "image/jpeg" and shown.getpixel((10, 10))[0] > 200 and shown.getpixel((10, 10))[1] < 60  # the red page


def test_a_tiff_is_always_stored_as_a_picture_a_browser_shows():
    small = _tiff(Image.new("RGB", (300, 200), (200, 30, 30)))
    kept, kind = fetch.make_stored_original(_read(small), small, "image/tiff")
    assert kind == "image/jpeg" and Image.open(io.BytesIO(kept)).format == "JPEG"
    assert fetch.make_stored_original(Image.open(io.BytesIO(small)), small, "image/tiff")[1] == "image/jpeg"  # also from a file just opened
    transparent = _tiff(Image.new("RGBA", (300, 200), (0, 0, 0, 0)))
    kept, kind = fetch.make_stored_original(_read(transparent), transparent, "image/tiff")
    assert kind == "image/png" and Image.open(io.BytesIO(kept)).format == "PNG"
    heavy = _tiff(Image.new("RGB", (3000, 2000), (20, 120, 200)))
    kept, kind = fetch.make_stored_original(_read(heavy), heavy, "image/tiff")
    assert kind == "image/jpeg" and max(Image.open(io.BytesIO(kept)).size) <= fetch.STORED_SIDE
    grey16 = _ramp16()
    kept, kind = fetch.make_stored_original(_read(grey16), grey16, "image/tiff")
    assert kind == "image/jpeg"  # a 16-bit grey tiff keeps its source format through the 8-bit conversion


def test_other_light_files_are_still_stored_as_sent():
    png = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 20, 30)).save(png, format="PNG")
    assert fetch.make_stored_original(_read(png.getvalue()), png.getvalue(), "image/png") == (png.getvalue(), "image/png")
