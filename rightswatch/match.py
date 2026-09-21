"""Two-level, deterministic image matching.

Level 1 (cheap, exact/quasi-exact): perceptual hash (pHash) + difference hash
(dHash), compared by Hamming distance. Catches identical or lightly
re-encoded/resized images.

Level 2 (expensive, only for pairs level 1 missed): CLIP embeddings compared
by cosine similarity. Catches crops, overlays, and other retouches.

Every function that does the actual comparison is pure (hashes/vectors in,
a verdict out) so it can be unit tested without a database, a crawl, or a
network connection. `run_matching` is the only function that touches SQLite.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import imagehash
import numpy as np
from PIL import Image

from rightswatch.config import Config, MatchConfig

CONFIDENCE_HIGH = "haut"
CONFIDENCE_MEDIUM = "moyen"
CONFIDENCE_TO_VERIFY = "a_verifier"

LEVEL_PHASH = "phash"
LEVEL_DHASH = "dhash"
LEVEL_CLIP = "clip"


@dataclass(frozen=True)
class MatchResult:
    level: str
    score: float
    confidence: str


# --- hashing --------------------------------------------------------------

def compute_phash(image: Image.Image) -> str:
    return str(imagehash.phash(image))


def compute_dhash(image: Image.Image) -> str:
    return str(imagehash.dhash(image))


def compute_hashes(image: Image.Image) -> tuple[str, str]:
    rgb = image.convert("RGB")
    return compute_phash(rgb), compute_dhash(rgb)


def hamming_distance(hash_a: Optional[str], hash_b: Optional[str]) -> Optional[int]:
    if not hash_a or not hash_b:
        return None
    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)


# --- CLIP embeddings (lazy: torch/open_clip only load if level 2 runs) ---

@lru_cache(maxsize=1)
def _load_clip(model_name: str, pretrained: str):
    import open_clip
    import torch

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained
    )
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    return model, preprocess, device


def compute_clip_embedding(image: Image.Image, config: MatchConfig) -> np.ndarray:
    import torch

    model, preprocess, device = _load_clip(config.clip_model_name, config.clip_pretrained)
    tensor = preprocess(image.convert("RGB")).unsqueeze(0).to(device)
    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().numpy().astype(np.float32)


def cosine_similarity(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> Optional[float]:
    if a is None or b is None:
        return None
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return None
    return float(np.dot(a, b) / denom)


# --- classification --------------------------------------------------------

def classify_level1(
    ref_phash: Optional[str],
    ref_dhash: Optional[str],
    site_phash: Optional[str],
    site_dhash: Optional[str],
    config: MatchConfig,
) -> Optional[MatchResult]:
    """Hamming-distance match. Returns the best (lowest-distance) hit, if any."""
    candidates: list[MatchResult] = []

    p_dist = hamming_distance(ref_phash, site_phash)
    if p_dist is not None and p_dist <= config.phash_threshold:
        score = 1.0 - (p_dist / 64.0)
        candidates.append(MatchResult(LEVEL_PHASH, score, CONFIDENCE_HIGH))

    d_dist = hamming_distance(ref_dhash, site_dhash)
    if d_dist is not None and d_dist <= config.dhash_threshold:
        score = 1.0 - (d_dist / 64.0)
        candidates.append(MatchResult(LEVEL_DHASH, score, CONFIDENCE_HIGH))

    if not candidates:
        return None
    return max(candidates, key=lambda m: m.score)


def classify_level2(
    ref_embedding: Optional[np.ndarray],
    site_embedding: Optional[np.ndarray],
    config: MatchConfig,
) -> Optional[MatchResult]:
    similarity = cosine_similarity(ref_embedding, site_embedding)
    if similarity is None or similarity < config.clip_similarity_floor:
        return None
    if similarity >= config.clip_similarity_high:
        confidence = CONFIDENCE_HIGH
    elif similarity >= config.clip_similarity_medium:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_TO_VERIFY
    return MatchResult(LEVEL_CLIP, similarity, confidence)


def classify_pair(
    *,
    ref_phash: Optional[str],
    ref_dhash: Optional[str],
    site_phash: Optional[str],
    site_dhash: Optional[str],
    ref_embedding: Optional[np.ndarray] = None,
    site_embedding: Optional[np.ndarray] = None,
    config: MatchConfig,
    use_clip: bool = True,
) -> Optional[MatchResult]:
    """Level 1 first; level 2 only runs when level 1 found nothing."""
    level1 = classify_level1(ref_phash, ref_dhash, site_phash, site_dhash, config)
    if level1 is not None:
        return level1
    if use_clip:
        return classify_level2(ref_embedding, site_embedding, config)
    return None


# --- DB-integrated pass -----------------------------------------------------

def run_matching(db_path, config: Config, use_clip: bool = True, progress=None) -> int:
    """Match every reference image against every site image and persist hits.

    Returns the number of matches written. `progress`, if given, is called
    with (done, total) after each reference image is processed.
    """
    from rightswatch import db

    with db.connect(db_path) as conn:
        db.clear_matches(conn)
        refs = db.get_reference_images(conn)
        site_images = db.get_site_images(conn)

        total = len(refs)
        for i, ref in enumerate(refs):
            ref_embedding = db.unpack_embedding(ref["embedding"]) if use_clip else None
            for site in site_images:
                site_embedding = db.unpack_embedding(site["embedding"]) if use_clip else None
                result = classify_pair(
                    ref_phash=ref["phash"],
                    ref_dhash=ref["dhash"],
                    site_phash=site["phash"],
                    site_dhash=site["dhash"],
                    ref_embedding=ref_embedding,
                    site_embedding=site_embedding,
                    config=config.match,
                    use_clip=use_clip,
                )
                if result is not None:
                    db.upsert_match(
                        conn,
                        reference_id=ref["id"],
                        site_image_id=site["id"],
                        level=result.level,
                        score=result.score,
                        confidence=result.confidence,
                    )
            if progress:
                progress(i + 1, total)

        return conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]


def load_image_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# --- calibration ------------------------------------------------------------

@dataclass(frozen=True)
class GroundTruthPair:
    ref_filename: str
    site_url: str
    is_match: bool


@dataclass(frozen=True)
class ThresholdResult:
    metric: str
    threshold: float
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def sweep_phash_thresholds(
    pairs: list[tuple[GroundTruthPair, Optional[int]]], thresholds: list[int]
) -> list[ThresholdResult]:
    """`pairs` is (ground_truth, hamming_distance_or_None) per labeled pair."""
    results = []
    for t in thresholds:
        tp = fp = fn = 0
        for gt, distance in pairs:
            predicted = distance is not None and distance <= t
            if predicted and gt.is_match:
                tp += 1
            elif predicted and not gt.is_match:
                fp += 1
            elif not predicted and gt.is_match:
                fn += 1
        precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
        results.append(ThresholdResult("hamming<=", float(t), precision, recall, f1, tp, fp, fn))
    return results


def sweep_similarity_thresholds(
    pairs: list[tuple[GroundTruthPair, Optional[float]]], thresholds: list[float]
) -> list[ThresholdResult]:
    results = []
    for t in thresholds:
        tp = fp = fn = 0
        for gt, similarity in pairs:
            predicted = similarity is not None and similarity >= t
            if predicted and gt.is_match:
                tp += 1
            elif predicted and not gt.is_match:
                fp += 1
            elif not predicted and gt.is_match:
                fn += 1
        precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
        results.append(ThresholdResult("cosine>=", t, precision, recall, f1, tp, fp, fn))
    return results


def load_ground_truth_csv(path) -> list[GroundTruthPair]:
    import csv

    pairs = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = row["label"].strip().lower()
            pairs.append(
                GroundTruthPair(
                    ref_filename=row["ref_filename"].strip(),
                    site_url=row["site_url"].strip(),
                    is_match=label in {"match", "1", "true", "yes"},
                )
            )
    return pairs


def calibrate(db_path, ground_truth_path, config: Config) -> dict[str, list[ThresholdResult]]:
    """Compute precision/recall across a range of thresholds from hand-confirmed pairs.

    Requires refs and site images already ingested/crawled (their hashes and
    embeddings must already be in the database).
    """
    from rightswatch import db

    ground_truth = load_ground_truth_csv(ground_truth_path)

    with db.connect(db_path) as conn:
        refs_by_filename = {r["filename"]: r for r in db.get_reference_images(conn)}
        sites_by_url = {s["url"]: s for s in db.get_site_images(conn)}

    phash_pairs: list[tuple[GroundTruthPair, Optional[int]]] = []
    dhash_pairs: list[tuple[GroundTruthPair, Optional[int]]] = []
    clip_pairs: list[tuple[GroundTruthPair, Optional[float]]] = []

    for gt in ground_truth:
        ref = refs_by_filename.get(gt.ref_filename)
        site = sites_by_url.get(gt.site_url)
        if ref is None or site is None:
            phash_pairs.append((gt, None))
            dhash_pairs.append((gt, None))
            clip_pairs.append((gt, None))
            continue
        phash_pairs.append((gt, hamming_distance(ref["phash"], site["phash"])))
        dhash_pairs.append((gt, hamming_distance(ref["dhash"], site["dhash"])))
        clip_pairs.append(
            (gt, cosine_similarity(db.unpack_embedding(ref["embedding"]), db.unpack_embedding(site["embedding"])))
        )

    return {
        "phash": sweep_phash_thresholds(phash_pairs, list(range(0, 21))),
        "dhash": sweep_phash_thresholds(dhash_pairs, list(range(0, 21))),
        "clip": sweep_similarity_thresholds(clip_pairs, [round(x * 0.01, 2) for x in range(50, 100)]),
    }
