"""Where a library picture is used: search the crawled images for it (a job), then list the pages that show it."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db as cloud_db
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, brand_member_dep, enqueue, get_ctx, iso, safe_filename

router = APIRouter()

MAX_PAGES_PER_REQUEST = 100


def _reference(ctx: Ctx, scope: BrandScope, filename: str) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = cloud_db.get_reference_by_filename(conn, scope.brand_id, safe_filename(filename))
    if row is None:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return row


@router.post(f"{BRAND}/library/{{filename}}/locate")
def locate(filename: str, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Search every image already read on the brand's sites for this reference, crops included (a worker job:
    it checks each image, so it takes minutes on a large site)."""
    reference = _reference(ctx, scope, filename)
    return enqueue(ctx, scope, "locate", {"reference_id": str(reference["id"])})


@router.get(f"{BRAND}/library/{{filename}}/occurrences")
def occurrences(filename: str, limit: int = Query(50, ge=1, le=MAX_PAGES_PER_REQUEST), offset: int = Query(0, ge=0), tier: Optional[str] = Query(None, pattern="^review$"),
                scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    reference = _reference(ctx, scope, filename)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        summary = cloud_db.occurrence_summary(conn, scope.brand_id, reference["id"], tier=tier)
        # Results can shrink between two requests (a new search, a match set aside): never answer past the end.
        offset = min(offset, max(summary["pages"] - 1, 0) // limit * limit)
        rows = cloud_db.occurrence_pages(conn, scope.brand_id, reference["id"], limit=limit, offset=offset, tier=tier)
    urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [path for row in rows for image in row["images"]
                                                         for path in (image["thumb_path"] or image["storage_path"],) if path])
    pages = [{
        "url": row["page_url"], "image_count": row["image_count"],
        "images": [{"site_image_id": image["site_image_id"], "url": image["url"], "tier": image["tier"],
                    "thumb": urls.get(image["thumb_path"] or image["storage_path"] or "", "")} for image in row["images"]],
    } for row in rows]
    return {
        "pages": pages, "total_pages": summary["pages"], "offset": offset, "images_found": summary["images"], "review_images": summary["review_images"],
        "located_at": iso(summary["located_at"]), "last_crawled_at": iso(summary["last_crawled_at"]),
        "pages_crawled": summary["pages_crawled"],
    }
