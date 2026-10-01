"""Reports (per brand) and settings (per organization, shared by its brands)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nyra.config import validate_overrides, with_overrides

from .. import db as cloud_db
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, admin_dep, brand_admin_dep, brand_member_dep, enqueue, get_ctx, iso, member_dep

router = APIRouter()


class ReportBody(BaseModel):
    within_days: Optional[int] = Field(default=None, ge=0, le=3650)


class SettingsBody(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


@router.post(f"{BRAND}/reports")
def create_report(body: ReportBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    return enqueue(ctx, scope, "report", {"within_days": body.within_days})


@router.get(f"{BRAND}/reports")
def list_reports(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        reports = cloud_db.list_reports(conn, scope.brand_id)
    paths = [r[key] for r in reports for key in ("storage_path_html", "storage_path_csv", "storage_path_not_found_csv")]
    urls = ctx.sign(cloud_storage.BUCKET_REPORTS, paths)
    return {
        "reports": [
            {
                "id": str(r["id"]), "within_days": r["within_days"], "generated_at": iso(r["generated_at"]),
                "stats": r["stats"],
                "files": {
                    "report.html": urls.get(r["storage_path_html"], ""),
                    "matches.csv": urls.get(r["storage_path_csv"], ""),
                    "not_found.csv": urls.get(r["storage_path_not_found_csv"], ""),
                },
            }
            for r in reports
        ]
    }


@router.get("/api/orgs/{org_id}/settings")
def get_settings(org_id: uuid.UUID, member=Depends(member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    base = ctx.settings.base_config()
    with cloud_db.connect(ctx.settings.database_url) as conn:
        overrides = cloud_db.get_overrides(conn, org_id)
    effective = with_overrides(base, overrides)
    return {
        "overrides": overrides,
        "defaults": {"crawl": base.crawl.__dict__, "match": base.match.__dict__, "report": base.report.__dict__},
        "effective": {"crawl": effective.crawl.__dict__, "match": effective.match.__dict__,
                      "report": effective.report.__dict__},
    }


@router.put("/api/orgs/{org_id}/settings")
def put_settings(org_id: uuid.UUID, body: SettingsBody, member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    try:
        clean = validate_overrides(body.overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clean = {section: values for section, values in clean.items() if values}
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_db.set_overrides(conn, org_id, clean, member.user_id)
    return {"overrides": clean}
