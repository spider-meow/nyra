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


def match_index_pairs(
    ref_ids: list[int],
    ref_phash: np.ndarray,
    ref_dhash: np.ndarray,
    ref_p_ok: np.ndarray,
    ref_d_ok: np.ndarray,
    ref_emb: Optional[np.ndarray],
    ref_emb_ok: np.ndarray,
    site_ids: list[int],
    site_phash: np.ndarray,
    site_dhash: np.ndarray,
    site_p_ok: np.ndarray,
    site_d_ok: np.ndarray,
    site_emb: Optional[np.ndarray],
    site_emb_ok: np.ndarray,
    config: MatchConfig,
    use_clip: bool,
    progress=None,
    should_stop=None,
) -> list[tuple[int, int, str, float, str]]:
    """Compare refs × sites in chunks. Level 1 is a Hamming matrix; CLIP is a dot product.

    Embeddings are assumed L2-normalized, which is how `compute_clip_embedding` stores them.
    """
    hits: list[tuple[int, int, str, float, str]] = []
    n_sites = len(site_ids)
    if not ref_ids or not n_sites:
        return hits
    chunks = max(1, (n_sites + _CHUNK - 1) // _CHUNK)
    for chunk_index, start in enumerate(range(0, n_sites, _CHUNK)):
        if should_stop and should_stop():
            raise MatchStopped()
        stop = min(start + _CHUNK, n_sites)
        sl = slice(start, stop)
        p_dist = _popcount(np.bitwise_xor(ref_phash[:, None], site_phash[sl][None, :]))
        d_dist = _popcount(np.bitwise_xor(ref_dhash[:, None], site_dhash[sl][None, :]))
        p_hit = ref_p_ok[:, None] & site_p_ok[sl][None, :] & (p_dist <= config.phash_threshold)
        d_hit = ref_d_ok[:, None] & site_d_ok[sl][None, :] & (d_dist <= config.dhash_threshold)
        p_score = np.where(p_hit, 1.0 - (p_dist / 64.0), -1.0)
        d_score = np.where(d_hit, 1.0 - (d_dist / 64.0), -1.0)
        level1 = p_hit | d_hit
        prefer_p = p_score >= d_score
        sims = None
        if use_clip and ref_emb is not None and site_emb is not None:
            sims = ref_emb @ site_emb[sl].T
        ref_index, site_index = np.nonzero(level1)
        for i, j in zip(ref_index.tolist(), site_index.tolist()):
            if prefer_p[i, j]:
                hits.append((ref_ids[i], site_ids[start + j], LEVEL_PHASH, float(p_score[i, j]), CONFIDENCE_HIGH))
            else:
                hits.append((ref_ids[i], site_ids[start + j], LEVEL_DHASH, float(d_score[i, j]), CONFIDENCE_HIGH))
        if sims is not None:
            both = (~level1) & ref_emb_ok[:, None] & site_emb_ok[sl][None, :]
            clip_hit = both & (sims >= config.clip_similarity_floor)
            ci, cj = np.nonzero(clip_hit)
            for i, j in zip(ci.tolist(), cj.tolist()):
                similarity = float(sims[i, j])
                if similarity >= config.clip_similarity_high:
                    confidence = CONFIDENCE_HIGH
                elif similarity >= config.clip_similarity_medium:
                    confidence = CONFIDENCE_MEDIUM
                else:
                    confidence = CONFIDENCE_TO_VERIFY
                hits.append((ref_ids[i], site_ids[start + j], LEVEL_CLIP, similarity, confidence))
        if progress:
            progress(chunk_index + 1, chunks)
    return hits


def _pack_rows(rows: list, use_clip: bool):
    ids = [int(row["id"]) for row in rows]
    phash = np.zeros(len(rows), dtype=np.uint64)
    dhash = np.zeros(len(rows), dtype=np.uint64)
    p_ok = np.zeros(len(rows), dtype=bool)
    d_ok = np.zeros(len(rows), dtype=bool)
    vectors: list[Optional[np.ndarray]] = []
    for index, row in enumerate(rows):
        p_value, p_known = _u64(row["phash"])
        d_value, d_known = _u64(row["dhash"])
        phash[index] = p_value
        dhash[index] = d_value
        p_ok[index] = p_known
        d_ok[index] = d_known
        if use_clip:
            vector = db_unpack(row["embedding"])
            vectors.append(None if vector is None else np.array(vector, dtype=np.float32, copy=True))
        else:
            vectors.append(None)
    matrix, mask = _embedding_matrix(vectors) if use_clip else (None, np.zeros(len(rows), dtype=bool))
    return ids, phash, dhash, p_ok, d_ok, matrix, mask


def db_unpack(blob):
    from nyra import db

    return db.unpack_embedding(blob)


def _signature(config: MatchConfig, use_clip: bool) -> str:
    return "|".join(
        [
            str(config.phash_threshold),
            str(config.dhash_threshold),
            str(config.clip_similarity_high),
            str(config.clip_similarity_medium),
            str(config.clip_similarity_floor),
            "clip" if use_clip else "hash",
        ]
    )


def _load_side(conn, table: str):
    return conn.execute(f"SELECT id, phash, dhash, embedding, compared_at FROM {table}").fetchall()


def run_matching(db_path, config: Config, use_clip: bool = True, progress=None, should_stop=None) -> int:
    """Match references against site images and persist hits.

    A finished pass is remembered. The next one only compares images that are
    new, unless the thresholds or the CLIP switch changed — then it recomputes.
    Hits are written after the comparison, so a stop or a crash keeps the
    previous report.
    """
    from nyra import db

    signature = _signature(config.match, use_clip)
    with db.connect(db_path) as conn:
        stored = db.get_match_signature(conn)
        refs = _load_side(conn, "reference_images")
        sites = _load_side(conn, "site_images")
        full = stored != signature
        if full:
            ref_rows = list(refs)
            site_passes = [list(sites)]
            clear_ref_ids = [int(row["id"]) for row in refs]
            clear_site_ids = [int(row["id"]) for row in sites]
        else:
            ref_rows = [row for row in refs if row["compared_at"] is None]
            new_sites = [row for row in sites if row["compared_at"] is None]
            if not ref_rows and not new_sites:
                return conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()["c"]
            site_passes = []
            clear_ref_ids = [int(row["id"]) for row in ref_rows]
            clear_site_ids = [int(row["id"]) for row in new_sites]
            if ref_rows:
                site_passes.append(list(sites))
            if new_sites and any(row["compared_at"] is not None for row in refs):
                ref_rows_old = [row for row in refs if row["compared_at"] is not None]
                if ref_rows_old:
                    # Second rectangle: already-compared refs against new site images.
                    # Handled below by a dedicated call, not by enlarging ref_rows.
                    pass
            old_refs = [row for row in refs if row["compared_at"] is not None] if new_sites else []
        if not full:
            old_refs = [row for row in refs if row["compared_at"] is not None] if clear_site_ids else []
        else:
            old_refs = []

    rectangles: list[tuple[list, list]] = []
    if full:
        rectangles.append((list(refs), list(sites)))
    else:
        if ref_rows:
            rectangles.append((ref_rows, list(sites)))
        if old_refs and clear_site_ids:
            rectangles.append((old_refs, [row for row in sites if row["compared_at"] is None]))

    hits: list[tuple[int, int, str, float, str]] = []
    seen: set[tuple[int, int]] = set()
    total_steps = 0
    prepared = []
    for ref_part, site_part in rectangles:
        if not ref_part or not site_part:
            continue
        prepared.append((_pack_rows(ref_part, use_clip), _pack_rows(site_part, use_clip)))
        total_steps += max(1, (len(site_part) + _CHUNK - 1) // _CHUNK)
    done_steps = 0

    def on_chunk(done: int, total: int) -> None:
        nonlocal done_steps
        done_steps += 1
        if progress:
            progress(done_steps, max(total_steps, 1))

    for (ref_pack, site_pack) in prepared:
        ref_ids, ref_ph, ref_dh, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok = ref_pack
        site_ids, site_ph, site_dh, site_p_ok, site_d_ok, site_emb, site_emb_ok = site_pack
        found = match_index_pairs(
            ref_ids, ref_ph, ref_dh, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok,
            site_ids, site_ph, site_dh, site_p_ok, site_d_ok, site_emb, site_emb_ok,
            config.match, use_clip,
            progress=on_chunk,
            should_stop=should_stop,
        )
        for hit in found:
            key = (hit[0], hit[1])
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)

    if should_stop and should_stop():
        raise MatchStopped()

    from nyra import db

    stamped_refs = sorted({int(row["id"]) for part, _site in rectangles for row in part}) if not full else clear_ref_ids
    stamped_sites = clear_site_ids if not full else [int(row["id"]) for row in sites]
    if full:
        stamped_refs = [int(row["id"]) for row in refs]
        stamped_sites = [int(row["id"]) for row in sites]
    created = db.now_iso()
    payload = [(ref_id, site_id, level, score, confidence, created) for ref_id, site_id, level, score, confidence in hits]
    with db.connect(db_path) as conn:
        if full:
            db.clear_matches(conn)
        else:
            db.delete_matches_for(conn, reference_ids=clear_ref_ids, site_ids=clear_site_ids)
        if payload:
            db.write_matches(conn, payload)
        db.stamp_compared(conn, stamped_refs, stamped_sites, signature)
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
    from nyra import db

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
