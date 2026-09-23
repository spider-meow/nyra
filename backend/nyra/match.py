"""Two-level, deterministic image matching.

Level 1 (cheap, exact/quasi-exact): perceptual hash (pHash) + difference hash
(dHash), compared by Hamming distance. Catches identical or lightly
re-encoded/resized images. References also carry the hashes of their
mirror image, so a horizontally flipped reuse is caught at this level too.

Level 2 (expensive, only for pairs level 1 missed): CLIP embeddings compared
by cosine similarity. Catches crops, overlays, and other retouches.

Every function that does the actual comparison is pure (hashes/vectors in,
a verdict out) so it can be unit tested without a database, a crawl, or a
network connection. `run_matching` orchestrates a pass against any
`MatchStore` — SQLite for the CLI (`db.LocalStore`), Postgres for the
hosted product (`cloud.store.CloudMatchStore`).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

import imagehash
import numpy as np
from PIL import Image, ImageOps

from nyra.config import Config, MatchConfig

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


def compute_flip_hashes(image: Image.Image) -> tuple[str, str]:
    """Hashes of the horizontal mirror, stored on references only."""
    return compute_hashes(ImageOps.mirror(image.convert("RGB")))


def hamming_distance(hash_a: Optional[str], hash_b: Optional[str]) -> Optional[int]:
    if not hash_a or not hash_b:
        return None
    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)


# --- CLIP embeddings (lazy: torch/open_clip only load if level 2 runs) ---

@lru_cache(maxsize=1)
def _load_clip(model_name: str, pretrained: str):
    import open_clip
    import torch

    model, _, preprocess = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    return model, preprocess, device


def compute_clip_embeddings(images: Sequence[Image.Image], config: MatchConfig) -> list[np.ndarray]:
    """L2-normalized embeddings, computed in batches of `embedding_batch_size`."""
    if not images:
        return []
    import torch

    model, preprocess, device = _load_clip(config.clip_model_name, config.clip_pretrained)
    out: list[np.ndarray] = []
    size = max(1, config.embedding_batch_size)
    for start in range(0, len(images), size):
        batch = torch.stack([preprocess(img.convert("RGB")) for img in images[start : start + size]]).to(device)
        with torch.no_grad():
            features = model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)
        out.extend(row.astype(np.float32) for row in features.cpu().numpy())
    return out


def compute_clip_embedding(image: Image.Image, config: MatchConfig) -> np.ndarray:
    return compute_clip_embeddings([image], config)[0]


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
        candidates.append(MatchResult(LEVEL_PHASH, 1.0 - (p_dist / 64.0), CONFIDENCE_HIGH))

    d_dist = hamming_distance(ref_dhash, site_dhash)
    if d_dist is not None and d_dist <= config.dhash_threshold:
        candidates.append(MatchResult(LEVEL_DHASH, 1.0 - (d_dist / 64.0), CONFIDENCE_HIGH))

    if not candidates:
        return None
    return max(candidates, key=lambda m: m.score)


def confidence_for(similarity: float, config: MatchConfig) -> str:
    if similarity >= config.clip_similarity_high:
        return CONFIDENCE_HIGH
    if similarity >= config.clip_similarity_medium:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_TO_VERIFY


def classify_level2(
    ref_embedding: Optional[np.ndarray],
    site_embedding: Optional[np.ndarray],
    config: MatchConfig,
) -> Optional[MatchResult]:
    similarity = cosine_similarity(ref_embedding, site_embedding)
    if similarity is None or similarity < config.clip_similarity_floor:
        return None
    return MatchResult(LEVEL_CLIP, similarity, confidence_for(similarity, config))


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


# --- vectorized kernel -------------------------------------------------------

class MatchStopped(Exception):
    """Raised when a match pass is asked to stop before it writes."""


_BITCOUNT = np.array([int(i).bit_count() for i in range(256)], dtype=np.uint8)
_CHUNK = 2048


def _popcount(values: np.ndarray) -> np.ndarray:
    flat = np.ascontiguousarray(values, dtype=np.uint64).ravel()
    raw = flat.view(np.uint8).reshape(-1, 8)
    counts = _BITCOUNT[raw].sum(axis=1)
    return counts.reshape(values.shape)


def _u64(value: Optional[str]) -> tuple[int, bool]:
    if not value:
        return 0, False
    return int(value, 16) & ((1 << 64) - 1), True


def _embedding_matrix(vectors: list[Optional[np.ndarray]]) -> tuple[Optional[np.ndarray], np.ndarray]:
    present = [vector for vector in vectors if vector is not None and vector.size]
    mask = np.array([vector is not None and vector.size > 0 for vector in vectors], dtype=bool)
    if not present:
        return None, mask
    dim = int(present[0].shape[0])
    matrix = np.zeros((len(vectors), dim), dtype=np.float32)
    for index, vector in enumerate(vectors):
        if vector is not None and vector.shape[0] == dim:
            matrix[index] = vector
        else:
            mask[index] = False
    return matrix, mask


@dataclass
class Packed:
    """One side of the comparison, as numpy arrays."""

    ids: list
    phash: np.ndarray
    dhash: np.ndarray
    p_ok: np.ndarray
    d_ok: np.ndarray
    emb: Optional[np.ndarray]
    emb_ok: np.ndarray
    phash_flip: Optional[np.ndarray] = None
    dhash_flip: Optional[np.ndarray] = None
    pf_ok: Optional[np.ndarray] = None
    df_ok: Optional[np.ndarray] = None


def pack(rows: Sequence[dict[str, Any]], use_clip: bool, *, with_flip: bool = False) -> Packed:
    """`rows` are dicts with id, phash, dhash, embedding (ndarray or None) and,
    for references, phash_flip/dhash_flip."""
    n = len(rows)

    def hashes(key: str) -> tuple[np.ndarray, np.ndarray]:
        values = np.zeros(n, dtype=np.uint64)
        ok = np.zeros(n, dtype=bool)
        for index, row in enumerate(rows):
            values[index], ok[index] = _u64(row.get(key))
        return values, ok

    phash, p_ok = hashes("phash")
    dhash, d_ok = hashes("dhash")
    if use_clip:
        vectors = [None if row.get("embedding") is None else np.asarray(row["embedding"], dtype=np.float32) for row in rows]
        emb, emb_ok = _embedding_matrix(vectors)
    else:
        emb, emb_ok = None, np.zeros(n, dtype=bool)
    packed = Packed([row["id"] for row in rows], phash, dhash, p_ok, d_ok, emb, emb_ok)
    if with_flip:
        packed.phash_flip, packed.pf_ok = hashes("phash_flip")
        packed.dhash_flip, packed.df_ok = hashes("dhash_flip")
    return packed


def _distance(ref: np.ndarray, site: np.ndarray, flip: Optional[np.ndarray], flip_ok: Optional[np.ndarray]) -> np.ndarray:
    dist = _popcount(np.bitwise_xor(ref[:, None], site[None, :]))
    if flip is not None and flip_ok is not None and flip_ok.any():
        mirrored = _popcount(np.bitwise_xor(flip[:, None], site[None, :]))
        dist = np.where(flip_ok[:, None], np.minimum(dist, mirrored), dist)
    return dist


def compare(
    refs: Packed,
    sites: Packed,
    config: MatchConfig,
    use_clip: bool,
    progress=None,
    should_stop=None,
) -> list[tuple[Any, Any, str, float, str]]:
    """Compare refs × sites in chunks. Level 1 is a Hamming matrix; CLIP is a dot product.

    Embeddings are assumed L2-normalized, which is how they are stored.
    """
    hits: list[tuple[Any, Any, str, float, str]] = []
    n_sites = len(sites.ids)
    if not refs.ids or not n_sites:
        return hits
    chunks = max(1, (n_sites + _CHUNK - 1) // _CHUNK)
    for chunk_index, start in enumerate(range(0, n_sites, _CHUNK)):
        if should_stop and should_stop():
            raise MatchStopped()
        sl = slice(start, min(start + _CHUNK, n_sites))
        p_dist = _distance(refs.phash, sites.phash[sl], refs.phash_flip, refs.pf_ok)
        d_dist = _distance(refs.dhash, sites.dhash[sl], refs.dhash_flip, refs.df_ok)
        p_hit = refs.p_ok[:, None] & sites.p_ok[sl][None, :] & (p_dist <= config.phash_threshold)
        d_hit = refs.d_ok[:, None] & sites.d_ok[sl][None, :] & (d_dist <= config.dhash_threshold)
        p_score = np.where(p_hit, 1.0 - (p_dist / 64.0), -1.0)
        d_score = np.where(d_hit, 1.0 - (d_dist / 64.0), -1.0)
        level1 = p_hit | d_hit
        prefer_p = p_score >= d_score
        for i, j in zip(*[axis.tolist() for axis in np.nonzero(level1)]):
            if prefer_p[i, j]:
                hits.append((refs.ids[i], sites.ids[start + j], LEVEL_PHASH, float(p_score[i, j]), CONFIDENCE_HIGH))
            else:
                hits.append((refs.ids[i], sites.ids[start + j], LEVEL_DHASH, float(d_score[i, j]), CONFIDENCE_HIGH))
        if use_clip and refs.emb is not None and sites.emb is not None:
            sims = refs.emb @ sites.emb[sl].T
            both = (~level1) & refs.emb_ok[:, None] & sites.emb_ok[sl][None, :]
            for i, j in zip(*[axis.tolist() for axis in np.nonzero(both & (sims >= config.clip_similarity_floor))]):
                similarity = float(sims[i, j])
                hits.append((refs.ids[i], sites.ids[start + j], LEVEL_CLIP, similarity, confidence_for(similarity, config)))
        if progress:
            progress(chunk_index + 1, chunks)
    return hits


def match_index_pairs(
    ref_ids, ref_phash, ref_dhash, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok,
    site_ids, site_phash, site_dhash, site_p_ok, site_d_ok, site_emb, site_emb_ok,
    config: MatchConfig, use_clip: bool, progress=None, should_stop=None,
):
    """Array-level entry point kept for callers that pack their own arrays."""
    return compare(
        Packed(list(ref_ids), ref_phash, ref_dhash, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok),
        Packed(list(site_ids), site_phash, site_dhash, site_p_ok, site_d_ok, site_emb, site_emb_ok),
        config, use_clip, progress=progress, should_stop=should_stop,
    )


# --- orchestration -----------------------------------------------------------

class MatchStore(Protocol):
    """What `run_matching` needs from a persistence layer.

    Feature rows are dicts: id, phash, dhash, embedding (ndarray or None),
    compared_at (None when never compared) and, for references,
    phash_flip / dhash_flip.
    """

    def load_features(self, use_clip: bool) -> tuple[list[dict], list[dict]]: ...

    def get_signature(self) -> Optional[str]: ...

    def load_exclusions(self) -> list[tuple[str, str]]:
        """(hash, "phash" | "dhash") of images never to match again."""

    def save_matches(
        self,
        *,
        full: bool,
        clear_ref_ids: list,
        clear_site_ids: list,
        hits: list[tuple],
        signature: str,
    ) -> int: ...


def signature(config: MatchConfig, use_clip: bool, exclusions: Sequence[tuple[str, str]] = ()) -> str:
    """Anything that changes which pairs match. A different value forces a full recompute."""
    import hashlib

    excluded = hashlib.sha1(chr(10).join(sorted(f"{kind}:{value}" for value, kind in exclusions)).encode()).hexdigest()[:12]
    return "|".join(
        [
            str(config.phash_threshold),
            str(config.dhash_threshold),
            str(config.clip_similarity_high),
            str(config.clip_similarity_medium),
            str(config.clip_similarity_floor),
            f"{config.clip_model_name}/{config.clip_pretrained}" if use_clip else "hash",
            "flip",
            f"x{excluded}" if exclusions else "x0",
        ]
    )


def excluded_site_ids(sites: Sequence[dict], exclusions: Sequence[tuple[str, str]], config: MatchConfig) -> set:
    """Site images within the usual Hamming threshold of an excluded hash.

    Exclusions are for recurring false positives — a logo, a generic
    asset used everywhere — so a re-encoded or resized copy of the same
    image is excluded too, not only the exact bytes.
    """
    if not sites or not exclusions:
        return set()
    packed = pack(sites, use_clip=False)
    hit = np.zeros(len(sites), dtype=bool)
    for kind, values, ok, threshold in (
        ("phash", packed.phash, packed.p_ok, config.phash_threshold),
        ("dhash", packed.dhash, packed.d_ok, config.dhash_threshold),
    ):
        refs = np.array([_u64(value)[0] for value, k in exclusions if k == kind and value], dtype=np.uint64)
        if not refs.size:
            continue
        dist = _popcount(np.bitwise_xor(refs[:, None], values[None, :]))
        hit |= ok & (dist <= threshold).any(axis=0)
    return {packed.ids[index] for index in np.nonzero(hit)[0].tolist()}


def run_matching(store: "MatchStore | str | Path", config: Config, use_clip: bool = True, progress=None, should_stop=None) -> int:
    """Match references against site images and persist hits.

    A finished pass is remembered. The next one only compares what is new
    (references × all site images, then older references × new site
    images), unless the thresholds, the model or the CLIP switch changed —
    then everything is recomputed. Hits are written in one go after the
    comparison, so a stop or a crash keeps the previous results.
    """
    if isinstance(store, (str, Path)):
        from nyra.db import LocalStore

        store = LocalStore(store)

    exclusions = list(store.load_exclusions()) if hasattr(store, "load_exclusions") else []
    sig = signature(config.match, use_clip, exclusions)
    refs, all_sites = store.load_features(use_clip)
    excluded = excluded_site_ids(all_sites, exclusions, config.match)
    sites = [row for row in all_sites if row["id"] not in excluded]
    full = store.get_signature() != sig

    if full:
        rectangles = [(refs, sites)]
        clear_ref_ids = [row["id"] for row in refs]
        clear_site_ids = [row["id"] for row in all_sites]
    else:
        new_refs = [row for row in refs if row["compared_at"] is None]
        new_sites = [row for row in sites if row["compared_at"] is None]
        old_refs = [row for row in refs if row["compared_at"] is not None]
        rectangles = [(new_refs, sites), (old_refs, new_sites)]
        clear_ref_ids = [row["id"] for row in new_refs]
        clear_site_ids = [row["id"] for row in new_sites]
        if not clear_ref_ids and not clear_site_ids:
            return store.save_matches(full=False, clear_ref_ids=[], clear_site_ids=[], hits=[], signature=sig)

    prepared = [
        (pack(ref_part, use_clip, with_flip=True), pack(site_part, use_clip))
        for ref_part, site_part in rectangles
        if ref_part and site_part
    ]
    total_steps = sum(max(1, (len(site_pack.ids) + _CHUNK - 1) // _CHUNK) for _, site_pack in prepared)
    done_steps = 0

    def on_chunk(_done: int, _total: int) -> None:
        nonlocal done_steps
        done_steps += 1
        if progress:
            progress(done_steps, max(total_steps, 1))

    hits: list[tuple] = []
    seen: set[tuple] = set()
    for ref_pack, site_pack in prepared:
        for hit in compare(ref_pack, site_pack, config.match, use_clip, progress=on_chunk, should_stop=should_stop):
            key = (hit[0], hit[1])
            if key not in seen:
                seen.add(key)
                hits.append(hit)

    if should_stop and should_stop():
        raise MatchStopped()

    return store.save_matches(
        full=full, clear_ref_ids=clear_ref_ids, clear_site_ids=clear_site_ids, hits=hits, signature=sig
    )


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
        for row in csv.DictReader(f):
            label = row["label"].strip().lower()
            pairs.append(
                GroundTruthPair(
                    ref_filename=row["ref_filename"].strip(),
                    site_url=row["site_url"].strip(),
                    is_match=label in {"match", "1", "true", "yes"},
                )
            )
    return pairs


def _min_flip(ref: dict, site: dict, key: str) -> Optional[int]:
    distances = [
        d for d in (hamming_distance(ref.get(key), site.get(key)), hamming_distance(ref.get(f"{key}_flip"), site.get(key)))
        if d is not None
    ]
    return min(distances) if distances else None


def sweep_features(
    labeled: list[tuple[GroundTruthPair, Optional[dict], Optional[dict]]],
) -> dict[str, list[ThresholdResult]]:
    """Threshold sweep over (label, ref_features, site_features) triples.

    Feature dicts carry phash, dhash, phash_flip, dhash_flip and embedding;
    a missing side counts as "not predicted" at every threshold.
    """
    phash_pairs, dhash_pairs, clip_pairs = [], [], []
    for gt, ref, site in labeled:
        if ref is None or site is None:
            phash_pairs.append((gt, None))
            dhash_pairs.append((gt, None))
            clip_pairs.append((gt, None))
            continue
        phash_pairs.append((gt, _min_flip(ref, site, "phash")))
        dhash_pairs.append((gt, _min_flip(ref, site, "dhash")))
        clip_pairs.append((gt, cosine_similarity(ref.get("embedding"), site.get("embedding"))))
    return {
        "phash": sweep_phash_thresholds(phash_pairs, list(range(0, 21))),
        "dhash": sweep_phash_thresholds(dhash_pairs, list(range(0, 21))),
        "clip": sweep_similarity_thresholds(clip_pairs, [round(x * 0.01, 2) for x in range(50, 100)]),
    }


def calibrate(db_path, ground_truth_path, config: Config) -> dict[str, list[ThresholdResult]]:
    """Precision/recall across thresholds from a hand-confirmed CSV (SQLite database)."""
    from nyra import db

    ground_truth = load_ground_truth_csv(ground_truth_path)
    with db.connect(db_path) as conn:
        refs = {r["filename"]: db.feature_dict(r) for r in db.get_reference_images(conn)}
        sites = {s["url"]: db.feature_dict(s) for s in db.get_site_images(conn)}
    return sweep_features([(gt, refs.get(gt.ref_filename), sites.get(gt.site_url)) for gt in ground_truth])
