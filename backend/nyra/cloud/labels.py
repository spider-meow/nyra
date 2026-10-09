"""Labels of a brand's images (see `nyra/labels.py` and migration 20): the database side.

What a person decided is stored (`image_labels`); what the algorithm proposes is computed from the CLIP
embeddings when asked, so a new example changes the proposals at once. Loading a brand's embeddings is
about 2 KB per image: fine up to some tens of thousands of images per brand.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Optional

import numpy as np
import psycopg

from nyra import labels as engine

from .store import _vector

DECISIONS = {"yes", "no", "clear"}


def ensure_types(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID) -> None:
    """The brand's four types exist (idempotent: a brand that already has them is left alone)."""
    conn.execute(
        "INSERT INTO labels (org_id, brand_id, kind, name) SELECT %s, %s, 'type', unnest(%s::text[]) ON CONFLICT DO NOTHING",
        (org_id, brand_id, list(engine.DEFAULT_TYPES)),
    )


def list_labels(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        """SELECT l.id, l.kind, l.name, count(il.id) FILTER (WHERE il.positive) AS examples
           FROM labels l LEFT JOIN image_labels il ON il.label_id = l.id
           WHERE l.brand_id = %s GROUP BY l.id ORDER BY l.created_at, l.name""",
        (brand_id,),
    ).fetchall()
    order = {name: index for index, name in enumerate(engine.DEFAULT_TYPES)}
    return sorted(rows, key=lambda row: (row["kind"] != "type", order.get(row["name"], 99)))


def add_content_label(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, name: str) -> dict:
    """The content label of that name (created if new)."""
    conn.execute("INSERT INTO labels (org_id, brand_id, kind, name) VALUES (%s, %s, 'content', %s) ON CONFLICT DO NOTHING",
                 (org_id, brand_id, name))
    return conn.execute("SELECT id, kind, name, org_id FROM labels WHERE brand_id = %s AND kind = 'content' AND name = %s",
                        (brand_id, name)).fetchone()


def get_label(conn: psycopg.Connection, brand_id: uuid.UUID, label_id: uuid.UUID) -> Optional[dict]:
    return conn.execute("SELECT id, kind, name, org_id FROM labels WHERE id = %s AND brand_id = %s",
                        (label_id, brand_id)).fetchone()


def delete_content_label(conn: psycopg.Connection, brand_id: uuid.UUID, label_id: uuid.UUID) -> bool:
    """The four types stay: only a content label can be deleted."""
    return conn.execute("DELETE FROM labels WHERE id = %s AND brand_id = %s AND kind = 'content'",
                        (label_id, brand_id)).rowcount > 0


def set_decision(conn: psycopg.Connection, label: dict, brand_id: uuid.UUID, decision: str, *,
                 site_image_ids: list[uuid.UUID], reference_ids: list[uuid.UUID], user_id: Optional[uuid.UUID]) -> int:
    """Record that these images are (`yes`) or are not (`no`) the label, or forget what was said (`clear`).
    Only the brand's own images are touched. A `yes` for a type replaces the image's other type. Returns how many
    rows were written or removed."""
    if decision == "yes" and label["kind"] == "type":
        conn.execute(
            """DELETE FROM image_labels il USING labels l WHERE il.label_id = l.id AND l.brand_id = %s AND l.kind = 'type'
               AND (il.site_image_id = ANY(%s) OR il.reference_id = ANY(%s))""",
            (brand_id, site_image_ids, reference_ids),
        )
    if decision == "clear":
        return conn.execute(
            "DELETE FROM image_labels WHERE label_id = %s AND (site_image_id = ANY(%s) OR reference_id = ANY(%s))",
            (label["id"], site_image_ids, reference_ids),
        ).rowcount
    positive = decision == "yes"
    written = conn.execute(
        """INSERT INTO image_labels (org_id, label_id, site_image_id, positive, created_by)
           SELECT %s, %s, si.id, %s, %s FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE s.brand_id = %s AND si.id = ANY(%s)
           ON CONFLICT (label_id, site_image_id) DO UPDATE SET positive = excluded.positive, created_at = now()""",
        (label["org_id"], label["id"], positive, user_id, brand_id, site_image_ids),
    ).rowcount
    return written + conn.execute(
        """INSERT INTO image_labels (org_id, label_id, reference_id, positive, created_by)
           SELECT %s, %s, r.id, %s, %s FROM reference_images r WHERE r.brand_id = %s AND r.id = ANY(%s)
           ON CONFLICT (label_id, reference_id) DO UPDATE SET positive = excluded.positive, created_at = now()""",
        (label["org_id"], label["id"], positive, user_id, brand_id, reference_ids),
    ).rowcount


def _decisions(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[dict]:
    """Every decision of the brand with the embedding of its image, newest first."""
    return conn.execute(
        """SELECT il.label_id, il.positive, il.site_image_id, il.reference_id, COALESCE(si.embedding, r.embedding) AS embedding
           FROM image_labels il JOIN labels l ON l.id = il.label_id
           LEFT JOIN site_images si ON si.id = il.site_image_id
           LEFT JOIN reference_images r ON r.id = il.reference_id
           WHERE l.brand_id = %s ORDER BY il.created_at DESC""",
        (brand_id,),
    ).fetchall()


def _examples(decisions: list[dict], labels: list[dict]) -> dict[Any, np.ndarray]:
    """label id -> the embeddings of its newest positive examples (at most `MAX_EXAMPLES`)."""
    found: dict[Any, list[np.ndarray]] = defaultdict(list)
    for row in decisions:
        vector = _vector(row["embedding"])
        if row["positive"] and vector is not None and len(found[row["label_id"]]) < engine.MAX_EXAMPLES:
            found[row["label_id"]].append(vector)
    return {label["id"]: np.stack(found[label["id"]]) for label in labels if found[label["id"]]}


def quality(conn: psycopg.Connection, brand_id: uuid.UUID) -> dict[str, dict]:
    """Type name -> how well its examples predict themselves (`engine.leave_one_out`)."""
    labels = [row for row in list_labels(conn, brand_id) if row["kind"] == "type"]
    by_id = _examples(_decisions(conn, brand_id), labels)
    return engine.leave_one_out({label["name"]: by_id[label["id"]] for label in labels if label["id"] in by_id})


def _candidates(conn: psycopg.Connection, brand_id: uuid.UUID, target: str) -> tuple[list[Any], np.ndarray]:
    if target == "site":
        rows = conn.execute(
            """SELECT si.id, si.embedding FROM site_images si JOIN sites s ON s.id = si.site_id
               WHERE s.brand_id = %s AND si.embedding IS NOT NULL AND si.storage_path IS NOT NULL""", (brand_id,)).fetchall()
    else:
        rows = conn.execute("SELECT id, embedding FROM reference_images WHERE brand_id = %s AND embedding IS NOT NULL",
                            (brand_id,)).fetchall()
    ids = [row["id"] for row in rows]
    return ids, (np.stack([_vector(row["embedding"]) for row in rows]) if rows else np.zeros((0, 512), dtype=np.float32))


def _entry(label: dict, source: str, score: Optional[float]) -> dict:
    return {"label_id": str(label["id"]), "name": label["name"], "kind": label["kind"], "source": source, "score": score}


def assignments(conn: psycopg.Connection, brand_id: uuid.UUID, target: str) -> dict[Any, list[dict]]:
    """image id -> its labels, for `target` 'site' (types and contents) or 'reference' (contents).

    A person's `yes` is `source: user`; the algorithm's proposals are `source: algo` with their score, and give
    way to what a person decided (a type per image, a content label unless refused)."""
    labels = list_labels(conn, brand_id)
    decisions = _decisions(conn, brand_id)
    column = "site_image_id" if target == "site" else "reference_id"
    by_id = {label["id"]: label for label in labels}
    out: dict[Any, list[dict]] = defaultdict(list)
    decided: set[tuple[Any, Any]] = set()
    typed: set[Any] = set()
    for row in decisions:
        image_id = row[column]
        if image_id is None:
            continue
        decided.add((row["label_id"], image_id))
        label = by_id[row["label_id"]]
        if row["positive"]:
            out[image_id].append(_entry(label, "user", None))
            if label["kind"] == "type":
                typed.add(image_id)
    ids, matrix = _candidates(conn, brand_id, target)
    examples = _examples(decisions, labels)
    if target == "site":
        types = {label["name"]: label for label in labels if label["kind"] == "type" and label["id"] in examples}
        guesses = engine.best_type(matrix, {name: examples[label["id"]] for name, label in types.items()})
        for image_id, guess in zip(ids, guesses):
            if guess and image_id not in typed:
                out[image_id].append(_entry(types[guess[0]], "algo", round(guess[1], 3)))
    for label in labels:
        if label["kind"] != "content" or len(examples.get(label["id"], ())) < engine.MIN_EXAMPLES or not ids:
            continue
        for image_id, score in zip(ids, engine.scores(matrix, examples[label["id"]])):
            if score >= engine.CONTENT_THRESHOLD and (label["id"], image_id) not in decided:
                out[image_id].append(_entry(label, "algo", round(float(score), 3)))
    return out


def type_example_vectors(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[np.ndarray]:
    """The embeddings of every image a person typed (for a sample that avoids what is already labeled)."""
    types = {label["id"] for label in list_labels(conn, brand_id) if label["kind"] == "type"}
    vectors = (_vector(row["embedding"]) for row in _decisions(conn, brand_id) if row["positive"] and row["label_id"] in types)
    return [vector for vector in vectors if vector is not None]


def embeddings_of(conn: psycopg.Connection, site_image_ids: list[Any]) -> dict[Any, np.ndarray]:
    rows = conn.execute("SELECT id, embedding FROM site_images WHERE id = ANY(%s) AND embedding IS NOT NULL",
                        (site_image_ids,)).fetchall()
    return {row["id"]: _vector(row["embedding"]) for row in rows}
