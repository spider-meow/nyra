"""Label images from a few examples a person chose (pure: arrays in, numbers out).

An image's score for a label is the mean similarity (CLIP embeddings, L2-normalized) of its `TOP_K` closest
examples of that label: a label with several looks (a round logo, a wide one) is not averaged into a blur.
`cloud/labels.py` reads and writes the database.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

# The four types of image a site shows; every brand starts with them.
DEFAULT_TYPES = ("logo", "packshot", "pictogramme", "autre")
# A label with fewer examples proposes nothing: three is the least that shows more than one example's look.
MIN_EXAMPLES = 3
# Examples used per label: the newest ones. Bounds the memory and the time of a proposal.
MAX_EXAMPLES = 200
TOP_K = 3
# A content label (carafe, glasses…) is proposed above this score. ponytail: a guess, not calibrated on real
# images; the upgrade path is a threshold per label picked on the examples, like the matching thresholds.
CONTENT_THRESHOLD = 0.82


def scores(embeddings: np.ndarray, examples: np.ndarray) -> np.ndarray:
    """Score of each embedding (rows) for a label whose examples are `examples` (rows): the mean of its
    `TOP_K` highest similarities."""
    sims = embeddings @ examples.T
    top = min(TOP_K, examples.shape[0])
    return np.sort(sims, axis=1)[:, -top:].mean(axis=1)


def best_type(embeddings: np.ndarray, examples: dict[str, np.ndarray]) -> list[Optional[tuple[str, float]]]:
    """For each embedding, the type with the highest score and that score; None if no type has enough examples."""
    usable = {name: rows for name, rows in examples.items() if len(rows) >= MIN_EXAMPLES}
    if not usable or len(embeddings) == 0:
        return [None] * len(embeddings)
    names = list(usable)
    matrix = np.stack([scores(embeddings, usable[name]) for name in names], axis=1)
    best = matrix.argmax(axis=1)
    return [(names[index], float(matrix[row, index])) for row, index in enumerate(best)]


def leave_one_out(examples: dict[str, np.ndarray]) -> dict[str, dict]:
    """How well the examples predict themselves: each one is classified by the others.

    Per label: `examples`, and `precision` / `recall` (None when no prediction / fewer than two examples).
    An estimate, not a measure on unseen images: examples chosen by hand are easier than the rest."""
    names = [name for name, rows in examples.items() if len(rows) >= 1]
    hits = {name: 0 for name in names}
    predicted = {name: 0 for name in names}
    for name in names:
        for index in range(len(examples[name])):
            others = {other: rows for other, rows in examples.items() if other != name and len(rows) >= MIN_EXAMPLES}
            own = np.delete(examples[name], index, axis=0)
            if len(own) >= MIN_EXAMPLES:
                others[name] = own
            guess = best_type(examples[name][index : index + 1], others)[0]
            if guess is not None and guess[0] in predicted:
                predicted[guess[0]] += 1
                hits[name] += guess[0] == name
    return {
        name: {
            "examples": len(examples[name]),
            "precision": hits[name] / predicted[name] if predicted[name] else None,
            "recall": hits[name] / len(examples[name]) if len(examples[name]) >= 2 else None,
        }
        for name in names
    }


def diverse_sample(embeddings: np.ndarray, count: int, taken: Sequence[np.ndarray] = ()) -> list[int]:
    """`count` row indexes far from each other (and from `taken`): the images that tell the most about the rest.
    Farthest-point: each pick is the row least similar to everything picked so far."""
    if len(embeddings) == 0 or count < 1:
        return []
    closest = np.full(len(embeddings), -1.0)
    for vector in taken:
        closest = np.maximum(closest, embeddings @ vector)
    picked: list[int] = []
    while len(picked) < min(count, len(embeddings)):
        index = int(closest.argmin())
        picked.append(index)
        closest = np.maximum(closest, embeddings @ embeddings[index])
        closest[index] = 2.0  # never picked twice
    return picked
