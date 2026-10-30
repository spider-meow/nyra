"""What the Nyra team says about the packshot detector, image by image (AXE-668): "it is a packshot" / "it is not".

A feedback is kept with the two measures and the two thresholds of the moment, and whether the image was detected, so the case can be replayed later
(`report` reads them back: the false positives and false negatives with their measures, and the precision and recall of the thresholds in force, with
the number of images behind each). A feedback never changes the detection: it measures, and moving a threshold is a decision taken with these figures.

Migration 20261030000045 adds the table. Until it is applied nothing is written or listed (one cached look at the catalogue).
"""

from __future__ import annotations

import time
import uuid

import psycopg

from .. import packshots as detector

Row = dict
MAX_NOTE = 200
LISTED = 200  # the false positives and the false negatives listed, each

_TABLE = {"seen": False, "asked_at": float("-inf")}  # as `cloud/packshots.py`: true for good once seen, asked again after a minute otherwise
_RECHECK_SECONDS = 60


def has_table(conn: psycopg.Connection) -> bool:
    """Whether migration 20261030000045 is applied."""
    if not _TABLE["seen"] and time.monotonic() - _TABLE["asked_at"] > _RECHECK_SECONDS:
        _TABLE["asked_at"] = time.monotonic()
        _TABLE["seen"] = conn.execute("SELECT to_regclass('public.packshot_feedback') IS NOT NULL AS found").fetchone()["found"]
    return bool(_TABLE["seen"])


def give(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, user_id: uuid.UUID, site_image_id: uuid.UUID,
         is_packshot: bool, note: str) -> Row | None:
    """Record (or replace) this person's feedback on an image of the brand, with the measures of the moment. None when the image is not the brand's."""
    image = conn.execute(
        """SELECT si.id, si.content_hash, si.packshot_score, si.bg_uniform FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE si.id = %s AND s.brand_id = %s AND si.org_id = %s""",
        (site_image_id, brand_id, org_id),
    ).fetchone()
    if image is None:
        return None
    measured = image["packshot_score"] is not None and image["bg_uniform"] is not None
    detected = measured and detector.is_packshot(image["packshot_score"], image["bg_uniform"])
    return conn.execute(
        """INSERT INTO packshot_feedback (org_id, brand_id, site_image_id, content_hash, is_packshot, packshot_score, bg_uniform,
                                          product_threshold, uniform_threshold, detected, note, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (created_by, site_image_id) DO UPDATE SET
               content_hash = EXCLUDED.content_hash, is_packshot = EXCLUDED.is_packshot, packshot_score = EXCLUDED.packshot_score,
               bg_uniform = EXCLUDED.bg_uniform, product_threshold = EXCLUDED.product_threshold, uniform_threshold = EXCLUDED.uniform_threshold,
               detected = EXCLUDED.detected, note = EXCLUDED.note, updated_at = now()
           RETURNING detected""",
        (org_id, brand_id, image["id"], image["content_hash"], is_packshot, image["packshot_score"], image["bg_uniform"],
         detector.PRODUCT_THRESHOLD, detector.UNIFORM_THRESHOLD, detected, note.strip() or None, user_id),
    ).fetchone()


def remove(conn: psycopg.Connection, brand_id: uuid.UUID, user_id: uuid.UUID, site_image_id: uuid.UUID) -> bool:
    """Take back this person's feedback on an image; False when there was none."""
    return conn.execute("DELETE FROM packshot_feedback WHERE brand_id = %s AND created_by = %s AND site_image_id = %s", (brand_id, user_id, site_image_id)).rowcount > 0


def of_images(conn: psycopg.Connection, brand_id: uuid.UUID, user_id: uuid.UUID, images: list[Row]) -> dict[str, Row]:
    """{image id: this person's feedback} for the images (rows with `id` and `content_hash`): found by the row, or by the file's hash when the site was read again."""
    if not images or not has_table(conn):
        return {}
    rows = conn.execute(
        """SELECT site_image_id, content_hash, is_packshot, note FROM packshot_feedback
           WHERE brand_id = %s AND created_by = %s AND (site_image_id = ANY(%s) OR content_hash = ANY(%s))""",
        (brand_id, user_id, [image["id"] for image in images], [image["content_hash"] for image in images if image["content_hash"]]),
    ).fetchall()
    found: dict[str, Row] = {}
    for image in images:
        mine = next((row for row in rows if row["site_image_id"] == image["id"] or (image["content_hash"] and row["content_hash"] == image["content_hash"])), None)
        if mine is not None:
            found[str(image["id"])] = {"is_packshot": mine["is_packshot"], "note": mine["note"] or ""}
    return found


def measures_of(conn: psycopg.Connection, ids: list[uuid.UUID]) -> dict[str, Row]:
    """{image id: its two measures and whether it is detected}, for the cards a platform admin judges."""
    rows = conn.execute("SELECT id, packshot_score, bg_uniform FROM site_images WHERE id = ANY(%s)", (ids,)).fetchall()
    return {
        str(row["id"]): {
            "product": row["packshot_score"],
            "border": row["bg_uniform"],
            "detected": row["packshot_score"] is not None and row["bg_uniform"] is not None and detector.is_packshot(row["packshot_score"], row["bg_uniform"]),
        }
        for row in rows
    }


def _share(hits: int, of: int) -> Row | None:
    """A rate with the number of images behind it; none when there is nothing to divide."""
    return {"value": round(hits / of, 3), "of": of} if of else None


def summarize(rows: list[Row]) -> Row:
    """What the feedbacks say about the thresholds in force: each image judged again with its stored measures against the current thresholds."""
    measured = [row for row in rows if row["packshot_score"] is not None and row["bg_uniform"] is not None]
    for row in measured:
        row["detected_now"] = detector.is_packshot(row["packshot_score"], row["bg_uniform"])
    true_pos = sum(1 for row in measured if row["is_packshot"] and row["detected_now"])
    false_pos = [row for row in measured if not row["is_packshot"] and row["detected_now"]]
    false_neg = [row for row in measured if row["is_packshot"] and not row["detected_now"]]
    return {
        "feedbacks": len(rows),
        "not_measured": len(rows) - len(measured),  # no measure when it was given: it says nothing about the thresholds
        "true_positives": true_pos,
        "false_positives": len(false_pos),
        "false_negatives": len(false_neg),
        "true_negatives": len(measured) - true_pos - len(false_pos) - len(false_neg),
        "precision": _share(true_pos, true_pos + len(false_pos)),
        "recall": _share(true_pos, true_pos + len(false_neg)),
        "thresholds": {"product": detector.PRODUCT_THRESHOLD, "uniform": detector.UNIFORM_THRESHOLD},
        "false_positive_cases": [_case(row) for row in false_pos[:LISTED]],
        "false_negative_cases": [_case(row) for row in false_neg[:LISTED]],
    }


def _case(row: Row) -> Row:
    return {
        "site_image_id": str(row["site_image_id"]) if row["site_image_id"] else None,
        "content_hash": row["content_hash"],
        "product": row["packshot_score"],
        "border": row["bg_uniform"],
        "detected_then": row["detected"],
        "note": row["note"] or "",
        "at": row["updated_at"].isoformat(),
    }


def report(conn: psycopg.Connection, brand_id: uuid.UUID) -> Row:
    """The feedbacks of a brand, one per image (the latest when several people judged it), summarized against the thresholds in force."""
    if not has_table(conn):
        return {"available": False}
    rows = conn.execute(
        """SELECT DISTINCT ON (COALESCE(content_hash, site_image_id::text, id::text)) site_image_id, content_hash, is_packshot, packshot_score, bg_uniform,
                  detected, note, updated_at
           FROM packshot_feedback WHERE brand_id = %s
           ORDER BY COALESCE(content_hash, site_image_id::text, id::text), updated_at DESC""",
        (brand_id,),
    ).fetchall()
    return {"available": True, **summarize(rows)}
