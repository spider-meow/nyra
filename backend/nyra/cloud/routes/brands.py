"""Brands, their sites (addresses), and the brand's overview page."""

from __future__ import annotations

import base64
import io
import uuid
from urllib.parse import urlparse

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from nyra import netguard
from nyra import report as report_module
from nyra.crawl import normalize_url

from .. import db as cloud_db
from .. import insights as cloud_insights
from .. import jobs as cloud_jobs
from .. import presence as cloud_presence
from .. import storage as cloud_storage
from .common import (
    BRAND,
    BrandScope,
    Ctx,
    active_jobs,
    admin_dep,
    brand_admin_dep,
    brand_json,
    brand_member_dep,
    enforce,
    get_ctx,
    iso,
    site_json,
    staff_dep,
    unreferenced_count,
)

router = APIRouter()


LOGO_MAX_BYTES = 4_000_000
LOGO_SIZE = 128  # px: shown at 30, so this stays sharp on a dense screen and weighs a few KB


class BrandBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(default="", max_length=63)


class SiteBody(BaseModel):
    url: str = Field(max_length=2000)
    label: str = Field(default="", max_length=60)


class SiteLabelBody(BaseModel):
    label: str = Field(default="", max_length=60)


def _brand_name_and_slug(body: BrandBody) -> tuple[str, str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Indiquez un nom de marque.")
    try:
        return name, cloud_db.slugify(body.slug or name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identifiant invalide : lettres, chiffres et tirets.") from exc


def _checked_site_url(raw: str) -> str:
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Indiquez une adresse qui commence par http:// ou https://.")
    try:
        netguard.check_url(url)
    except netguard.BlockedURL as exc:
        raise HTTPException(status_code=400, detail=f"Adresse refusée : {exc}.") from exc
    return normalize_url(url)


@router.post("/api/orgs/{org_id}/brands")
def create_brand(org_id: uuid.UUID, body: BrandBody, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Platform admins only: each brand is billed, so an organization cannot add its own."""
    name, slug = _brand_name_and_slug(body)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if cloud_db.get_organization(conn, org_id) is None:
            raise HTTPException(status_code=404, detail="Organisation introuvable.")
    enforce(ctx, org_id, "brands")
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            brand_id = cloud_db.create_brand(conn, org_id=org_id, name=name, slug=slug, created_by=claims.user_id)
            brand = cloud_db.get_brand(conn, org_id, brand_id)
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail=f"Une marque utilise déjà l'identifiant {slug}.") from exc
    return {"brand": brand_json(brand)}


@router.put("/api/orgs/{org_id}/brands/{brand_id}")
def update_brand(org_id: uuid.UUID, brand_id: uuid.UUID, body: BrandBody, member=Depends(admin_dep),
                 ctx: Ctx = Depends(get_ctx)) -> dict:
    name, slug = _brand_name_and_slug(body)
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            if not cloud_db.update_brand(conn, org_id, brand_id, name=name, slug=slug):
                raise HTTPException(status_code=404, detail="Marque introuvable.")
            brand = cloud_db.get_brand(conn, org_id, brand_id)
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail=f"Une marque utilise déjà l'identifiant {slug}.") from exc
    return {"brand": brand_json(brand)}


@router.delete("/api/orgs/{org_id}/brands/{brand_id}")
def delete_brand(org_id: uuid.UUID, brand_id: uuid.UUID, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The brand and everything that belongs to it, nothing else: a crawled image
    another brand also found keeps its file. Platform admins only."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if active_jobs(conn, brand_id, tuple(cloud_jobs.KINDS)):
            raise HTTPException(status_code=409, detail="Une tâche est en cours sur cette marque. Arrêtez-la d'abord.")
        orphans = cloud_db.delete_brand(conn, org_id, brand_id)
    if orphans is None:
        raise HTTPException(status_code=404, detail="Marque introuvable.")
    ctx.remove_files(cloud_storage.BUCKET_REFS, orphans.refs)
    ctx.remove_files(cloud_storage.BUCKET_SITE_IMAGES, orphans.site_images)
    ctx.remove_files(cloud_storage.BUCKET_REPORTS, orphans.reports)
    return {"ok": True}


def _logo_data_uri(raw: bytes) -> str:
    """The upload as a small PNG (transparency kept, orientation applied) in a data URI."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="Ce fichier n'est pas une image lisible (PNG, JPEG ou WebP).") from exc
    image = ImageOps.exif_transpose(image).convert("RGBA")
    image.thumbnail((LOGO_SIZE, LOGO_SIZE))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


@router.put(f"{BRAND}/logo")
def set_logo(file: UploadFile = File(...), scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    raw = file.file.read(LOGO_MAX_BYTES + 1)
    if len(raw) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image trop lourde : 4 Mo au plus.")
    data_uri = _logo_data_uri(raw)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_db.set_brand_logo(conn, scope.brand_id, data_uri)
    return {"logo": data_uri}


@router.delete(f"{BRAND}/logo")
def remove_logo(scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_db.delete_brand_logo(conn, scope.brand_id)
    return {"ok": True}


@router.get(f"{BRAND}/sites")
def list_sites(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_sites(conn, scope.brand_id)
    return {"sites": [site_json(row) for row in rows]}


@router.post(f"{BRAND}/sites")
def add_site(body: SiteBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    url = _checked_site_url(body.url)
    enforce(ctx, scope.org_id, "sites", brand_id=scope.brand_id)
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            site_id = cloud_db.create_site(conn, org_id=scope.org_id, brand_id=scope.brand_id, url=url,
                                           label=body.label.strip() or None)
            rows = cloud_db.list_sites(conn, scope.brand_id)
    except cloud_db.SiteTaken as exc:
        raise HTTPException(status_code=409, detail="Cette adresse appartient déjà à une autre marque.") from exc
    return {"site": site_json(next(row for row in rows if row["id"] == site_id))}


@router.put(f"{BRAND}/sites/{{site_id}}")
def update_site(site_id: uuid.UUID, body: SiteLabelBody, scope: BrandScope = Depends(brand_admin_dep),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_db.update_site_label(conn, scope.brand_id, site_id, body.label.strip() or None):
            raise HTTPException(status_code=404, detail="Adresse introuvable.")
    return {"ok": True}


@router.delete(f"{BRAND}/sites/{{site_id}}")
def delete_site(site_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The address with its pages, images and their matches."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if active_jobs(conn, scope.brand_id, ("crawl", "match", "index")):
            raise HTTPException(status_code=409, detail="Une lecture ou une comparaison est en cours. Attendez sa fin.")
        orphans = cloud_db.delete_site(conn, scope.org_id, scope.brand_id, site_id)
    if orphans is None:
        raise HTTPException(status_code=404, detail="Adresse introuvable.")
    ctx.remove_files(cloud_storage.BUCKET_SITE_IMAGES, orphans)
    return {"ok": True}


@router.get(f"{BRAND}/insights")
def brand_insights(scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return cloud_insights.brand_insights(conn, scope.brand_id)


@router.get(f"{BRAND}/overview")
def overview(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        org = cloud_db.get_organization(conn, scope.org_id)
        config = ctx.org_config(conn, scope.org_id)
        stats = cloud_db.get_stats(conn, scope.brand_id)
        matches = cloud_db.match_rows(conn, scope.brand_id)
        unmatched = cloud_db.unmatched_rows(conn, scope.brand_id)
        jobs = cloud_jobs.current(conn, scope.brand_id)
        runs = cloud_db.list_crawl_runs(conn, scope.brand_id, limit=1)
        sites = cloud_db.list_sites(conn, scope.brand_id)
        pending_index = conn.execute(
            "SELECT COUNT(*) AS c FROM reference_images WHERE brand_id = %s AND embedding IS NULL", (scope.brand_id,)
        ).fetchone()["c"]
        unreferenced_online = unreferenced_count(conn, scope.brand_id, config)
        replaced = cloud_presence.replaced_references(conn, scope.brand_id)
    last_run = runs[0] if runs else None
    return {
        "organization": {"id": str(scope.org_id), "name": org["name"], "slug": org["slug"]},
        "brand": {"id": str(scope.brand_id), "name": scope.name},
        "role": scope.member.role,
        "stats": {**stats.__dict__, "references_pending_index": pending_index},
        "dashboard": {**report_module.dashboard(matches, unmatched, replaced=replaced), "unreferenced_online": unreferenced_online},
        "jobs": jobs,
        "last_crawl": None if last_run is None else {
            "site_url": last_run["site_url"], "site_label": last_run["site_label"] or "",
            "status": last_run["status"],
            "started_at": iso(last_run["started_at"]), "finished_at": iso(last_run["finished_at"]),
            "pages_visited": last_run["pages_visited"], "images_new": last_run["images_new"],
        },
        "sites": [site_json(site) for site in sites],
        "defaults": {
            "max_pages": config.crawl.max_pages,
            "max_pages_limit": config.crawl.max_pages_limit,
            "within_days": config.report.default_within_days,
        },
    }
