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
from functools import cached_property, lru_cache
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


class _Reference:
    """A reference seen through the keypoint check: its grayscale and its keypoints, mirror on demand."""

    def __init__(self, gray: Optional[Image.Image]):
        self.gray = gray
        self.plain = None if gray is None else extract(gray)

    @cached_property
    def mirrored(self) -> Optional[Features]:
        return None if self.gray is None else extract(self.gray, mirror=True)


# A reference meets many site images, so its decoded grayscale (~1 MB) and its two keypoint sets
# (~1 MB each: 2000 keypoints x 128 float32) are kept; the hits come reference by reference, so a few
# dozen entries are enough. 32 references hold about 100 MB at most.
REFERENCE_CACHE = 32
# A site image is a candidate of several references, so its keypoints (~1 MB, its grayscale is dropped
# once read) are kept too, unreadable ones as a memo: 64 sites hold about 65 MB at most.
SITE_CACHE = 64


class _ImageFeatures:
    """Reference and site keypoints, each in a small LRU, so an image is read once while it stays in it."""

    def __init__(self, load_image: ImageLoader):
        self.load_image = load_image
        self.reference = lru_cache(maxsize=REFERENCE_CACHE)(self._load_reference)
        self.site = lru_cache(maxsize=SITE_CACHE)(self._load_site)

    def gray(self, side: str, image_id: Any) -> Optional[Image.Image]:
        img = self.load_image(side, image_id)
        return None if img is None else working_gray(img)

    def _load_reference(self, ref_id: Any) -> _Reference:
        return _Reference(self.gray("ref", ref_id))

    def _load_site(self, site_id: Any) -> Optional[tuple[Optional[Features]]]:
        """None: unreadable. Otherwise a 1-tuple, as an image without keypoints (`(None,)`) is not an unreadable one."""
        gray = self.gray("site", site_id)
        return None if gray is None else (extract(gray),)

    def clear(self) -> None:
        # The caches and their owner reference each other: free the images now, not at the next GC.
        self.reference.cache_clear()
        self.site.cache_clear()


def _pair_verdict(images: _ImageFeatures, ref_id: Any, site_id: Any, config: MatchConfig) -> Optional[Verdict]:
    """The keypoint verdict of a pair, the mirrored reference included; None when an image can't be read."""
    ref = images.reference(ref_id)
    loaded = None if ref.gray is None else images.site(site_id)
    if loaded is None:
        return None
    (site,) = loaded
    verdict = compare(ref.plain, site, config)
    if verdict.tier != SAME:
        mirrored = compare(ref.mirrored, site, config)
        if mirrored.tier == SAME or (mirrored.tier and not verdict.tier):
            verdict = mirrored
    return verdict


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
    images = _ImageFeatures(load_image)
    out = [hit for hit in hits if hit[2] != level_clip]
    kept = dropped = 0
    for index, hit in enumerate(candidates):
        if should_stop and should_stop():
            break
        ref_id, site_id, level, score, _confidence = hit
        verdict = _pair_verdict(images, ref_id, site_id, config)
        if verdict is None:
            # Can't look: keep it, but for a person to check.
            out.append((ref_id, site_id, level, score, confidence_to_verify))
            continue
        if verdict.tier:
            confidence = confidence_high if verdict.tier == SAME else confidence_to_verify
            out.append((ref_id, site_id, level_verified, score, confidence))
            kept += 1
        else:
            dropped += 1
        if progress:
            progress(index + 1, len(candidates))
    images.clear()
    log.info("geometric verification: %d CLIP candidate(s) kept, %d dropped", kept, dropped)
    return out
