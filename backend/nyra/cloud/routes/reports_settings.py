"""Reports (per brand) and settings (per organization, shared by its brands)."""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nyra import report as report_module
from nyra.config import validate_overrides, with_overrides

from .. import audit as cloud_audit
from .. import db as cloud_db
from .. import storage as cloud_storage
from .common import (
    BRAND,
    BrandScope,
    Ctx,
    admin_dep,
    brand_admin_dep,
    brand_member_dep,
    enqueue,
    get_ctx,
    iso,
    member_dep,
    seen_as,
)

router = APIRouter()


class ReportBody(BaseModel):
    within_days: Optional[int] = Field(default=None, ge=0, le=3650)
    site_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)  # the site versions it covers; none: all of them
    include: list[Literal["expired", "soon", "check", "unreferenced"]] = Field(default_factory=lambda: list(report_module.INCLUDE),
                                                                              min_length=1, max_length=4)
    lang: Literal["fr", "en"] = "fr"  # the language of the HTML and the PDF; the CSV files stay in French


class SettingsBody(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


@router.post(f"{BRAND}/reports")
def create_report(body: ReportBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    site_ids = list(dict.fromkeys(body.site_ids))
    if site_ids:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            if len(cloud_db.get_sites(conn, scope.brand_id, site_ids)) != len(site_ids):
                raise HTTPException(status_code=400, detail="Une des versions du site choisies n'existe pas.")
    return enqueue(ctx, scope, "report", {"within_days": body.within_days, "site_ids": [str(i) for i in site_ids],
                                          "include": list(dict.fromkeys(body.include)), "lang": body.lang})


@router.get(f"{BRAND}/reports")
def list_reports(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        reports = cloud_db.list_reports(conn, scope.brand_id)
        latest_read = cloud_db.latest_read_finished(conn, scope.brand_id)
    extra = {r["id"]: (r["stats"] or {}).get("files") or {} for r in reports}  # the PDF and the CSV of the images not in the library
    paths = [r[key] for r in reports for key in ("storage_path_html", "storage_path_csv", "storage_path_not_found_csv")]
    urls = ctx.sign(cloud_storage.BUCKET_REPORTS, paths + [path for files in extra.values() for path in files.values()])
    return {
        "reports": [
            {
                "id": str(r["id"]), "within_days": r["within_days"], "generated_at": iso(r["generated_at"]),
                "stats": {key: value for key, value in (r["stats"] or {}).items() if key != "files"},
                # a read of the sites finished after the report was made: its figures may no longer be true
                "stale": bool(latest_read and latest_read > r["generated_at"]),
                "files": {
                    "report.html": urls.get(r["storage_path_html"], ""),
                    "matches.csv": urls.get(r["storage_path_csv"], ""),
                    "not_found.csv": urls.get(r["storage_path_not_found_csv"], ""),
                    **{name: urls.get(path, "") for name, path in extra[r["id"]].items()},
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
        cloud_audit.record(conn, org_id, member.user_id, "settings.update", "settings", **seen_as(member), sections=",".join(sorted(clean)))
    return {"overrides": clean}
