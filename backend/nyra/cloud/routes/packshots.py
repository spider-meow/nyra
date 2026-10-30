"""Packshots: the per-brand setting of the category of "Images en ligne" (AXE-527). The tab itself comes with `GET .../site-images?view=packshots`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .. import packshots as cloud_packshots
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, get_ctx

router = APIRouter()


class SettingBody(BaseModel):
    enabled: bool


@router.get(f"{BRAND}/packshots")
def packshots_state(scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The setting, whether the migration that makes it possible is applied (`available`), and how many images are detected."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        available = cloud_packshots.has_columns(conn)
        enabled = cloud_packshots.is_enabled(conn, scope.brand_id)
        detected = len(cloud_packshots.ids(conn, scope.brand_id)) if enabled else 0
    return {"available": available, "enabled": enabled, "detected": detected}


@router.put(f"{BRAND}/packshots/setting")
def set_setting(body: SettingBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Turn the Packshots category on or off for this brand. On: the images already read are scored at once from their embedding, and an analysis job
    (`index`) is queued to measure their border (their thumbnail is read, nothing else). Nothing is hidden from the comparison either way."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_packshots.has_columns(conn):
            raise HTTPException(status_code=409, detail="La migration des packshots n'est pas appliquée sur la base.")
        if not cloud_packshots.set_enabled(conn, scope.org_id, scope.brand_id, body.enabled):
            raise HTTPException(status_code=404, detail="Marque introuvable.")
        scored = cloud_packshots.score_pending(conn, scope.brand_id) if body.enabled else 0
        scope.audit(conn, "packshots.setting", "brand", scope.brand_id, enabled=body.enabled, scored=scored)
        job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="index", created_by=scope.member.user_id) if body.enabled else None
    return {"enabled": body.enabled, "scored": scored, "job": job}
