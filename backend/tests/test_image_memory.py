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
    return {"RGB": rgb, "L": rgb.convert("L"), "P": rgb.convert("P"),
            "RGBA": Image.merge("RGBA", (*rgb.split(), Image.new("L", rgb.size, 180)))}


@pytest.mark.parametrize("mode", ["RGB", "L", "P", "RGBA"])
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


def test_an_rgb_image_is_not_copied_to_be_hashed_and_nothing_cut_from_it_changes_it():
    img = _picture(1800, 1200)
    assert _rgb(img) is img and _rgb(img.convert("L")) is not None
    exif = Image.Exif()
    exif[0x0112] = 6  # turned a quarter: the copies are cut from the turned image
    jpeg = io.BytesIO()
    img.save(jpeg, format="JPEG", exif=exif)
    for source in (img, Image.open(io.BytesIO(jpeg.getvalue())), img.convert("RGBA")):
        source.load()
        before = source.tobytes()
        assert fetch._turned(source) is not source  # a copy, which `thumbnail` is free to shrink in place
        fetch.make_thumbnail(source)
        fetch.make_working_copy(source)
        fetch.make_stored_original(source, b"x" * (fetch.STORED_BYTES + 1), "image/jpeg")
        reference_features(source)
        assert source.tobytes() == before and source.size[0] > 1000  # still whole, still the same pixels


def test_the_stored_original_and_the_copies_keep_their_pixels_whatever_the_mode():
    for mode, img in _in_every_mode().items():
        rgb = img.convert("RGB")
        thumb = Image.open(io.BytesIO(fetch.make_thumbnail(img)))
        reference = rgb.copy()
        reference.thumbnail((fetch.THUMB_SIZE, fetch.THUMB_SIZE))
        assert thumb.size == reference.size, mode
        work = Image.open(io.BytesIO(fetch.make_working_copy(img)))
        assert work.size == rgb.size  # 400 px wide: under the working side, kept at its size


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
