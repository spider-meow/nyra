"""Is a whole image a logo? CLIP zero-shot on the embeddings already stored for every image: no new model, no brand setup.

The image's embedding is compared with the embeddings of short descriptions ("a logo", "a photograph of a product"...);
the logo probability is the share of the softmax that falls on the logo descriptions. It judges the image as a whole:
a photograph that happens to show a logo scores as a photograph. Colour statistics alone cannot tell a logo from a
white porcelain plate on a white background (tried, see `docs/MATCHING.md`), CLIP can.

The descriptions' embeddings are computed once with the model and kept in `resources/logo_text.npz`, so the web process,
which has no torch, can score with numpy alone. Regenerate the file with `python -m nyra.logos` after editing the prompts.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

MODEL = ("ViT-B-32", "laion2b_s34b_b79k")  # the embedding model of `config.yaml`; the file is only valid for it
LOGO_PROMPTS = (
    "a logo", "a brand logo on a plain background", "a wordmark logo with lettering", "an emblem or crest logo",
    "a flat graphic icon", "a monogram", "a pictogram",
)
OTHER_PROMPTS = (
    "a photograph of a product", "a photo of a bottle", "a photo of a plate or cup on a white background",
    "a photo of a person", "a photo of an interior room", "a gift box packaging", "a close-up photograph",
    "a landscape photograph", "a photo of glasses on a table", "a dark moody photograph",
)
# CLIP's own temperature: the softmax over the similarities.
LOGIT_SCALE = 100.0
# Strict on purpose: setting a real photograph aside is the costly mistake. On the 1,716 images of one brand's site, every
# image above 0.95 was a logo, and a photograph with a small emblem on it already reached 0.86.
# ponytail: measured on one brand only; check another brand's images before relying on it (docs/MATCHING.md).
THRESHOLD = 0.95
_FILE = Path(__file__).parent / "resources" / "logo_text.npz"


@lru_cache(maxsize=1)
def _text() -> np.ndarray:
    """The stored description embeddings (rows follow LOGO_PROMPTS then OTHER_PROMPTS); refuses a file that is out of date."""
    with np.load(_FILE) as data:
        stale = [str(item) for item in data["prompts"]] != list(LOGO_PROMPTS + OTHER_PROMPTS) or [str(item) for item in data["model"]] != list(MODEL)
        if stale:
            raise RuntimeError("resources/logo_text.npz does not match the prompts or the model of nyra/logos.py: run `python -m nyra.logos`.")
        return data["embeddings"].astype(np.float32)


def logo_probability(embeddings: np.ndarray) -> np.ndarray:
    """Logo probability (0 to 1) of each L2-normalized embedding (rows)."""
    if len(embeddings) == 0:
        return np.zeros(0, dtype=np.float32)
    logits = LOGIT_SCALE * embeddings.astype(np.float32) @ _text().T
    shifted = np.exp(logits - logits.max(axis=1, keepdims=True))
    return (shifted[:, : len(LOGO_PROMPTS)].sum(axis=1) / shifted.sum(axis=1)).astype(np.float32)


def is_logo(embeddings: np.ndarray) -> np.ndarray:
    """Boolean per embedding: above the threshold."""
    return logo_probability(embeddings) >= THRESHOLD


def build_file() -> None:
    """Embed the descriptions with the model (needs torch and open_clip, i.e. the worker's install) and store them."""
    import open_clip
    import torch

    model, _, _ = open_clip.create_model_and_transforms(MODEL[0], pretrained=MODEL[1])
    tokenizer = open_clip.get_tokenizer(MODEL[0])
    prompts = LOGO_PROMPTS + OTHER_PROMPTS
    with torch.no_grad():
        vectors = model.encode_text(tokenizer(list(prompts)))
        vectors = (vectors / vectors.norm(dim=-1, keepdim=True)).numpy()
    _FILE.parent.mkdir(exist_ok=True)
    np.savez_compressed(_FILE, embeddings=vectors.astype(np.float32), prompts=np.array(prompts), model=np.array(MODEL))


if __name__ == "__main__":
    build_file()
