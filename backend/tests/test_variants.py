"""nyra/variants.py: which site images are worth a keypoint check, and how the pairs found make groups."""

from __future__ import annotations

import uuid

import numpy as np

from nyra import variants


def unit(*rows: list[float]) -> np.ndarray:
    matrix = np.array(rows, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def test_candidates_are_the_closest_images_above_the_floor_and_never_the_image_itself():
    embeddings = unit([1, 0, 0], [0.99, 0.1, 0], [0, 1, 0], [0.5, 0.5, 0.7])
    assert variants.candidate_pairs(embeddings, [0], floor=0.9) == [(0, 1)]
    # Only fresh rows look for twins, but any row can be the twin; pairs come out once, smaller index first.
    assert variants.candidate_pairs(embeddings, [0, 1], floor=0.9) == [(0, 1)]
    assert variants.candidate_pairs(embeddings, [2], floor=0.9) == []
    assert variants.candidate_pairs(unit([1, 0]), [0], floor=0.0) == []


def test_candidates_per_image_are_bounded():
    embeddings = unit(*([[1.0, 0.0]] + [[1.0, 0.001 * step] for step in range(1, 20)]))
    assert len(variants.candidate_pairs(embeddings, [0], floor=0.5)) == variants.TOP_K


def test_links_make_groups_that_keep_their_ids_and_merge_through_a_new_link():
    old, other = uuid.uuid4(), uuid.uuid4()
    groups = variants.link_groups([old, old, None, None, other, other], [(1, 2)])
    assert groups[:3] == [old, old, old]  # an image joins the group of its twin
    assert groups[3] is None  # alone: no group
    assert groups[4] == groups[5] == other
    merged = variants.link_groups([old, old, other, other], [(1, 2)])
    assert set(merged) == {min(old, other, key=str)}  # two groups joined by a link keep one of the two ids


def test_a_new_group_gets_an_id_and_an_image_left_alone_loses_its_group():
    groups = variants.link_groups([None, None, uuid.uuid4()], [(0, 1)])
    assert groups[0] == groups[1] and groups[0] is not None
    assert groups[2] is None  # its group has no other member any more


def test_a_group_never_grows_past_the_cap():
    count = variants.MAX_GROUP + 5
    groups = variants.link_groups([None] * count, [(index, index + 1) for index in range(count - 1)])
    sizes = {group: groups.count(group) for group in set(groups) if group is not None}
    assert sizes and max(sizes.values()) <= variants.MAX_GROUP
