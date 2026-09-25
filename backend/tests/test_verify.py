"""nyra/verify.py: CLIP candidates are kept only when their keypoints line up.

Calibration on real images is described in docs/MATCHING.md; these tests
pin the behavior on generated, textured images.
"""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageOps

pytest.importorskip("cv2")

from nyra import verify  # noqa: E402
from nyra.config import MatchConfig  # noqa: E402

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
