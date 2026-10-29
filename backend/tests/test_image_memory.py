"""AXE-639: what one uploaded image costs the `web` process. The copies an image is cut from are the memory (a 60 Mpx image is 240 MB each), so
they are cut to the minimum, WITHOUT changing a byte of what comes out: the hashes, the thumbnail, the working copy and the stored original are
those of before. The expected values below are the definitions (the hashes of the RGB image and of its mirror), not a recording of the code."""

from __future__ import annotations

import io
import logging
import threading
import time

import imagehash
import numpy as np
import pytest
from PIL import Image, ImageOps

from nyra import fetch
from nyra.cloud.routes import common
from nyra.config import load_config
from nyra.match import _rgb, compute_dhash, compute_flip_hashes, compute_hashes, compute_phash
from nyra.refs import reference_features


def _picture(width: int = 400, height: int = 300) -> Image.Image:
    """Not a flat image: gradients, two blocks and noise, so that every hash bit means something."""
    rng = np.random.default_rng(3)
    y, x = np.mgrid[0:height, 0:width]
    pixels = np.stack([x * 255 // width, y * 255 // height, (x + y) * 255 // (width + height)], axis=2).astype(np.int16)
    pixels[height // 4: height // 2, width // 3: width // 2] = (250, 20, 20)
    pixels[height // 2:, : width // 4] = (10, 200, 90)
    return Image.fromarray(np.clip(pixels + rng.integers(-15, 15, pixels.shape), 0, 255).astype(np.uint8))


def _in_every_mode() -> dict[str, Image.Image]:
    rgb = _picture()
    return {"RGB": rgb, "L": rgb.convert("L"), "P": rgb.convert("P"), "1": rgb.convert("1"), "CMYK": rgb.convert("CMYK"),
            "LA": Image.merge("LA", (rgb.convert("L"), Image.new("L", rgb.size, 180))),
            "RGBA": Image.merge("RGBA", (*rgb.split(), Image.new("L", rgb.size, 180)))}


@pytest.mark.parametrize("mode", ["RGB", "L", "P", "1", "CMYK", "LA", "RGBA"])
def test_the_hashes_are_those_of_the_rgb_image_and_of_its_mirror(mode):
    img = _in_every_mode()[mode]
    rgb = img.convert("RGB")
    mirror = ImageOps.mirror(rgb)
    expected = (str(imagehash.phash(rgb)), str(imagehash.dhash(rgb)), str(imagehash.phash(mirror)), str(imagehash.dhash(mirror)))
    assert compute_hashes(img) == expected[:2]
    assert compute_flip_hashes(img) == expected[2:]
    features = reference_features(img)
    assert (features.phash, features.dhash, features.phash_flip, features.dhash_flip) == expected
    assert (compute_phash(rgb), compute_dhash(rgb)) == expected[:2]


def _turned_upright(img: Image.Image, orientation: int) -> Image.Image:
    img = img.copy()
    exif = img.getexif()
    exif[0x0112] = orientation  # what a camera writes: 1 as shot, 6 and 8 a quarter turn, 3 upside down
    return img


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _thumbnail_as_it_was(img: Image.Image) -> bytes:
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    rgb.thumbnail((fetch.THUMB_SIZE, fetch.THUMB_SIZE))
    return _jpeg(rgb, 78)


def _working_copy_as_it_was(img: Image.Image) -> bytes:
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    rgb.thumbnail((fetch.WORKING_SIDE, fetch.WORKING_SIDE))
    return _jpeg(rgb, 85)


def _stored_original_as_it_was(img: Image.Image, data: bytes):
    flat = ImageOps.exif_transpose(img)
    alpha = flat.mode in ("RGBA", "LA", "PA") or "transparency" in flat.info
    flat = flat.convert("RGBA" if alpha else "RGB")
    flat.thumbnail((fetch.STORED_SIDE, fetch.STORED_SIDE))
    buf = io.BytesIO()
    if alpha:
        flat.save(buf, format="PNG", optimize=True)
    else:
        flat.save(buf, format="JPEG", quality=88, optimize=True)
    if len(buf.getvalue()) >= len(data):
        return data, "image/png"
    return buf.getvalue(), "image/png" if alpha else "image/jpeg"


@pytest.mark.parametrize("orientation", [1, 3, 6, 8])
@pytest.mark.parametrize("mode", ["RGB", "L", "P", "1", "CMYK", "LA", "RGBA"])
def test_the_thumbnail_the_working_copy_and_the_stored_original_are_byte_for_byte_those_of_before(mode, orientation):
    img = _turned_upright(_in_every_mode()[mode], orientation)
    assert fetch.make_thumbnail(img) == _thumbnail_as_it_was(img)
    assert fetch.make_working_copy(img) == _working_copy_as_it_was(img)
    heavy = b"x" * (fetch.STORED_BYTES + 1)  # a heavy file: the copy is cut (a light one is kept as it was sent)
    assert fetch.make_stored_original(img, heavy, "image/png") == _stored_original_as_it_was(img, heavy)


@pytest.mark.parametrize("mode", ["RGB", "L", "P", "CMYK", "RGBA"])
def test_nothing_cut_from_an_image_changes_it_and_an_rgb_one_is_not_copied_to_be_hashed(mode):
    for orientation in (1, 6):  # nothing to turn, and a quarter turn
        img = _turned_upright(_in_every_mode()[mode], orientation)
        before = (img.tobytes(), img.size, img.mode)
        assert fetch._turned(img) is not img  # a copy, which `thumbnail` is free to shrink in place (relies on Pillow's exif_transpose, which copies)
        fetch.make_thumbnail(img)
        fetch.make_working_copy(img)
        fetch.make_stored_original(img, b"x" * (fetch.STORED_BYTES + 1), "image/jpeg")
        reference_features(img)
        compute_flip_hashes(img)
        assert (img.tobytes(), img.size, img.mode) == before, (mode, orientation)
    assert _rgb(_in_every_mode()["RGB"]) is not None and _rgb(_in_every_mode()["L"]).mode == "RGB"
    rgb = _in_every_mode()["RGB"]
    assert _rgb(rgb) is rgb  # the point: no copy of what is already RGB


def test_both_routes_that_decode_a_library_image_hold_the_lock_and_the_pixel_limit():
    """A route that lost its `with heavy_image()` would pass every test above and bring the container down again."""
    import inspect

    from nyra.cloud.routes import library, site_images

    for route in (library._store_upload, site_images._adopt_one):
        source = inspect.getsource(route)
        assert "with heavy_image():" in source and "upload_pixel_limit(config)" in source, route.__name__


def test_the_pixel_limit_of_an_upload(monkeypatch, caplog):
    config = load_config()
    monkeypatch.delenv("NYRA_UPLOAD_MAX_PIXELS", raising=False)
    assert common.upload_pixel_limit(config) == common.UPLOAD_MAX_PIXELS == 25_000_000
    monkeypatch.setenv("NYRA_UPLOAD_MAX_PIXELS", "32000000")
    assert common.upload_pixel_limit(config) == 32_000_000
    monkeypatch.setenv("NYRA_UPLOAD_MAX_PIXELS", "900000000")
    assert common.upload_pixel_limit(config) == config.crawl.max_image_pixels  # never above the organization's own limit
    for bad in ("beaucoup", "0", "-5", "999999"):
        monkeypatch.setenv("NYRA_UPLOAD_MAX_PIXELS", bad)
        with caplog.at_level(logging.WARNING, logger="nyra.api"):
            assert common.upload_pixel_limit(config) == common.UPLOAD_MAX_PIXELS, bad
        assert "NYRA_UPLOAD_MAX_PIXELS" in caplog.text
        caplog.clear()


def test_above_the_limit_a_jpeg_is_reduced_and_any_other_format_is_refused_with_the_reason():
    wide = _picture(2400, 1600)  # 3.8 Mpx
    png, jpeg = io.BytesIO(), io.BytesIO()
    wide.save(png, format="PNG")
    wide.save(jpeg, format="JPEG", quality=90)
    limit = 1_000_000
    refused_image, why = fetch.decode_reference(png.getvalue(), limit)
    assert refused_image is None and "Image trop grande" in why and "2400 × 1600" in why and "PNG" in why
    reduced, why = fetch.decode_reference(jpeg.getvalue(), limit)
    assert reduced is not None and why == "" and reduced.size[0] * reduced.size[1] <= limit
    assert reduced.info["original_size"] == (2400, 1600)  # its real size is kept
    whole, _ = fetch.decode_reference(png.getvalue(), 4_000_000)
    assert whole is not None and whole.size == (2400, 1600)


def test_one_heavy_image_is_processed_at_a_time():
    inside, most, lock = 0, 0, threading.Lock()

    def work():
        nonlocal inside, most
        with common.heavy_image():
            with lock:
                inside += 1
                most = max(most, inside)
            time.sleep(0.02)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads) and most == 1


def _old_to_8bit(img: Image.Image) -> Image.Image:
    """The conversion as it was written before the bands (AXE-639): the definition the bands must give back."""
    values = np.asarray(img).astype(np.float64)
    if img.mode.startswith("I;16"):
        scaled = values / 256.0
    else:
        low, high = values.min(), values.max()
        scaled = (values - low) / (high - low or 1.0) * 255.0
    return Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), "L")


def test_the_16_bit_and_float_greys_are_converted_by_bands_to_the_same_pixels(monkeypatch):
    rng = np.random.default_rng(5)
    height, width = 301, 257  # not a multiple of the band: the last band is a short one
    images = {
        "I;16": Image.fromarray(rng.integers(0, 65536, (height, width), dtype=np.uint16)),
        "I": Image.fromarray(rng.integers(-5000, 70000, (height, width), dtype=np.int32)),
        "F": Image.fromarray(rng.normal(0.4, 3.0, (height, width)).astype(np.float32)),
        "flat F": Image.fromarray(np.full((height, width), 7.5, dtype=np.float32)),  # high == low: nothing to stretch
    }
    for band_values in (4_000_000, 1000, 257, 1):  # one band, several, one row, and a band smaller than a row
        monkeypatch.setattr(fetch, "_BAND_VALUES", band_values)
        for name, img in images.items():
            assert fetch._to_8bit(img).tobytes() == _old_to_8bit(img).tobytes(), (name, band_values)
            assert fetch._to_8bit(img).size == img.size


# --- AXE-630: a TIFF may weigh more than the other formats, and is never held in memory as bytes -----------------------------------------

def _tiff_bytes(width: int = 600, height: int = 400) -> bytes:
    buf = io.BytesIO()
    _picture(width, height).save(buf, format="TIFF")  # uncompressed: 3 bytes a pixel
    return buf.getvalue()


def _spooled(content: bytes, name: str):
    """What the multipart parser gives a route: a file spooled to disk past 1 MiB, seekable, positioned wherever it was left."""
    import tempfile

    from starlette.datastructures import UploadFile

    spool = tempfile.SpooledTemporaryFile(max_size=1_000_000)
    spool.write(content)
    spool.seek(len(content) // 2)  # not at the start: whatever reads it must rewind
    return UploadFile(file=spool, filename=name, size=len(content))


def test_the_hash_and_the_decoding_of_a_file_are_those_of_its_bytes():
    data = _tiff_bytes()
    for content in (data, b"", b"x", b"y" * (3 * (1 << 20) + 7)):  # empty, tiny, and over three 1 MiB pieces
        assert fetch.sha256_of(_spooled(content, "a.tif").file) == fetch.sha256_of(content)
    from_bytes, why = fetch.decode_reference(data, 25_000_000)
    from_file, why_file = fetch.decode_reference(_spooled(data, "a.tif").file, 25_000_000)
    assert from_bytes is not None and from_file is not None and why == why_file == ""
    assert from_file.tobytes() == from_bytes.tobytes() and fetch.source_format(from_file) == "TIFF"
    refused, reason = fetch.decode_reference(_spooled(data, "a.tif").file, 100_000)  # 240,000 px: above this limit
    assert refused is None and "Image trop grande" in reason


def test_a_tiff_has_its_own_weight_limit_and_the_refusal_says_the_real_size(monkeypatch):
    from nyra.cloud.routes import library

    assert common.upload_byte_limit("scan.TIF") == common.upload_byte_limit("scan.tiff") == common.MAX_TIFF_UPLOAD_BYTES == 100_000_000
    assert common.upload_byte_limit("photo.jpg") == common.upload_byte_limit("photo.png") == common.MAX_UPLOAD_BYTES == 30_000_000
    assert common.megabytes(45_300_000) == "45,3 Mo" and common.megabytes(100_000_000) == "100,0 Mo"
    monkeypatch.setattr(common, "MAX_UPLOAD_BYTES", 1_000_000)
    monkeypatch.setattr(common, "MAX_TIFF_UPLOAD_BYTES", 2_000_000)
    light = _tiff_bytes(600, 400)  # 720 KB
    heavy = _tiff_bytes(1000, 700)  # 2.1 MB
    refusal, name, content = library._read_upload(_spooled(light, "scan.tif"))
    assert refusal is None and name == "scan.tif"
    assert not isinstance(content, bytes) and fetch.sha256_of(content) == fetch.sha256_of(light)  # the file, not 720 KB of bytes
    refusal, _, _ = library._read_upload(_spooled(heavy, "scan.tif"))
    assert refusal == {"filename": "scan.tif", "reason": "Fichier trop lourd : 2,1 Mo, 2,0 Mo au plus pour un TIFF. Exportez-le en JPEG ou réduisez-le."}
    refusal, _, _ = library._read_upload(_spooled(b"j" * 1_200_000, "photo.jpg"))  # another format keeps the lower limit, whatever it holds
    assert refusal == {"filename": "photo.jpg", "reason": "Fichier trop lourd : 1,2 Mo, 1,0 Mo au plus."}
    refusal, _, content = library._read_upload(_spooled(b"j" * 900_000, "photo.jpg"))
    assert refusal is None and isinstance(content, bytes) and len(content) == 900_000  # bytes, as before
    refusal, _, _ = library._read_upload(_spooled(b"", "vide.tif"))
    assert refusal == {"filename": "vide.tif", "reason": "Fichier vide."}
