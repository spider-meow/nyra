"""backend/nyra/labels.py: labels learned from a few examples (pure, no model, no database)."""

from __future__ import annotations

import numpy as np

from nyra import labels


def _unit(rows: np.ndarray) -> np.ndarray:
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def _cluster(centre: np.ndarray, count: int, rng: np.random.Generator) -> np.ndarray:
    return _unit(centre + 0.05 * rng.normal(size=(count, centre.shape[0])))


def _world(rng: np.random.Generator):
    centres = _unit(rng.normal(size=(3, 64)))
    return {name: _cluster(centre, 8, rng) for name, centre in zip(("logo", "packshot", "autre"), centres)}


def test_a_type_is_proposed_from_its_examples_and_only_with_enough_of_them():
    rng = np.random.default_rng(1)
    world = _world(rng)
    examples = {name: rows[:4] for name, rows in world.items()}
    for name, rows in world.items():
        assert [guess[0] for guess in labels.best_type(rows[4:], examples)] == [name] * 4
    thin = {**examples, "autre": examples["autre"][: labels.MIN_EXAMPLES - 1]}
    assert {guess[0] for guess in labels.best_type(world["autre"][4:], thin)} <= {"logo", "packshot"}  # too few examples: never proposed
    assert labels.best_type(world["logo"], {"logo": examples["logo"][:1]}) == [None] * 8
    assert labels.best_type(np.zeros((0, 64), dtype=np.float32), examples) == []


def test_leave_one_out_scores_examples_that_are_alike_and_flags_a_label_with_one_example():
    rng = np.random.default_rng(2)
    world = _world(rng)
    quality = labels.leave_one_out({"logo": world["logo"][:5], "packshot": world["packshot"][:5], "autre": world["autre"][:1]})
    assert quality["logo"]["precision"] == 1.0 and quality["logo"]["recall"] == 1.0
    assert quality["autre"]["recall"] is None and quality["autre"]["examples"] == 1


def test_a_content_score_is_high_for_a_look_the_examples_share_and_low_for_another():
    rng = np.random.default_rng(3)
    world = _world(rng)
    examples = world["logo"][:4]
    assert labels.scores(world["logo"][4:], examples).min() > labels.CONTENT_THRESHOLD
    assert labels.scores(world["packshot"], examples).max() < labels.CONTENT_THRESHOLD


def test_the_sample_spreads_over_the_looks_and_skips_what_is_already_labeled():
    rng = np.random.default_rng(4)
    world = _world(rng)
    everything = np.concatenate(list(world.values()))
    first = labels.diverse_sample(everything, 3)
    assert sorted(index // 8 for index in first) == [0, 1, 2]  # one image of each look
    assert len(set(labels.diverse_sample(everything, 100))) == len(everything)  # never twice, never more than there are
    rest = labels.diverse_sample(everything, 2, taken=list(world["logo"]) + list(world["packshot"]))
    assert {index // 8 for index in rest} == {2}  # the only look nobody labeled yet
