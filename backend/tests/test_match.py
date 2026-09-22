"""Unit tests for nyra.match.

Images are generated on the fly with Pillow (gradient + shapes) so tests
don't depend on binary fixtures. Level 2 (CLIP) tests are skipped when
open_clip/torch aren't installed, since they require downloading model
weights.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from nyra.config import MatchConfig
from nyra.match import (
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


def test_vectorized_match_finds_identical_hash_and_skips_a_distant_one(config):
    from nyra.match import match_index_pairs

    same = np.array([np.uint64(0xFFFF0000FFFF0000)], dtype=np.uint64)
    far = np.array([np.uint64(0xFFFFFFFFFFFFFFFF)], dtype=np.uint64)
    ok = np.array([True])
    none = np.array([False])
    hits = match_index_pairs(
        [7, 8],
        np.array([np.uint64(0xFFFF0000FFFF0000), np.uint64(1)], dtype=np.uint64),
        same.repeat(2),
        np.array([True, True]),
        np.array([True, True]),
        None,
        none.repeat(2),
        [3, 4],
        np.concatenate([same, far]),
        np.concatenate([same, far]),
        np.array([True, True]),
        np.array([True, True]),
        None,
        none.repeat(2),
        config,
        False,
    )
    pairs = {(hit[0], hit[1]) for hit in hits}
    assert (7, 3) in pairs
    assert (8, 4) not in pairs
    assert all(hit[3] == 1.0 for hit in hits if hit[1] == 3)


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


# --- run_matching: DB-integrated incremental matching -----------------------
#
# match_index_pairs (tested above) is the pure comparison kernel. run_matching
# wraps it with a cache: a reference/site pair already compared under the
# current threshold signature isn't recompared on the next call, only the
# delta (new refs x all sites, all old refs x new sites). These tests exercise
# that caching layer end to end against a real (temp-file) database, since
# it's the most complex and least-tested code path in the product.

def _config_with_match(**overrides):
    from dataclasses import replace

    from nyra.config import load_config

    base = load_config()
    return replace(base, match=replace(base.match, **overrides))


def test_run_matching_incremental_picks_up_new_images_without_losing_old_matches(tmp_path):
    from nyra import db
    from nyra.match import run_matching

    db_path = tmp_path / "rw.db"
    db.init_db(db_path)
    config = _config_with_match()
    same_hash = "ffff0000ffff0000"

    with db.connect(db_path) as conn:
        ref_a = db.upsert_reference_image(
            conn, filename="a.jpg", path="a.jpg", expiry_date=None, credit=None, notes=None,
            phash=same_hash, dhash=same_hash,
        )
        site_x = db.upsert_site_image(conn, url="https://example.com/x.jpg", phash=same_hash, dhash=same_hash)

    assert run_matching(db_path, config, use_clip=False) == 1
    # Calling again with nothing new must be a stable no-op, not a crash or a duplicate.
    assert run_matching(db_path, config, use_clip=False) == 1

    # A re-crawl finds the same reference re-served at a second URL.
    with db.connect(db_path) as conn:
        site_y = db.upsert_site_image(conn, url="https://example.com/y.jpg", phash=same_hash, dhash=same_hash)
    assert run_matching(db_path, config, use_clip=False) == 2

    # A newly-ingested reference happens to match both already-known site images.
    with db.connect(db_path) as conn:
        ref_b = db.upsert_reference_image(
            conn, filename="b.jpg", path="b.jpg", expiry_date=None, credit=None, notes=None,
            phash=same_hash, dhash=same_hash,
        )
    assert run_matching(db_path, config, use_clip=False) == 4

    with db.connect(db_path) as conn:
        pairs = {(m["reference_id"], m["site_image_id"]) for m in db.get_matches(conn)}
        coverage = {row["filename"] for row in db.get_unmatched_references(conn)}
    assert pairs == {(ref_a, site_x), (ref_a, site_y), (ref_b, site_x), (ref_b, site_y)}
    assert coverage == set()  # every reference has at least one hit


def test_run_matching_threshold_change_forces_a_full_recompute(tmp_path):
    from nyra import db
    from nyra.match import run_matching

    db_path = tmp_path / "rw.db"
    db.init_db(db_path)
    ref_hash = "ffff0000ffff0000"
    site_hash = "ffff0000ffff000f"  # Hamming distance 4 from ref_hash

    with db.connect(db_path) as conn:
        db.upsert_reference_image(
            conn, filename="a.jpg", path="a.jpg", expiry_date=None, credit=None, notes=None,
            phash=ref_hash, dhash=ref_hash,
        )
        db.upsert_site_image(conn, url="https://example.com/x.jpg", phash=site_hash, dhash=site_hash)

    loose = _config_with_match(phash_threshold=8, dhash_threshold=8)
    assert run_matching(db_path, loose, use_clip=False) == 1

    # Nothing new was added; only the threshold changed. If run_matching only
    # ever compared the delta (nothing, here), this stale match would survive
    # a tightened threshold — that's the bug this test guards against.
    strict = _config_with_match(phash_threshold=0, dhash_threshold=0)
    assert run_matching(db_path, strict, use_clip=False) == 0

    with db.connect(db_path) as conn:
        assert db.get_unmatched_references(conn)[0]["compared_at"] is not None


# --- CLIP end-to-end (skipped unless open_clip/torch are installed) --------

def test_clip_embedding_end_to_end(base_image, config):
    pytest.importorskip("torch")
    pytest.importorskip("open_clip")
    from nyra.match import compute_clip_embedding

    emb_a = compute_clip_embedding(base_image, config)
    emb_b = compute_clip_embedding(base_image, config)
    similarity = cosine_similarity(emb_a, emb_b)
    assert similarity is not None
    assert similarity > 0.99
