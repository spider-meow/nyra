"""Is an image a packshot? A product photographed alone on a plain background (a bottle cut out on white, grey or black).

Two signals, both needed (AXE-527, `docs/MATCHING.md`):
- CLIP zero-shot on the embedding already stored for every image: "is this a photo of a product?". CLIP alone cannot tell a packshot
  from styled product imagery: it gives about 1.0 to every photograph with a bottle in it, advertising visuals and lifestyle scenes included.
- the border of the picture is ONE flat colour (`edge_uniformity`), read on the pixels: CLIP does not see "plain background".

Rights-wise nothing here decides anything: it files an image under a category of "Images en ligne" (and out of "À qualifier" while the brand's setting is
on); the comparison with the library never reads it.

The descriptions' embeddings are computed once with the model and kept in `resources/packshot_text.npz`, so the web process, which has no torch,
can score with numpy alone. Regenerate the file with `python -m nyra.packshots` after editing the prompts.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

MODEL = ("ViT-B-32", "laion2b_s34b_b79k")  # the embedding model of `config.yaml`; the file is only valid for it
PRODUCT_PROMPTS = (
    "a packshot of a bottle on a plain white background", "a studio product photo of a cognac bottle on a white background",
    "a product photograph isolated on a plain background", "a decanter and its gift box on a white background",
    "a single product centered on a neutral backdrop",
)
OTHER_PROMPTS = (
    "a lifestyle photograph", "a photo of a person", "a landscape photograph", "an interior room", "a bottle on a bar with glasses and people",
    "a logo", "a graphic banner with text", "a close-up of glasses with a drink", "a dark moody photograph", "a flat graphic icon",
    "a vineyard", "a gift box packaging in a shop", "a pouring cocktail", "a screenshot of a website",
)
LOGIT_SCALE = 100.0  # CLIP's own temperature
# Both thresholds are strict on purpose: calling a rights-managed photograph a packshot is the costly mistake, missing a packshot costs one more image to look at.
# ponytail: picked on 96 random images of ONE brand's site, labelled by one person (13 packshots: precision 13 of 13, recall 13 of 13, the
# lower bound of the precision is about 0.77), and the uniformity threshold was chosen looking at those same images. To measure on another brand
# and on more images before relying on it (`docs/MATCHING.md`).
PRODUCT_THRESHOLD = 0.9
UNIFORM_THRESHOLD = 0.9
_FILE = Path(__file__).parent / "resources" / "packshot_text.npz"

# The border: a ring `RING` px wide on the picture shrunk to `SIDE` px; a pixel is "the background colour" when no channel is further than `TOLERANCE` from the ring's median.
SIDE = 128
RING = 6
TOLERANCE = 14


def edge_uniformity(img: Image.Image) -> float:
    """The share (0 to 1) of the pixels of the picture's border that are the border's own colour: 1.0 for a cut-out on a flat white, grey or black,
    far lower for a scene, a gradient or a textured backdrop. A transparent PNG counts with the colour its hidden pixels carry (usually black)."""
    pixels = np.asarray(img.convert("RGB").resize((SIDE, SIDE)), dtype=np.float32)
    ring = np.concatenate([pixels[:RING].reshape(-1, 3), pixels[-RING:].reshape(-1, 3), pixels[:, :RING].reshape(-1, 3), pixels[:, -RING:].reshape(-1, 3)])
    return float((np.abs(ring - np.median(ring, axis=0)).max(axis=1) < TOLERANCE).mean())


@lru_cache(maxsize=1)
def _text() -> np.ndarray:
    """The stored description embeddings (rows follow PRODUCT_PROMPTS then OTHER_PROMPTS); refuses a file that is out of date."""
    with np.load(_FILE) as data:
        stale = [str(item) for item in data["prompts"]] != list(PRODUCT_PROMPTS + OTHER_PROMPTS) or [str(item) for item in data["model"]] != list(MODEL)
        if stale:
            raise RuntimeError("resources/packshot_text.npz does not match the prompts or the model of nyra/packshots.py: run `python -m nyra.packshots`.")
        return data["embeddings"].astype(np.float32)


def product_probability(embeddings: np.ndarray) -> np.ndarray:
    """Probability (0 to 1) that each L2-normalized embedding (rows) is a photograph of a product: the share of CLIP's softmax on the product descriptions."""
    if len(embeddings) == 0:
        return np.zeros(0, dtype=np.float32)
    logits = LOGIT_SCALE * embeddings.astype(np.float32) @ _text().T
    shifted = np.exp(logits - logits.max(axis=1, keepdims=True))
    return (shifted[:, : len(PRODUCT_PROMPTS)].sum(axis=1) / shifted.sum(axis=1)).astype(np.float32)


def is_packshot(product: float, uniform: float) -> bool:
    """A packshot: a photograph of a product (`product_probability`) on a plain background (`edge_uniformity`), both above their threshold."""
    return product >= PRODUCT_THRESHOLD and uniform >= UNIFORM_THRESHOLD


def build_file() -> None:
    """Embed the descriptions with the model (needs torch and open_clip, i.e. the worker's install) and store them."""
    import open_clip
    import torch

    model, _, _ = open_clip.create_model_and_transforms(MODEL[0], pretrained=MODEL[1])
    tokenizer = open_clip.get_tokenizer(MODEL[0])
    prompts = PRODUCT_PROMPTS + OTHER_PROMPTS
    with torch.no_grad():
        vectors = model.encode_text(tokenizer(list(prompts)))
        vectors = (vectors / vectors.norm(dim=-1, keepdim=True)).numpy()
    _FILE.parent.mkdir(exist_ok=True)
    np.savez_compressed(_FILE, embeddings=vectors.astype(np.float32), prompts=np.array(prompts), model=np.array(MODEL))


if __name__ == "__main__":
    build_file()
