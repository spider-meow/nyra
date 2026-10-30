"""The matches page (what was found online for the library) and the reviews that annotate it."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nyra import report as report_module

from .. import db as cloud_db
from .. import presence as cloud_presence
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, brand_member_dep, get_ctx

router = APIRouter()


class ReplacedBody(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=cloud_presence.MAX_PAGES_PER_REQUEST)
    replaced: bool


class ReviewBody(BaseModel):
    reference_id: uuid.UUID
    site_image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    decision: str = ""


def _shaped(row: dict, ref_urls: dict[str, str], site_urls: dict[str, str]) -> dict:
    return {
        "reference_id": str(row["reference_id"]), "filename": row["filename"],
        "expiry_date": row["expiry_date"], "unlimited_rights": row["unlimited_rights"],
        "credit": row["credit"] or "", "notes": row["notes"] or "",
        "site_image_id": str(row["site_image_id"]), "site_url": row["site_url"],
        "content_hash": row["content_hash"], "variant_group": row["variant_group"], "site_label": row["site_label"], "read_after": row["read_after"], "seen": row["seen"], "suspect_read": row["suspect_read"],
        "unread_pages": row["unread_pages"], "level": row["level"], "score": float(row["score"]),
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
        "expiry_date": row["expiry_date"], "unlimited_rights": row["unlimited_rights"],
        "credit": row["credit"] or "", "notes": row["notes"] or "",
        "compared": row["compared"],
        "ref_thumb": ref_urls.get(row["ref_thumb_path"] or "") or ref_urls.get(row["ref_storage_path"], ""),
    }


@router.get(f"{BRAND}/matches")
def matches(within_days: Optional[int] = Query(None, ge=0, le=3650), country: list[str] = Query([], max_length=20),
            scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        rows = cloud_db.match_rows(conn, scope.brand_id)
        unmatched = cloud_db.unmatched_rows(conn, scope.brand_id)
    window = config.report.default_within_days if within_days is None else within_days
    # The label of a site (US, FR, INT...) is its country. Filtering the rows before grouping keeps the tabs and
    # their counts right: a reference is "found" only by what the chosen countries' sites show.
    countries = sorted({row["site_label"] for row in rows if row["site_label"]})
    if country:
        rows = [row for row in rows if row["site_label"] in country]
    ref_urls = ctx.sign(cloud_storage.BUCKET_REFS, [r["ref_thumb_path"] for r in rows + unmatched]
                        + [r["ref_storage_path"] for r in rows + unmatched]
                        + [r["ref_work_path"] for r in rows])
    site_urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [r["site_thumb_path"] for r in rows]
                         + [r["site_storage_path"] for r in rows])
    result = report_module.group_matches([_shaped(row, ref_urls, site_urls) for row in rows], window)
    for key in ("confirmed", "to_verify", "later", "unlimited"):
        for group in result[key]:
            for hit in group["hits"]:
                hit["site_image_ids"] = [str(item) for item in hit["site_image_ids"]]
                hit.pop("content_hash", None)
                for internal in ("variant_group", "read_after", "seen", "suspect_read", "unread_pages", "site_label"):
                    hit.pop(internal, None)
                hit["pages"] = hit["pages"][:20]
    result["not_found"] = report_module.not_found_items([_unmatched_item(row, ref_urls) for row in unmatched], window)
    result["countries"] = countries
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
        if touched:
            scope.audit(conn, "review.set", "reference", body.reference_id, decision=body.decision or "cleared", count=touched,
                        site_image_id=body.site_image_ids[0] if len(body.site_image_ids) == 1 else None)
    if body.decision and not touched:
        raise HTTPException(status_code=404, detail="Correspondance introuvable.")
    return {"ok": True, "updated": touched}


@router.get(f"{BRAND}/visuals/{{reference_id}}/presence")
def presence(reference_id: uuid.UUID, site: Optional[uuid.UUID] = None, section: Optional[str] = Query(None, max_length=200),
             state: Optional[str] = Query(None, pattern="^(still|gone|unknown)$"), marked: Optional[bool] = None,
             limit: int = Query(50, ge=1, le=cloud_presence.MAX_PAGES_PER_REQUEST), offset: int = Query(0, ge=0),
             scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Where a visual is still online, page by page (see `cloud.presence`): the counts per site version and section, and
    `limit` pages from `offset`, narrowed to a site version, a section and a state."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not conn.execute("SELECT 1 FROM reference_images WHERE id = %s AND brand_id = %s", (reference_id, scope.brand_id)).fetchone():
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        conclude = ctx.org_config(conn, scope.org_id).report.conclude_redirected_pages
        counts = cloud_presence.counts(conn, scope.brand_id, reference_id, conclude=conclude)
        rows = cloud_presence.pages(conn, scope.brand_id, reference_id, site=site, section=section, state=state, marked=marked, limit=limit,
                                    offset=offset, conclude=conclude)
        suspect = frozenset(row["site_id"] for row in cloud_presence.suspect_sites(conn, scope.brand_id))
    return {**cloud_presence.summary(counts, suspect), "total": cloud_presence.total(counts, site=site, section=section, state=state, marked=marked), "pages": [{"url": row["url"], "site_id": str(row["site_id"]), "section": row["section"], "state": row["state"], "marked": row["marked"], "checked": row["checked"], "walled": row["walled"],
                                                                                                    "redirect_url": row["redirect_url"], "redirect_hops": row["redirect_hops"], "redirect_kind": row["redirect_kind"]} for row in rows],
            "offset": offset, "redirects_concluded": conclude}


@router.get(f"{BRAND}/visuals")
def visuals(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The visuals to replace: those a person confirmed, each with how many pages still show it, were seen without it, or
    were not read again (see `cloud.presence`); `replaced` once every page was read again and none shows it."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        conclude = ctx.org_config(conn, scope.org_id).report.conclude_redirected_pages
        items = cloud_presence.visuals(cloud_presence.by_visual(conn, scope.brand_id, conclude=conclude))
        suspect = cloud_presence.suspect_sites(conn, scope.brand_id)
    urls = ctx.sign(cloud_storage.BUCKET_REFS, [path for item in items for path in (item["row"]["thumb_path"], item["row"]["storage_path"]) if path])
    today = datetime.now(timezone.utc).date()
    out = []
    for item in items:
        row = item["row"]
        expiry = row["expiry_date"].isoformat() if row["expiry_date"] else None
        left = report_module.days_until(expiry, today)
        out.append({"reference_id": str(row["reference_id"]), "filename": row["filename"], "expiry_date": expiry,
                    "days_left": left, "status": report_module.rights_status(row, left),
                    "thumb": urls.get(row["thumb_path"] or "") or urls.get(row["storage_path"] or "", ""),
                    **{key: item[key] for key in (*cloud_presence.STATES, "marked", "pages", "replaced", "redirected")}})
    return {"visuals": out, "suspect_sites": [{**row, "site_id": str(row["site_id"])} for row in suspect], "redirects_concluded": conclude}


@router.post(f"{BRAND}/visuals/{{reference_id}}/replaced")
def mark_replaced(reference_id: uuid.UUID, body: ReplacedBody, scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Mark pages as replaced for a visual (or take the mark off): where the person changed it by hand. A page that does not
    show the visual cannot be marked."""
    urls = list(dict.fromkeys(body.urls))
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not conn.execute("SELECT 1 FROM reference_images WHERE id = %s AND brand_id = %s", (reference_id, scope.brand_id)).fetchone():
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        pages = cloud_presence.pages_showing(conn, scope.brand_id, reference_id, urls)
        if set(pages) != set(urls):
            raise HTTPException(status_code=400, detail="Une des pages ne montre pas ce visuel.")
        cloud_presence.set_replaced(conn, scope.org_id, reference_id, list(pages.values()), replaced=body.replaced, user_id=scope.member.user_id)
        scope.audit(conn, "visual.replaced", "reference", reference_id, replaced=body.replaced, pages=len(pages))
    return {"ok": True, "pages": len(pages)}
