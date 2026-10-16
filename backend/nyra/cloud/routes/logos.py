"""Logos set aside: the per-brand setting, and the list of images recognised as logos, where one can be put back."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nyra import logos as detector

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .. import logos as cloud_logos
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, get_ctx

router = APIRouter()


class SettingBody(BaseModel):
    enabled: bool


class KeepBody(BaseModel):
    site_image_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)  # the files of one photo
    kept: bool


def _drop_matches(conn, org_id: uuid.UUID, ids: set) -> int:
    """What the images set aside had produced disappears at once; the next comparison recomputes the rest."""
    if not ids:
        return 0
    removed = conn.execute("DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)", (org_id, list(ids))).rowcount
    conn.execute("DELETE FROM reference_locations WHERE org_id = %s AND site_image_id = ANY(%s)", (org_id, list(ids)))
    return removed


def _rematch(ctx: Ctx, scope: BrandScope) -> Optional[dict]:
    """Ask for a comparison (the set of images left out changed). One that is running has already read the old set, so
    another is queued behind it; None only when one is already waiting, and that one will see the change."""
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            return cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="match",
                                      created_by=scope.member.user_id, behind_running=True)
    except cloud_jobs.JobConflict:
        return None


@router.get(f"{BRAND}/logos")
def logos_state(scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The setting and the images recognised as logos, best score first, with whether each is put back into the comparison."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_logos.score_pending(conn, scope.brand_id)  # images indexed before the scores existed
        enabled = cloud_logos.is_enabled(conn, scope.brand_id)
        rows = cloud_logos.list_recognised(conn, scope.brand_id)
    urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in rows])
    return {
        "enabled": enabled,
        "items": [
            {"id": str(row["id"]), "url": row["url"], "thumb_url": urls.get(row["thumb_path"] or "", ""), "width": row["width"],
             "height": row["height"], "score": round(float(row["logo_score"]), 3), "pages": row["pages"], "kept": row["logo_kept"]}
            for row in rows
        ],
    }


@router.put(f"{BRAND}/logos/setting")
def set_setting(body: SettingBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Turn setting the logos aside on or off for this brand. On: the matches they produced go at once. Either way a
    comparison is queued, since the images it covers changed."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_logos.set_enabled(conn, scope.org_id, scope.brand_id, body.enabled):
            raise HTTPException(status_code=404, detail="Marque introuvable.")
        removed = 0
        if body.enabled:
            cloud_logos.score_pending(conn, scope.brand_id)
            removed = _drop_matches(conn, scope.org_id, cloud_logos.set_aside_ids(conn, scope.brand_id))
    return {"enabled": body.enabled, "matches_removed": removed, "job": _rematch(ctx, scope)}


@router.post(f"{BRAND}/logos/keep")
def keep_logo(body: KeepBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Put the images of a logo (every file of the photo) back into the comparison (`kept`), or set them aside again."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if cloud_logos.keep(conn, scope.brand_id, body.site_image_ids, body.kept) is None:
            raise HTTPException(status_code=404, detail="Image introuvable.")
        if not body.kept:
            _drop_matches(conn, scope.org_id, cloud_logos.set_aside_ids(conn, scope.brand_id))
    return {"ok": True, "job": _rematch(ctx, scope), "threshold": detector.THRESHOLD}
