"""Splitting a group of crops by hand (AXE-521): a person says an image is not the same photo as the rest of its group.

The automatic grouping (`cloud/variants.py`) can join two different photos that share a banner or a template. The image split here leaves its group and
is marked (`site_images.variants_split_at`): the grouping no longer searches it for twins nor offers it to a group. Splitting hides nothing, it only
gives the image its own card. Migration 20261030000046 adds the column; until it is applied nothing here does anything (one cached look at the catalogue).
"""

from __future__ import annotations

import time
import uuid

import psycopg

Row = dict

_COLUMN = {"seen": False, "asked_at": float("-inf")}  # as `cloud/packshots.py`: true for good once seen, asked again after a minute otherwise
_RECHECK_SECONDS = 60


def has_column(conn: psycopg.Connection) -> bool:
    """Whether migration 20261030000046 is applied."""
    if not _COLUMN["seen"] and time.monotonic() - _COLUMN["asked_at"] > _RECHECK_SECONDS:
        _COLUMN["asked_at"] = time.monotonic()
        _COLUMN["seen"] = conn.execute(
            "SELECT count(*) = 1 AS found FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'site_images' AND column_name = 'variants_split_at'"
        ).fetchone()["found"]
    return bool(_COLUMN["seen"])


def split(conn: psycopg.Connection, org_id: uuid.UUID, brand_id: uuid.UUID, site_image_id: uuid.UUID) -> Row | None:
    """Take an image (and the other addresses of the same file) out of its group. None when it is not the brand's; else how many addresses left and
    whether the group it left is gone (a group of one file is no group)."""
    target = conn.execute(
        """SELECT si.id, si.content_hash, si.variant_group FROM site_images si JOIN sites s ON s.id = si.site_id
           WHERE si.id = %s AND s.brand_id = %s AND si.org_id = %s""",
        (site_image_id, brand_id, org_id),
    ).fetchone()
    if target is None:
        return None
    left = conn.execute(
        """UPDATE site_images SET variant_group = NULL, variants_split_at = now(), variants_checked_at = COALESCE(variants_checked_at, now())
           WHERE org_id = %s AND (id = %s OR (%s::text IS NOT NULL AND content_hash = %s)) AND site_id IN (SELECT id FROM sites WHERE brand_id = %s)""",
        (org_id, target["id"], target["content_hash"], target["content_hash"], brand_id),
    ).rowcount
    dissolved = False
    if target["variant_group"] is not None:
        files = conn.execute(
            "SELECT count(DISTINCT COALESCE(content_hash, id::text)) AS n FROM site_images WHERE org_id = %s AND variant_group = %s",
            (org_id, target["variant_group"]),
        ).fetchone()["n"]
        if files < 2:  # one file left: it is no longer a group
            conn.execute("UPDATE site_images SET variant_group = NULL WHERE org_id = %s AND variant_group = %s", (org_id, target["variant_group"]))
            dissolved = True
    return {"addresses": left, "group_dissolved": dissolved}
