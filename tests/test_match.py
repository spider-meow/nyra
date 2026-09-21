"""Unit tests for rightswatch.match.

Images are generated on the fly with Pillow (gradient + shapes) so tests
don't depend on binary fixtures. Level 2 (CLIP) tests are skipped when
open_clip/torch aren't installed, since they require downloading model
weights.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from rightswatch.config import MatchConfig
from rightswatch.match import (
    classify_level1,
    classify_level2,
    classify_pair,
    compute_dhash,
    compute_phash,
    cosine_similarity,
    hamming_distance,
)


def make_base_image(size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        color = (y % 256, (255 - y) % 256, (y * 2) % 256)
        draw.line([(0, y), (size, y)], fill=color)
    draw.ellipse([size // 4, size // 4, size * 3 // 4, size * 3 // 4], fill=(20, 200, 20))
    draw.rectangle([10, 10, 60, 40], fill=(200, 20, 20))
    return img


def make_different_image(size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size), color=(10, 10, 10))
    draw = ImageDraw.Draw(img)
    for i in range(0, size, 16):
        draw.line([(i, 0), (0, i)], fill=(255, 255, 0), width=3)
    draw.polygon([(size // 2, 10), (10, size - 10), (size - 10, size - 10)], fill=(0, 100, 255))
    return img


def recompress_jpeg(img: Image.Image, quality: int = 40) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def resize(img: Image.Image, factor: float) -> Image.Image:
    w, h = img.size
    return img.resize((int(w * factor), int(h * factor)), Image.LANCZOS)


def add_text_overlay(img: Image.Image) -> Image.Image:
    img = img.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    draw.rectangle([0, h - 40, w, h], fill=(0, 0, 0))
    draw.text((10, h - 32), "PROMO -50%", fill=(255, 255, 255))
    return img


def crop(img: Image.Image, margin_frac: float = 0.15) -> Image.Image:
    w, h = img.size
    mx, my = int(w * margin_frac), int(h * margin_frac)
    return img.crop((mx, my, w - mx, h - my)).resize((w, h), Image.LANCZOS)


@pytest.fixture(scope="module")
def config() -> MatchConfig:
    return MatchConfig()


@pytest.fixture(scope="module")
def base_image() -> Image.Image:
    return make_base_image()


# --- hamming distance -------------------------------------------------------

def test_hamming_distance_identical_hashes_is_zero():
    assert hamming_distance("ffff0000ffff0000", "ffff0000ffff0000") == 0


def test_hamming_distance_none_when_missing():
    assert hamming_distance(None, "ffff0000ffff0000") is None
    assert hamming_distance("ffff0000ffff0000", None) is None


# --- level 1: identical / recompressed / resized should match --------------

def test_identical_image_matches_level1(base_image, config):
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(base_image), compute_dhash(base_image)
    result = classify_level1(p1, d1, p2, d2, config)
    assert result is not None
    assert result.confidence == "haut"


def test_recompressed_image_matches_level1(base_image, config):
    recompressed = recompress_jpeg(base_image, quality=35)
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(recompressed), compute_dhash(recompressed)
    result = classify_level1(p1, d1, p2, d2, config)
    assert result is not None


def test_resized_image_matches_level1(base_image, config):
    resized = resize(base_image, 0.4)
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(resized), compute_dhash(resized)
    result = classify_level1(p1, d1, p2, d2, config)
    assert result is not None


def test_different_image_does_not_match_level1(base_image, config):
    other = make_different_image()
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(other), compute_dhash(other)
    result = classify_level1(p1, d1, p2, d2, config)
    assert result is None


def test_different_image_does_not_match_via_classify_pair_without_clip(base_image, config):
    other = make_different_image()
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(other), compute_dhash(other)
    result = classify_pair(
        ref_phash=p1, ref_dhash=d1, site_phash=p2, site_dhash=d2, config=config, use_clip=False
    )
    assert result is None


# --- heavier transforms (crop, overlay) exercise the level-1/level-2 split -

def test_heavily_cropped_image_needs_level2(base_image, config):
    cropped = crop(base_image, margin_frac=0.25)
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(cropped), compute_dhash(cropped)
    result = classify_level1(p1, d1, p2, d2, config)
    # A heavy crop should not be treated as a quasi-exact match by hashing alone;
    # this is exactly the case level 2 (CLIP) exists to catch.
    assert result is None


def test_overlay_image_still_close_enough_or_falls_to_level2(base_image, config):
    overlaid = add_text_overlay(base_image)
    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    p2, d2 = compute_phash(overlaid), compute_dhash(overlaid)
    # Either level 1 catches a small overlay (hash barely moves) or it
    # doesn't and the pair is left for level 2 — both are valid, but the
    # function must not raise and must return a well-formed result or None.
    result = classify_level1(p1, d1, p2, d2, config)
    assert result is None or result.confidence == "haut"


# --- level 2: cosine similarity classification (no model download needed) --

def test_classify_level2_high_confidence(config):
    import numpy as np

    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.99, 0.05, 0.0], dtype=np.float32)
    result = classify_level2(a, b, config)
    assert result is not None
    assert result.level == "clip"
    assert result.confidence == "haut"


def test_classify_level2_medium_confidence(config):
    import numpy as np

    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.87, 0.5], dtype=np.float32)
    result = classify_level2(a, b, config)
    assert result is not None
    assert result.confidence in {"moyen", "a_verifier"}


def test_classify_level2_below_floor_is_none(config):
    import numpy as np

    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    result = classify_level2(a, b, config)
    assert result is None


def test_cosine_similarity_none_when_missing():
    import numpy as np

    assert cosine_similarity(None, np.array([1.0])) is None
    assert cosine_similarity(np.array([1.0]), None) is None


# --- classify_pair prefers level 1 over level 2 -----------------------------

def test_classify_pair_prefers_level1_result(base_image, config):
    import numpy as np

    p1, d1 = compute_phash(base_image), compute_dhash(base_image)
    embedding = np.array([1.0, 0.0], dtype=np.float32)
    result = classify_pair(
        ref_phash=p1,
        ref_dhash=d1,
        site_phash=p1,
        site_dhash=d1,
        ref_embedding=embedding,
        site_embedding=np.array([0.0, 1.0], dtype=np.float32),  # would fail level 2
        config=config,
        use_clip=True,
    )
    assert result is not None
    assert result.level in {"phash", "dhash"}


# --- CLIP end-to-end (skipped unless open_clip/torch are installed) --------

def test_clip_embedding_end_to_end(base_image, config):
    pytest.importorskip("torch")
    pytest.importorskip("open_clip")
    from rightswatch.match import compute_clip_embedding

    emb_a = compute_clip_embedding(base_image, config)
    emb_b = compute_clip_embedding(base_image, config)
    similarity = cosine_similarity(emb_a, emb_b)
    assert similarity is not None
    assert similarity > 0.99
