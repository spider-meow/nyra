"""Long work as jobs (read the sites, compare) and the history of site reads."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nyra import estimate

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, brand_member_dep, enqueue, get_ctx, iso

router = APIRouter()


class CrawlBody(BaseModel):
    site_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    max_pages: Optional[int] = Field(default=None, ge=1)
    fresh: bool = False
    then_match: bool = True


@router.get(f"{BRAND}/jobs/current")
def current_jobs(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return cloud_jobs.current(conn, scope.brand_id)


@router.get(f"{BRAND}/jobs/estimate")
def estimate_crawl(site_ids: list[uuid.UUID] = Query(default=[], max_length=50), max_pages: Optional[int] = Query(default=None, ge=1),
                   fresh: bool = False, then_match: bool = True,
                   scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """How long a read of these addresses would take, from the brand's past reads (all addresses when none is given).
    An address with no usable history is None (the interface says so rather than leave it out of a total);
    `compare_seconds` is None when the comparison has never run."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        ids = site_ids or [row["id"] for row in cloud_db.list_sites(conn, scope.brand_id)]
        ids = [row["id"] for row in cloud_db.get_sites(conn, scope.brand_id, ids)]  # only this brand's addresses
        history = cloud_db.crawl_history(conn, ids)
        compare = estimate.match_estimate(cloud_db.match_history(conn, scope.brand_id)) if then_match else None
    limit = min(max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
    sites = {}
    for site_id in ids:
        runs = [row for row in history if row["site_id"] == site_id]
        sites[str(site_id)] = estimate.crawl_estimate(runs, limit, fresh)
    return {"sites": sites, "compare_seconds": compare}


@router.get(f"{BRAND}/jobs")
def recent_jobs(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return {"jobs": cloud_jobs.recent(conn, scope.brand_id)}


@router.post(f"{BRAND}/jobs/crawl")
def start_crawl(body: CrawlBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Read the chosen addresses of the brand, all of them when none is chosen."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        known = {row["id"] for row in cloud_db.list_sites(conn, scope.brand_id)}
    if not known:
        raise HTTPException(status_code=400, detail="Ajoutez d'abord une adresse à cette marque.")
    if any(site_id not in known for site_id in body.site_ids):
        raise HTTPException(status_code=404, detail="Adresse introuvable pour cette marque.")
    max_pages = min(body.max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
    return enqueue(ctx, scope, "crawl", {"site_ids": [str(site_id) for site_id in body.site_ids], "max_pages": max_pages,
                                         "fresh": body.fresh, "then_match": body.then_match})


@router.post(f"{BRAND}/jobs/match")
def start_match(scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    return enqueue(ctx, scope, "match", {})


@router.post(f"{BRAND}/jobs/{{job_id}}/cancel")
def cancel_job(job_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        job = cloud_jobs.request_cancel(conn, scope.brand_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Aucune tâche active avec cet identifiant.")
    return {"job": job}


@router.get(f"{BRAND}/scans")
def scans(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        runs = cloud_db.list_crawl_runs(conn, scope.brand_id, limit=30)
    return {
        "scans": [
            {
                "id": str(run["id"]), "site_id": str(run["site_id"]), "site_url": run["site_url"],
                "site_label": run["site_label"] or "", "status": run["status"],
                "started_at": iso(run["started_at"]), "finished_at": iso(run["finished_at"]),
                "pages_visited": run["pages_visited"], "images_found": run["images_found"],
                "images_new": run["images_new"], "blocked_by_robots": run["blocked_by_robots"],
                "errors": list(run["errors"] or [])[:8], "error_count": len(run["errors"] or []),
            }
            for run in runs
        ]
    }
