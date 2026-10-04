"""Group the crops, resizes and re-encodings of one photo found on a brand's sites.

Same two steps as matching a reference, between site images: CLIP embeddings
pick the likely twins of each new image (`candidate_pairs`), the keypoint
check keeps the ones that are the same photo (`verify.same_photo_pairs`), and
`link_groups` merges the pairs into groups. Pure: arrays and ids in, ids out;
`cloud/variants.py` reads and writes the database.
"""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

import numpy as np

# Twins looked at per new image: a crop's original is among its closest few. Bounds the keypoint checks.
TOP_K = 5
# A group never grows past this, so one wrong link cannot swallow a whole catalogue.
MAX_GROUP = 40
# New images compared with all of them at once: 256 x 10,000 images is 10 MB of similarities.
_CHUNK = 256


def candidate_pairs(embeddings: np.ndarray, fresh: Sequence[int], floor: float) -> list[tuple[int, int]]:
    """Index pairs worth a keypoint check, sorted: each `fresh` row with its `TOP_K` most similar rows
    (never itself) whose cosine similarity reaches `floor`. Embeddings are L2-normalized, one row per image."""
    pairs: set[tuple[int, int]] = set()
    top = min(TOP_K, len(embeddings) - 1)
    if top < 1:
        return []
    for start in range(0, len(fresh), _CHUNK):
        rows = np.asarray(fresh[start : start + _CHUNK])
        sims = embeddings[rows] @ embeddings.T
        sims[np.arange(len(rows)), rows] = -1.0
        best = np.argpartition(-sims, top - 1, axis=1)[:, :top]
        for row, index in enumerate(rows.tolist()):
            for other in best[row].tolist():
                if sims[row, other] >= floor:
                    pairs.add((min(index, other), max(index, other)))
    return sorted(pairs)


def link_groups(current: Sequence[Optional[uuid.UUID]], links: Sequence[tuple[int, int]]) -> list[Optional[uuid.UUID]]:
    """The group of every image once `links` are added to the groups it already has (`current`).

    A group keeps its smallest existing id (two merged groups keep one), a new one gets a fresh id, an image
    alone in its group gets None. A link that would make a group larger than `MAX_GROUP` is ignored."""
    parent = list(range(len(current)))
    size = [1] * len(current)

    def find(node: int) -> int:
        while parent[node] != node:  # halving the path: ends at a root
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b and size[a] + size[b] <= MAX_GROUP:
            parent[b] = a
            size[a] += size[b]

    first: dict[uuid.UUID, int] = {}
    for index, group in enumerate(current):
        if group is None:
            continue
        union(first.setdefault(group, index), index)
    for a, b in links:
        union(a, b)

    members: dict[int, list[int]] = {}
    for index in range(len(current)):
        members.setdefault(find(index), []).append(index)
    out: list[Optional[uuid.UUID]] = [None] * len(current)
    for indexes in members.values():
        if len(indexes) < 2:
            continue
        known = [current[index] for index in indexes if current[index] is not None]
        group = min(known, key=str) if known else uuid.uuid4()
        for index in indexes:
            out[index] = group
    return out
