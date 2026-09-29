"""Geometric verification: the same photo, or only the same subject?

CLIP (level 2) finds candidates a perceptual hash misses (crops, overlays,
recolored copies), but it measures what an image *shows*: two different
shots of the same bottle, or two vineyards at dusk, score high. So every
CLIP candidate is checked here before it is kept.

Two copies of one photo share many local details (SIFT keypoints) that
line up under a single similarity transform: a resize, a crop, a slight
rotation, a mirror. RANSAC finds that transform and counts the keypoints
that agree with it (inliers). Two different shots of the same object only
agree on the object itself (its label), and those points cover a small
part of each frame. So a candidate needs enough inliers spanning enough
of *both* images:

- under `geometric_review_coverage` (5 %): dropped. Another shot of the
  same product, a shared logo;
- from there: "to verify". The same product cut-out reused in another
  composition, a special edition shot on the same template: a person
  decides;
- from `geometric_confirm_coverage` (20 %): confirmed, the same photo,
  cropped, resized, recolored, mirrored or set in a banner.

Calibrated on 1,057 real site images: edited copies are kept 97.5 % of the
time (84 % confirmed), different images 0 %.

OpenCV is imported lazily: the web process never verifies anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

import numpy as np
from PIL import Image, ImageOps

from nyra.config import MatchConfig

log = logging.getLogger("nyra.verify")

WORKING_SIDE = 1024
MAX_KEYPOINTS = 2000
RATIO = 0.75
REPROJECTION_PX = 6.0


@dataclass(frozen=True)
class Features:
    points: np.ndarray  # N × 2, in working pixels
    descriptors: np.ndarray  # N × 128
    size: tuple[int, int]  # working width, height


SAME = "same"
REVIEW = "review"


@dataclass(frozen=True)
class Verdict:
    tier: Optional[str]  # SAME, REVIEW, or None: not the same photo
    inliers: int
    coverage: float  # 0..1, the smaller of the two sides
    coverage_a: float = 0.0
    coverage_b: float = 0.0


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def working_gray(img: Image.Image) -> Image.Image:
    gray = ImageOps.exif_transpose(img).convert("L")
    gray.thumbnail((WORKING_SIDE, WORKING_SIDE))
    return gray


def extract(gray: Image.Image, *, mirror: bool = False) -> Optional[Features]:
    """SIFT keypoints of a working-size grayscale image (see `working_gray`)."""
    import cv2

    if mirror:
        gray = ImageOps.mirror(gray)
    pixels = np.asarray(gray)
    keypoints, descriptors = cv2.SIFT_create(nfeatures=MAX_KEYPOINTS).detectAndCompute(pixels, None)
    if descriptors is None or len(keypoints) < 8:
        return None
    return Features(np.float32([kp.pt for kp in keypoints]), descriptors, gray.size)


def _coverage(points: np.ndarray, size: tuple[int, int]) -> float:
    import cv2

    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(points.reshape(-1, 1, 2))
    return float(cv2.contourArea(hull)) / float(size[0] * size[1])


def compare(a: Optional[Features], b: Optional[Features], config: MatchConfig) -> Verdict:
    if a is None or b is None:
        return Verdict(None, 0, 0.0)
    import cv2

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward = matcher.knnMatch(a.descriptors, b.descriptors, k=2)
    backward = {m.queryIdx: m.trainIdx for m, *_ in (p for p in matcher.knnMatch(b.descriptors, a.descriptors, k=1) if p)}
    # Distinctive (ratio test) and mutual: each keypoint is used once. Without this, many
    # points of one image can pile onto one point of the other and "agree" with a
    # degenerate transform.
    good = [
        m for m, n in (p for p in forward if len(p) == 2)
        if m.distance < RATIO * n.distance and backward.get(m.trainIdx) == m.queryIdx
    ]
    if len(good) < 6:
        return Verdict(None, len(good), 0.0)
    src = a.points[[m.queryIdx for m in good]]
    dst = b.points[[m.trainIdx for m in good]]
    # Resize, crop, slight rotation: a similarity transform. A full homography would
    # also "explain" matches between two different perspectives of the same object.
    transform, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=REPROJECTION_PX,
                                                  maxIters=3000, confidence=0.995)
    if transform is None or mask is None:
        return Verdict(None, 0, 0.0)
    scale = float(np.hypot(transform[0, 0], transform[1, 0]))
    if not 1 / 8 <= scale <= 8:
        return Verdict(None, 0, 0.0)
    keep = mask.ravel().astype(bool)
    inliers = int(keep.sum())
    # The agreeing points must span a real part of *both* images: a copy, even cropped or
    # set in a banner, does; the label of the same bottle in two different shots doesn't.
    coverage_a, coverage_b = _coverage(src[keep], a.size), _coverage(dst[keep], b.size)
    coverage = min(coverage_a, coverage_b)
    tier = None
    if inliers >= config.geometric_min_inliers and coverage >= config.geometric_review_coverage:
        tier = SAME if coverage >= config.geometric_confirm_coverage else REVIEW
    return Verdict(tier, inliers, coverage, coverage_a, coverage_b)


ImageLoader = Callable[[str, Any], Optional[Image.Image]]


def verify_hits(
    hits: Sequence[tuple],
    load_image: ImageLoader,
    config: MatchConfig,
    *,
    level_clip: str,
    level_verified: str,
    confidence_high: str,
    confidence_to_verify: str,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[tuple]:
    """Keep level-1 hits as they are; keep a CLIP hit only if its keypoints line up.

    A kept hit gets `level_verified`, confirmed or "to verify" by its tier.
    One whose images can't be loaded stays at its CLIP level, "to verify".
    `load_image(side, id)` with side "ref" or "site" returns the image or None.
    """
    candidates = [hit for hit in hits if hit[2] == level_clip]
    if not candidates:
        return list(hits)
    grays: dict[tuple[str, Any], Optional[Image.Image]] = {}
    cache: dict[tuple[str, Any, bool], Optional[Features]] = {}

    def gray(side: str, image_id: Any) -> Optional[Image.Image]:
        if (side, image_id) not in grays:
            img = load_image(side, image_id)
            grays[(side, image_id)] = None if img is None else working_gray(img)
        return grays[(side, image_id)]

    def features(side: str, image_id: Any, mirror: bool = False) -> Optional[Features]:
        key = (side, image_id, mirror)
        if key not in cache:
            image = gray(side, image_id)
            cache[key] = None if image is None else extract(image, mirror=mirror)
        return cache[key]

    out = [hit for hit in hits if hit[2] != level_clip]
    kept = dropped = 0
    for index, hit in enumerate(candidates):
        if should_stop and should_stop():
            break
        ref_id, site_id, level, score, _confidence = hit
        ref, site = features("ref", ref_id), features("site", site_id)
        if gray("ref", ref_id) is None or gray("site", site_id) is None:
            # Can't look: keep it, but for a person to check.
            out.append((ref_id, site_id, level, score, confidence_to_verify))
            continue
        verdict = compare(ref, site, config)
        if verdict.tier != SAME:
            mirrored = compare(features("ref", ref_id, True), site, config)
            if mirrored.tier == SAME or (mirrored.tier and not verdict.tier):
                verdict = mirrored
        if verdict.tier:
            confidence = confidence_high if verdict.tier == SAME else confidence_to_verify
            out.append((ref_id, site_id, level_verified, score, confidence))
            kept += 1
        else:
            dropped += 1
        if progress:
            progress(index + 1, len(candidates))
    log.info("geometric verification: %d CLIP candidate(s) kept, %d dropped", kept, dropped)
    return out
