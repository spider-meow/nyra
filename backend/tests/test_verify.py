"""nyra/verify.py: CLIP candidates are kept only when their keypoints line up.

Calibration on real images is described in docs/MATCHING.md; these tests
pin the behavior on generated, textured images.
"""

from __future__ import annotations

import io
import random

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

pytest.importorskip("cv2")

from nyra import db, verify  # noqa: E402
from nyra.config import MatchConfig, load_config  # noqa: E402
from nyra.match import compute_flip_hashes, compute_hashes, run_matching  # noqa: E402

CONFIG = MatchConfig()


def scene(seed: int, size=(900, 650)) -> Image.Image:
    """A busy picture: shapes, lines and text everywhere, like a photo has texture everywhere."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(260):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(8, 90), rng.randrange(8, 90)
        color = tuple(rng.randrange(256) for _ in range(3))
        shape = rng.randrange(3)
        if shape == 0:
            draw.rectangle([x, y, x + w, y + h], fill=color)
        elif shape == 1:
            draw.ellipse([x, y, x + w, y + h], outline=color, width=3)
        else:
            draw.line([x, y, x + w, y + h], fill=color, width=2)
    for _ in range(25):
        draw.text((rng.randrange(size[0]), rng.randrange(size[1])), f"{rng.randrange(10**6)}", fill=(0, 0, 0))
    return img


def jpeg(img: Image.Image, quality: int = 70) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def tier(a: Image.Image, b: Image.Image, *, mirror: bool = False):
    ref = verify.extract(verify.working_gray(a), mirror=mirror)
    return verify.compare(ref, verify.extract(verify.working_gray(b)), CONFIG)


def test_an_edited_copy_is_the_same_photo():
    original = scene(1)
    copy = jpeg(original.crop((90, 60, 800, 600)).resize((500, 380)), quality=55)
    verdict = tier(original, copy)
    assert verdict.tier == verify.SAME and verdict.inliers >= CONFIG.geometric_min_inliers


def test_a_mirrored_copy_is_found_through_the_mirrored_reference():
    original = scene(2)
    assert tier(original, jpeg(ImageOps.mirror(original)), mirror=True).tier == verify.SAME


def test_two_different_pictures_are_not():
    assert tier(scene(3), scene(4)).tier is None


def test_the_same_element_in_another_picture_is_for_a_person_to_check_or_dropped():
    """A shared cut-out (a bottle on another background) covers a small part of both frames."""
    product = scene(5, size=(160, 220))
    first, second = scene(6), scene(7)
    first.paste(product, (80, 120))
    second.paste(product, (600, 300))
    assert tier(first, second).tier in {verify.REVIEW, None}


def test_verify_hits_drops_other_pictures_confirms_copies_and_keeps_level_one_hits():
    images = {("ref", 1): scene(10), ("site", 1): jpeg(scene(10).resize((600, 433))), ("site", 2): scene(11)}
    hits = [
        (1, 1, "clip", 0.93, "haut"),
        (1, 2, "clip", 0.95, "haut"),  # CLIP is sure; the keypoints say otherwise
        (1, 3, "phash", 0.98, "haut"),  # level 1 is not re-checked
        (1, 4, "clip", 0.80, "a_verifier"),  # image missing: kept, for a person
    ]
    kept = verify.verify_hits(
        hits, lambda side, image_id: images.get((side, image_id)), CONFIG,
        level_clip="clip", level_verified="geo", confidence_high="haut", confidence_to_verify="a_verifier",
    )
    assert sorted(kept) == sorted([
        (1, 1, "geo", 0.93, "haut"),
        (1, 3, "phash", 0.98, "haut"),
        (1, 4, "clip", 0.80, "a_verifier"),
    ])


def test_the_image_caches_stay_bounded_and_verdicts_do_not_change(monkeypatch):
    """More references and sites than the caches hold: their sizes never pass the limits, and each verdict is
    the one of a pair checked alone (nothing cached), an unreadable site image included."""
    monkeypatch.setattr(verify, "REFERENCE_CACHE", 3)
    monkeypatch.setattr(verify, "SITE_CACHE", 2)
    size = (360, 260)
    refs = {i: scene(30 + i, size) for i in range(8)}
    sites = {0: jpeg(refs[0].resize((300, 216))), 1: scene(60, size), 2: jpeg(ImageOps.mirror(refs[5])), 3: None}
    load = lambda side, image_id: (refs if side == "ref" else sites)[image_id]  # noqa: E731
    pairs = [(r, s) for r in refs for s in sites]  # 32 distinct pairs, reference by reference

    shared = verify._ImageFeatures(load)
    for ref_id, site_id in pairs:
        got = verify._pair_verdict(shared, ref_id, site_id, CONFIG)
        assert shared.reference.cache_info().currsize <= 3 and shared.site.cache_info().currsize <= 2
        assert got == verify._pair_verdict(verify._ImageFeatures(load), ref_id, site_id, CONFIG)
        assert (got is None) == (site_id == 3)  # unreadable: nothing to say, not a "different picture"
    assert {verify._pair_verdict(shared, r, s, CONFIG).tier for r, s in [(0, 0), (5, 2), (1, 1)]} == {verify.SAME, None}
    shared.clear()
    assert shared.reference.cache_info().currsize == shared.site.cache_info().currsize == 0


def test_a_site_image_is_read_once_for_all_the_references_it_is_a_candidate_of():
    """Each site id is loaded at most once while cached, an unreadable one too (it is not retried)."""
    size = (360, 260)
    refs = {i: scene(70 + i, size) for i in range(6)}
    sites = {0: jpeg(refs[2].resize((300, 216))), 1: scene(90, size), 2: None}
    loads: dict = {}

    def load(side, image_id):
        loads[side, image_id] = loads.get((side, image_id), 0) + 1
        return (refs if side == "ref" else sites)[image_id]

    hits = [(r, s, "clip", 0.9, "haut") for r in refs for s in sites]
    names = dict(level_clip="clip", level_verified="geo", confidence_high="haut", confidence_to_verify="a_verifier")
    kept = verify.verify_hits(hits, load, CONFIG, **names)
    assert {key: n for key, n in loads.items() if key[0] == "site"} == {("site", 0): 1, ("site", 1): 1, ("site", 2): 1}
    assert (2, 0, "geo", 0.9, "haut") in kept and (2, 2, "clip", 0.9, "a_verifier") in kept  # copy confirmed; unreadable kept for a person


def test_the_offline_store_gives_the_verifier_its_images(tmp_path):
    """SQLite store + run_matching: a CLIP candidate that is a crop of the reference becomes geo/haut,
    another shot (same CLIP score) is dropped. Before, `load_image` found no file and both stayed clip/a_verifier."""
    db_path = tmp_path / "g.db"
    db.init_db(db_path)
    vector = np.random.default_rng(1).normal(0, 1, 16).astype(np.float32)
    vector /= np.linalg.norm(vector)  # the same embedding everywhere: CLIP alone can't tell the crop from the other shot
    original = scene(40)
    with db.connect(db_path) as conn:
        original.save(tmp_path / "ref.png")
        (phash, dhash), (pflip, dflip) = compute_hashes(original), compute_flip_hashes(original)
        db.upsert_reference_image(conn, filename="ref.jpg", path=str(tmp_path / "ref.png"), expiry_date=None, credit=None,
                                  notes=None, phash=phash, dhash=dhash, phash_flip=pflip, dhash_flip=dflip, embedding=vector)
        for name, image in {"crop": jpeg(original.crop((90, 60, 800, 600)).resize((500, 380)), 55), "other": scene(41)}.items():
            image.save(tmp_path / f"{name}.png")
            site_phash, site_dhash = compute_hashes(image)
            db.upsert_site_image(conn, url=f"https://ex.com/{name}.jpg", local_path=str(tmp_path / f"{name}.png"),
                                 phash=site_phash, dhash=site_dhash, embedding=vector)
    stats: dict = {}
    run_matching(db_path, load_config(), use_clip=True, stats=stats)
    with db.connect(db_path) as conn:
        found = [(m["site_url"], m["level"], m["confidence"]) for m in db.get_matches(conn)]
    assert stats["verified_candidates"] == 2
    assert found == [("https://ex.com/crop.jpg", "geo", "haut")]
