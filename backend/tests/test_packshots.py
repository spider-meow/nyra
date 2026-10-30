"""AXE-527: is an image a packshot? A product on a plain background: CLIP's product probability AND a flat border, read on the pixels."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from nyra import fetch, packshots
from nyra.cloud import packshots as cloud_packshots


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _bottle_on(background: str, size: int = 600) -> Image.Image:
    img = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([size * 0.38, size * 0.12, size * 0.62, size * 0.88], radius=30, fill=(150, 60, 20))
    draw.rectangle([size * 0.46, size * 0.04, size * 0.54, size * 0.16], fill=(200, 170, 90))
    return img


def _scene(size: int = 600) -> Image.Image:
    """Not a flat border: noise all over, like a photograph of a room or a vineyard."""
    rng = np.random.default_rng(1)
    base = np.linspace(60, 200, size)[None, :, None] * np.ones((size, 1, 3))
    return Image.fromarray(np.clip(base + rng.integers(-60, 60, (size, size, 3)), 0, 255).astype(np.uint8))


@pytest.mark.parametrize("background", ["white", "black", (235, 235, 235), (20, 20, 20)])
def test_a_product_cut_out_on_a_flat_colour_has_a_flat_border(background):
    assert packshots.edge_uniformity(_bottle_on(background)) >= 0.99


def test_a_scene_a_gradient_and_a_texture_do_not():
    assert packshots.edge_uniformity(_scene()) < 0.3
    gradient = Image.fromarray(np.tile(np.linspace(0, 255, 600, dtype=np.uint8)[:, None, None], (1, 600, 3)))  # a studio floor fading to a wall
    assert packshots.edge_uniformity(gradient) < 0.6
    # a product that reaches the border (a close-up of a bottle) is not a plain background either
    close_up = _bottle_on("white")
    ImageDraw.Draw(close_up).rectangle([0, 0, 599, 40], fill=(150, 60, 20))
    assert packshots.edge_uniformity(close_up) < 0.9


def test_the_border_is_measured_when_an_image_is_read(monkeypatch):
    """A crawl keeps the measure next to the image: nothing more is read later for a new image."""
    plain = fetch.process_image(_jpeg(_bottle_on("white")), "image/jpeg", min_side_px=200, max_pixels=40_000_000)
    scene = fetch.process_image(_jpeg(_scene()), "image/jpeg", min_side_px=200, max_pixels=40_000_000)
    assert plain is not None and plain.bg_uniform >= 0.99
    assert scene is not None and scene.bg_uniform < 0.3


def test_both_signals_are_needed():
    assert packshots.is_packshot(0.95, 0.95)
    assert not packshots.is_packshot(0.95, 0.5)  # a styled product visual: CLIP says product, the backdrop is not flat
    assert not packshots.is_packshot(0.2, 0.99)  # a graphic or a cocktail on black: flat border, not a product
    assert not packshots.is_packshot(0.89, 0.9) and not packshots.is_packshot(0.9, 0.89)  # the thresholds are the limits
    assert packshots.is_packshot(packshots.PRODUCT_THRESHOLD, packshots.UNIFORM_THRESHOLD)


def test_the_stored_descriptions_match_the_prompts_and_a_product_scores_above_a_scene():
    text = packshots._text()  # raises when resources/packshot_text.npz is out of date: regenerate it with `python -m nyra.packshots`
    assert text.shape[0] == len(packshots.PRODUCT_PROMPTS) + len(packshots.OTHER_PROMPTS)
    product_look, scene_look = text[0], text[len(packshots.PRODUCT_PROMPTS)]  # what a product photo and a lifestyle photo look like to CLIP
    scores = packshots.product_probability(np.stack([product_look, scene_look]))
    assert scores[0] >= packshots.PRODUCT_THRESHOLD > 0.5 > scores[1]
    assert packshots.product_probability(np.zeros((0, text.shape[1]))).shape == (0,)
    assert ((scores >= 0) & (scores <= 1)).all()


def test_the_columns_are_looked_for_once_and_nothing_is_read_before_the_migration(monkeypatch):
    """Without migration 20261030000044 every answer is "off" and no query touches a column that is not there."""
    monkeypatch.setattr(cloud_packshots, "has_columns", lambda conn: False)

    class NoQuery:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("a query ran without the migration")

    conn = NoQuery()
    assert cloud_packshots.is_enabled(conn, "brand") is False
    assert cloud_packshots.ids(conn, "brand") == set()
    assert cloud_packshots.score_pending(conn, "brand") == 0
    assert cloud_packshots.set_enabled(conn, "org", "brand", True) is False
    cloud_packshots.measures(conn, "image", None, 0.5)  # a crawl before the migration: nothing written, nothing raised
