"""Guard rail on the hand-set thresholds: precision and recall of the hash level and of the keypoint check on a fixed,
generated reference set. Nothing is stored: the images come from seeded code, so the set is the same on every run.

It does NOT validate the thresholds on real photographs (the figures in docs/MATCHING.md do that, by hand, with
`nyra calibrate`). It catches a threshold or a model changed without measuring: an absurd value (64 bits, 0 bits,
zero keypoints) falls below the floors written here. The CLIP level needs torch and real images; it is not covered.
To change the reference on purpose, see "Calibration in CI" in docs/MATCHING.md.
"""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from nyra import match, verify
from nyra.config import load_config

SEED = 1000  # the reference set: change it, a transform or the sizes on purpose, then re-measure and update the floors
TRANSFORMS = {
    "jpeg": lambda img: _jpeg(img, 40),
    "resize": lambda img: img.resize((img.width * 2 // 5, img.height * 2 // 5), Image.LANCZOS),
    "mirror": lambda img: _jpeg(ImageOps.mirror(img), 30),
    "heavy jpeg": lambda img: _jpeg(img.resize((img.width // 4, img.height // 4), Image.LANCZOS), 10),
    "colour": lambda img: _jpeg(ImageOps.autocontrast(img, cutoff=10), 30),
    "brightness": lambda img: ImageEnhance.Brightness(img).enhance(1.25),
    "light crop": lambda img: img.crop((img.width * 3 // 100, img.height * 3 // 100, img.width * 97 // 100, img.height * 97 // 100)),
}

# Measured on the reference set (Pillow 12.3, OpenCV 5.0): hash level precision 1.000 (0 false pairs of 6,090), recall
# 0.990 (208 of 210; a 3 % crop and a strong colour change are the two misses); keypoint check precision 1.000
# (0 of 210), recall 1.000 (42 of 42). The floors sit under that, so a library update does not trip them but a threshold
# set at random does.
HASH_MIN_PRECISION, HASH_MIN_RECALL = 0.98, 0.90
KEYPOINT_MIN_PRECISION, KEYPOINT_MIN_RECALL = 0.98, 0.95


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _scene(seed: int, size=(640, 480)) -> Image.Image:
    """A textured picture (shapes, lines, numbers) so that perceptual hashes and keypoints have something to hold."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(120):
        x, y, w, h = rng.randrange(size[0]), rng.randrange(size[1]), rng.randrange(10, 120), rng.randrange(10, 120)
        color = tuple(rng.randrange(256) for _ in range(3))
        shape = rng.randrange(3)
        if shape == 0:
            draw.rectangle([x, y, x + w, y + h], fill=color)
        elif shape == 1:
            draw.ellipse([x, y, x + w, y + h], outline=color, width=3)
        else:
            draw.line([x, y, x + w, y + h], fill=color, width=2)
    for _ in range(20):
        draw.text((rng.randrange(size[0]), rng.randrange(size[1])), str(rng.randrange(10**6)), fill=(0, 0, 0))
    return img


def reference_set(count: int) -> tuple[list[Image.Image], list[tuple[int, str, Image.Image]]]:
    """`count` original pictures and, for each, one copy per transform: (index of its original, transform, copy).
    Every other pairing is a pair of different pictures."""
    originals = [_scene(SEED + i) for i in range(count)]
    return originals, [(i, name, edit(img)) for i, img in enumerate(originals) for name, edit in TRANSFORMS.items()]


def _precision_recall(found: set, truth: set) -> tuple[float, float]:
    tp = len(found & truth)
    return tp / max(1, len(found)), tp / len(truth)


def test_hash_level_keeps_its_precision_and_recall_on_the_reference_set():
    config = load_config().match
    originals, copies = reference_set(30)

    def rows(images, flip):
        out = []
        for k, img in enumerate(images):
            phash, dhash = match.compute_hashes(img)
            out.append({"id": k, "phash": phash, "dhash": dhash,
                        **(dict(zip(("phash_flip", "dhash_flip"), match.compute_flip_hashes(img))) if flip else {})})
        return match.pack(out, False, with_flip=flip)

    hits = match.compare(rows(originals, True), rows([c[2] for c in copies], False), config, False)
    truth = {(original, k) for k, (original, _name, _img) in enumerate(copies)}
    precision, recall = _precision_recall({(ref, site) for ref, site, *_ in hits}, truth)
    assert precision >= HASH_MIN_PRECISION, f"hash precision {precision:.3f}: thresholds {config.phash_threshold}/{config.dhash_threshold}"
    assert recall >= HASH_MIN_RECALL, f"hash recall {recall:.3f}: thresholds {config.phash_threshold}/{config.dhash_threshold}"


@pytest.mark.skipif(not verify.available(), reason="OpenCV is not installed")
def test_keypoint_check_keeps_its_precision_and_recall_on_the_reference_set():
    config = load_config().match
    originals, copies = reference_set(6)  # 6 × 42 pairs: the keypoint check costs a few ms a pair
    refs = [(verify.extract(verify.working_gray(img)), verify.extract(verify.working_gray(img), mirror=True)) for img in originals]
    sites = [verify.extract(verify.working_gray(img)) for _original, _name, img in copies]
    kept = {(i, k) for i, pair in enumerate(refs) for k, site in enumerate(sites)
            if any(verify.compare(ref, site, config).tier for ref in pair)}
    truth = {(original, k) for k, (original, _name, _img) in enumerate(copies)}
    precision, recall = _precision_recall(kept, truth)
    settings = f"{config.geometric_min_inliers} inliers, coverage {config.geometric_review_coverage}/{config.geometric_confirm_coverage}"
    assert precision >= KEYPOINT_MIN_PRECISION, f"keypoint precision {precision:.3f} ({settings})"
    assert recall >= KEYPOINT_MIN_RECALL, f"keypoint recall {recall:.3f} ({settings})"
