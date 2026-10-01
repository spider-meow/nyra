"""The matches page (what was found online for the library) and the reviews that annotate it."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nyra import report as report_module

from .. import db as cloud_db
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, brand_member_dep, get_ctx

router = APIRouter()


class ReviewBody(BaseModel):
    reference_id: uuid.UUID
    site_image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    decision: str = ""


def _shaped(row: dict, ref_urls: dict[str, str], site_urls: dict[str, str]) -> dict:
    return {
        "reference_id": str(row["reference_id"]), "filename": row["filename"],
        "expiry_date": row["expiry_date"], "credit": row["credit"] or "", "notes": row["notes"] or "",
        "site_image_id": str(row["site_image_id"]), "site_url": row["site_url"],
        "content_hash": row["content_hash"], "level": row["level"], "score": float(row["score"]),
        "confidence": row["confidence"], "decision": row["decision"], "pages": row["pages"],
        # The working copy when there is one: the original can weigh tens of MB.
        "ref_image": ref_urls.get(row["ref_work_path"] or "") or ref_urls.get(row["ref_storage_path"], ""),
        "ref_thumb": ref_urls.get(row["ref_thumb_path"] or "") or ref_urls.get(row["ref_storage_path"], ""),
        "site_image": site_urls.get(row["site_storage_path"] or "", ""),
        "site_thumb": site_urls.get(row["site_thumb_path"] or "") or site_urls.get(row["site_storage_path"] or "", ""),
    }


def _unmatched_item(row: dict, ref_urls: dict[str, str]) -> dict:
    return {
        "reference_id": str(row["reference_id"]), "filename": row["filename"],
        "expiry_date": row["expiry_date"], "credit": row["credit"] or "", "notes": row["notes"] or "",
        "compared": row["compared"],
        "ref_thumb": ref_urls.get(row["ref_thumb_path"] or "") or ref_urls.get(row["ref_storage_path"], ""),
    }


@router.get(f"{BRAND}/matches")
def matches(within_days: Optional[int] = Query(None, ge=0, le=3650),
            scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        rows = cloud_db.match_rows(conn, scope.brand_id)
        unmatched = cloud_db.unmatched_rows(conn, scope.brand_id)
    window = config.report.default_within_days if within_days is None else within_days
    ref_urls = ctx.sign(cloud_storage.BUCKET_REFS, [r["ref_thumb_path"] for r in rows + unmatched]
                        + [r["ref_storage_path"] for r in rows + unmatched]
                        + [r["ref_work_path"] for r in rows])
    site_urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [r["site_thumb_path"] for r in rows]
                         + [r["site_storage_path"] for r in rows])
    result = report_module.group_matches([_shaped(row, ref_urls, site_urls) for row in rows], window)
    for key in ("confirmed", "to_verify", "later"):
        for group in result[key]:
            for hit in group["hits"]:
                hit["site_image_ids"] = [str(item) for item in hit["site_image_ids"]]
                hit.pop("content_hash", None)
                hit["pages"] = hit["pages"][:20]
    result["not_found"] = report_module.not_found_items([_unmatched_item(row, ref_urls) for row in unmatched], window)
    return result


@router.post(f"{BRAND}/reviews")
def review(body: ReviewBody, scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    if body.decision not in {"", *report_module.DECISIONS}:
        raise HTTPException(status_code=400, detail="Décision inconnue.")
    if not body.site_image_ids:
        raise HTTPException(status_code=400, detail="Aucune image à annoter.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        touched = cloud_db.set_reviews(
            conn, brand_id=scope.brand_id, reference_id=body.reference_id, site_image_ids=body.site_image_ids,
            decision=body.decision, reviewed_by=scope.member.user_id,
        )
    if body.decision and not touched:
        raise HTTPException(status_code=404, detail="Correspondance introuvable.")
    return {"ok": True, "updated": touched}
