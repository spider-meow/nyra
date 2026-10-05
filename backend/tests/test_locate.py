"""The search for every crop of one library picture (`verify.locate`) and the pure steps of the `locate` job."""

from __future__ import annotations

import uuid

import pytest
from PIL import Image, ImageOps

pytest.importorskip("cv2")

from test_verify import jpeg, scene  # noqa: E402

from nyra import verify  # noqa: E402
from nyra.cloud.locate import distinct_files, with_group_mates  # noqa: E402
from nyra.config import MatchConfig  # noqa: E402

CONFIG = MatchConfig()


def _crop(original: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    part = original.crop(box)
    return jpeg(part.resize((part.width * 2, part.height * 2)), quality=60)


def test_locate_finds_copies_crops_and_mirrors_but_not_other_pictures():
    original = scene(1)
    images = {
        "copy": jpeg(original.resize((600, 433))),
        "tight crop": _crop(original, (300, 200, 560, 400)),  # a sliver of the reference: `compare` alone drops it
        "mirror": jpeg(ImageOps.mirror(original)),
        "other": scene(2),
    }
    found = {image_id: tier for image_id, tier, _ in verify.locate(original, list(images), images.get, CONFIG)}
    assert found["copy"] == verify.SAME
    assert found["tight crop"] == verify.REVIEW
    assert found["mirror"] == verify.SAME
    assert "other" not in found


def test_a_tiny_crop_with_few_keypoints_is_for_a_person_to_check_never_confirmed():
    original = scene(1)
    tiny = _crop(original, (350, 250, 500, 350))
    [(_, tier, verdict)] = verify.locate(original, ["tiny"], {"tiny": tiny}.get, CONFIG)
    assert tier == verify.REVIEW and verdict.inliers < CONFIG.geometric_min_inliers


def test_locate_skips_unreadable_images_reports_progress_and_stops_when_asked():
    original = scene(3)
    images = {"a": jpeg(original), "b": None, "c": jpeg(original)}
    seen: list[tuple[int, int]] = []
    found = verify.locate(original, ["a", "b", "c"], images.get, CONFIG, progress=lambda done, total: seen.append((done, total)))
    assert [image_id for image_id, _, _ in found] == ["a", "c"]
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert verify.locate(original, ["a", "c"], images.get, CONFIG, should_stop=lambda: True) == []


def test_a_flat_reference_cannot_be_searched_for():
    with pytest.raises(verify.NoDetail):
        verify.locate(Image.new("RGB", (400, 300), (200, 200, 200)), ["a"], lambda _id: None, CONFIG)


def _row(name: int, group, content_hash=None) -> dict:
    return {"id": name, "variant_group": group, "content_hash": content_hash}


def test_group_mates_of_a_hit_join_it_with_the_best_tier_of_their_group():
    g1, g2 = uuid.uuid4(), uuid.uuid4()
    rows = [_row(1, g1), _row(2, g1), _row(3, g1), _row(4, g2), _row(5, g2), _row(6, None)]
    hits = {1: (verify.REVIEW, 12, 0.0, 0.4), 3: (verify.SAME, 80, 0.5, 0.9), 4: (verify.REVIEW, 11, 0.0, 0.3)}
    saved = with_group_mates(hits, rows)
    assert set(saved) == {1, 2, 3, 4, 5}  # 6 has no group, nothing links it
    assert saved[2][0] == verify.SAME and saved[2][1] == 0  # no figures of its own
    assert saved[5][0] == verify.REVIEW
    assert saved[1] == hits[1]  # a direct hit keeps its own tier and figures


def test_one_entry_per_file_with_every_id_it_stands_for():
    rows = [_row(1, None, "h"), _row(2, None, "h"), _row(3, None, None), _row(4, None, None)]
    assert [file["ids"] for file in distinct_files(rows)] == [[1, 2], [3], [4]]
