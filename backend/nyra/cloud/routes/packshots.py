"""Packshots: the per-brand setting of the category of "Images en ligne" (AXE-527). The tab itself comes with `GET .../site-images?view=packshots`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .. import packshot_feedback as cloud_feedback
from .. import packshots as cloud_packshots
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, get_ctx

router = APIRouter()


class SettingBody(BaseModel):
    enabled: bool


class FeedbackBody(BaseModel):
    site_image_id: uuid.UUID
    is_packshot: bool
    note: str = Field(default="", max_length=cloud_feedback.MAX_NOTE)


def staff_dep(scope: BrandScope = Depends(brand_admin_dep)) -> BrandScope:
    """Calibrating the detector is the Nyra team's work, not a function of the client: an administrator of the organization is refused."""
    if not scope.member.staff:
        raise HTTPException(status_code=403, detail="Réservé à l'équipe Nyra.")
    return scope


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


def _require_feedback(conn, scope: BrandScope) -> None:
    """Feedback needs the table (migration 20261030000045) and a brand that turned the category on, so there are measures to judge."""
    if not cloud_feedback.has_table(conn):
        raise HTTPException(status_code=409, detail="La migration des retours sur les packshots n'est pas appliquée sur la base.")
    if not cloud_packshots.is_enabled(conn, scope.brand_id):
        raise HTTPException(status_code=409, detail="Les packshots ne sont pas activés pour cette marque.")


@router.put(f"{BRAND}/packshots/feedback")
def give_feedback(body: FeedbackBody, scope: BrandScope = Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """« C'est un packshot » or « ce n'est pas un packshot » about an image, kept with the measures and thresholds of the moment. A new feedback of the same person
    on the same image replaces the old one. It never changes the detection."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        _require_feedback(conn, scope)
        kept = cloud_feedback.give(conn, scope.org_id, scope.brand_id, scope.member.user_id, body.site_image_id, body.is_packshot, body.note)
        if kept is None:
            raise HTTPException(status_code=404, detail="Image introuvable.")
        scope.audit(conn, "packshots.feedback", "site_image", body.site_image_id, is_packshot=body.is_packshot, detected=kept["detected"])
    return {"is_packshot": body.is_packshot, "detected": kept["detected"]}


@router.delete(f"{BRAND}/packshots/feedback/{{site_image_id}}")
def take_back_feedback(site_image_id: uuid.UUID, scope: BrandScope = Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        _require_feedback(conn, scope)
        if not cloud_feedback.remove(conn, scope.brand_id, scope.member.user_id, site_image_id):
            raise HTTPException(status_code=404, detail="Aucun retour sur cette image.")
        scope.audit(conn, "packshots.feedback_delete", "site_image", site_image_id)
    return {"ok": True}


@router.get(f"{BRAND}/packshots/feedback")
def feedback_report(scope: BrandScope = Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """What the feedbacks of the brand say about the detector: false positives and negatives with their measures, precision and recall of the thresholds in force."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return cloud_feedback.report(conn, scope.brand_id)
