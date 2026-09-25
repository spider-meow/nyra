"""The web process: HTTP API + the built interface.

Every product route lives under `/api/orgs/{org_id}/brands/{brand_id}/...`,
gated by `cloud.auth` (a Supabase JWT, then a membership in that
organization; admin-only routes say so), then by the brand belonging to
that organization. A brand owns its library, its sites (one per market,
say) and everything derived from them. Settings and memberships stay per
organization, under `/api/orgs/{org_id}/...`. This process never crawls or runs a model:
long work is written to the `jobs` table and picked up by `nyra worker`
(see cloud/worker.py), so the web container stays small and a slow crawl
can't block a request.

There is no public sign-up: organizations and their first admin are
created with `nyra cloud-provision-org`, and people are added with
`nyra cloud-invite` (Supabase sends the invitation e-mail).
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nyra import fetch, netguard
from nyra import match as match_module
from nyra import report as report_module
from nyra.config import Config, load_config, validate_overrides, with_overrides
from nyra.crawl import normalize_url
from nyra.refs import RefValidationError, parse_expiry, reference_features

from . import auth as cloud_auth
from . import db as cloud_db
from . import jobs as cloud_jobs
from . import storage as cloud_storage

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
MAX_UPLOAD_BYTES = 30_000_000
MAX_FILES_PER_UPLOAD = 100
MAX_CSV_BYTES = 2_000_000

BRAND = "/api/orgs/{org_id}/brands/{brand_id}"

log = logging.getLogger("nyra.api")


def frontend_dir() -> Path:
    """The built interface lives next to the backend, not inside the Python package."""
    packaged = Path(__file__).resolve().parents[3] / "frontend"
    if packaged.is_dir():
        return packaged
    return Path.cwd() / "frontend"


def safe_filename(name: str) -> str:
    base = Path(name).name.strip()
    if not base or base in {".", ".."} or "/" in base or "\\" in base or "\x00" in base or len(base) > 200:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    if Path(base).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Format non pris en charge : {base}")
    return base


class CloudSettings:
    def __init__(
        self,
        *,
        database_url: str,
        supabase_url: str,
        service_role_key: str,
        jwt_secret: Optional[str],
        anon_key: str = "",
        config_path: Optional[Path] = None,
    ):
        self.database_url = database_url
        self.supabase_url = supabase_url
        self.service_role_key = service_role_key
        self.jwt_secret = jwt_secret
        self.anon_key = anon_key
        self.config_path = config_path
        self._storage = None

    def base_config(self) -> Config:
        return load_config(self.config_path)

    def storage_client(self):
        if self._storage is None:
            self._storage = cloud_storage.get_client(self.supabase_url, self.service_role_key)
        return self._storage


class MetaBody(BaseModel):
    expiry_date: str = ""
    credit: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)


class FilenamesBody(BaseModel):
    filenames: list[str] = Field(default_factory=list, max_length=5000)


class BulkExpiryBody(FilenamesBody):
    expiry_date: str = ""


class BrandBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(default="", max_length=63)


class SiteBody(BaseModel):
    url: str = Field(max_length=2000)
    label: str = Field(default="", max_length=60)


class SiteLabelBody(BaseModel):
    label: str = Field(default="", max_length=60)


class CrawlBody(BaseModel):
    site_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    max_pages: Optional[int] = Field(default=None, ge=1)
    fresh: bool = False
    then_match: bool = True


class ReportBody(BaseModel):
    within_days: Optional[int] = Field(default=None, ge=0, le=3650)


class ReviewBody(BaseModel):
    reference_id: uuid.UUID
    site_image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    decision: str = ""


class ExclusionBody(BaseModel):
    site_image_id: uuid.UUID
    reason: str = Field(default="", max_length=500)


class AdoptBody(BaseModel):
    site_image_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    expiry_date: str = ""
    credit: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)
    # Optional name per image (site image id -> file name); otherwise taken from the image's address.
    filenames: dict[str, str] = Field(default_factory=dict)


class SettingsBody(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@dataclass(frozen=True)
class BrandScope:
    """The caller's membership, and a brand checked to belong to their organization."""
    member: cloud_auth.Member
    org_id: uuid.UUID
    brand_id: uuid.UUID
    name: str


def _brand(row: dict) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "slug": row["slug"]}


def _site(row: dict) -> dict:
    return {"id": str(row["id"]), "url": row["url"], "label": row["label"] or "", "images": row["images"],
            "last_crawled_at": _iso(row["last_crawled_at"])}


def _brand_name_and_slug(body: BrandBody) -> tuple[str, str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Indiquez un nom de marque.")
    try:
        return name, cloud_db.slugify(body.slug or name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identifiant invalide : lettres, chiffres et tirets.") from exc


def filename_from_url(url: str, storage_path: str) -> str:
    """A library file name for an image read at `url`: its last path segment, with an image extension."""
    stem = Path(unquote(urlparse(url).path)).name.strip() or "image"
    suffix = Path(stem).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        stored = Path(storage_path).suffix.lower()
        stem = f"{stem}{stored if stored in IMAGE_SUFFIXES else '.jpg'}"
    stem = stem.replace("\\", "_").replace("\x00", "")
    if len(stem) > 200:
        suffix = Path(stem).suffix
        stem = stem[: 200 - len(suffix)] + suffix
    return stem


def unique_filename(name: str, taken: set[str]) -> str:
    """`photo.jpg`, then `photo (2).jpg`… so an addition never replaces a visual already in the library."""
    if name not in taken:
        return name
    base, suffix = Path(name).stem, Path(name).suffix
    index = 2
    while f"{base} ({index}){suffix}" in taken:
        index += 1
    return f"{base} ({index}){suffix}"


def _upload_failure(exc: Exception) -> str:
    """Why a file couldn't be added, in words an admin can act on (the full trace goes to the log)."""
    detail = str(exc).strip()[:240] or type(exc).__name__
    if cloud_storage.is_transient(exc):
        return f"Coupure réseau avec le stockage, même après plusieurs essais. Cliquez sur « Réessayer ». ({detail})"
    if isinstance(exc, psycopg.Error):
        return f"Enregistrement en base impossible : {detail}"
    if type(exc).__module__.startswith(("storage3", "supabase", "httpx", "httpcore")):
        return f"Le stockage a refusé le fichier : {detail}"
    return f"Erreur inattendue ({type(exc).__name__}) : {detail}"


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


def _content_security_policy(supabase_url: str) -> str:
    supabase = f"{urlparse(supabase_url).scheme}://{urlparse(supabase_url).netloc}" if supabase_url else ""
    return "; ".join([
        "default-src 'self'",
        f"img-src 'self' data: blob: {supabase}".strip(),
        f"connect-src 'self' {supabase}".strip(),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ])


def settings_from_env(config_path: Optional[Path] = None) -> CloudSettings:
    """Settings from the environment (and a `.env` file when there is one)."""
    import os

    from nyra.envfile import load_env_files

    load_env_files()
    missing = [key for key in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)} (see .env.example).")
    return CloudSettings(
        database_url=os.environ["DATABASE_URL"],
        supabase_url=os.environ["SUPABASE_URL"],
        service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        anon_key=os.environ.get("SUPABASE_ANON_KEY", ""),
        config_path=config_path,
    )


def create_app(settings: CloudSettings) -> FastAPI:
    app = FastAPI(title="Nyra", docs_url=None, redoc_url=None, openapi_url=None)
    csp = _content_security_policy(settings.supabase_url)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", csp)
        return response

    @app.exception_handler(psycopg.OperationalError)
    async def postgres_unavailable(request, exc: psycopg.OperationalError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Base de données injoignable. Vérifiez DATABASE_URL (mode Session, port 5432)."},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("%s %s failed", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erreur interne du serveur ({type(exc).__name__}). Le détail est dans les journaux de l'API."},
        )

    dist = frontend_dir() / "dist"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    auth_args = dict(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret, database_url=settings.database_url)
    member_dep = cloud_auth.require_member(**auth_args)
    admin_dep = cloud_auth.require_admin(**auth_args)
    user_dep = cloud_auth.require_user(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret)

    def scope_for(member: cloud_auth.Member, brand_id: uuid.UUID) -> BrandScope:
        with cloud_db.connect(settings.database_url) as conn:
            brand = cloud_db.get_brand(conn, member.org_id, brand_id)
        if brand is None:
            raise HTTPException(status_code=404, detail="Marque introuvable.")
        return BrandScope(member=member, org_id=member.org_id, brand_id=brand_id, name=brand["name"])

    def brand_member_dep(brand_id: uuid.UUID, member: cloud_auth.Member = Depends(member_dep)) -> BrandScope:
        return scope_for(member, brand_id)

    def brand_admin_dep(brand_id: uuid.UUID, member: cloud_auth.Member = Depends(admin_dep)) -> BrandScope:
        return scope_for(member, brand_id)

    def org_config(conn, org_id: uuid.UUID) -> Config:
        return with_overrides(settings.base_config(), cloud_db.get_overrides(conn, org_id))

    def sign(bucket: str, paths) -> dict[str, str]:
        try:
            return cloud_storage.signed_urls(settings.storage_client(), bucket, paths)
        except Exception:  # noqa: BLE001 - missing thumbnails must not break a page
            return {}

    def remove_files(bucket: str, paths: list[str]) -> None:
        try:
            cloud_storage.delete(settings.storage_client(), bucket, paths)
        except Exception:  # noqa: BLE001 - rows are gone; an orphan file is harmless
            pass

    def original_from_site(url: str, config: Config) -> Optional[bytes]:
        """The image as the site serves it (Storage only keeps a working copy of crawled images)."""
        try:
            with netguard.client(headers={"User-Agent": config.crawl.user_agent}) as http:
                got = fetch.download(url, http, timeout=20.0, max_bytes=MAX_UPLOAD_BYTES)
        except Exception:  # noqa: BLE001 - the stored working copy is the fallback
            return None
        return got[0] if got else None

    def unreferenced_images(conn, brand_id: uuid.UUID, config: Config) -> list[dict]:
        """Images of the brand's sites that match nothing in its library and weren't set aside,
        one entry per distinct image (the same bytes under several URLs count once)."""
        rows = cloud_db.unmatched_site_images(conn, brand_id)
        excluded = match_module.excluded_site_ids(rows, cloud_db.load_exclusions(conn, brand_id), config.match)
        groups: dict[str, dict] = {}
        for row in rows:
            if row["id"] in excluded:
                continue
            group = groups.setdefault(row["content_hash"] or str(row["id"]), {"lead": row, "rows": []})
            group["rows"].append(row)
        return list(groups.values())

    def active_jobs(conn, brand_id: uuid.UUID, kinds: tuple[str, ...]) -> bool:
        return bool(conn.execute(
            "SELECT 1 FROM jobs WHERE brand_id = %s AND kind = ANY(%s) AND status = ANY(%s) LIMIT 1",
            (brand_id, list(kinds), list(cloud_jobs.ACTIVE)),
        ).fetchone())

    # --- session ------------------------------------------------------------

    @app.get("/api/healthz")
    def healthz() -> dict:
        cloud_db.ping(settings.database_url)
        return {"status": "ok"}

    @app.get("/api/auth/config")
    def auth_config() -> dict:
        return {"supabaseUrl": settings.supabase_url, "anonKey": settings.anon_key}

    @app.get("/api/orgs")
    def list_orgs(claims: cloud_auth.Claims = Depends(user_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            memberships = cloud_db.list_memberships_for_user(conn, claims.user_id)
            brands = cloud_db.list_brands(conn, [m["org_id"] for m in memberships])
        return {
            "organizations": [
                {
                    "org_id": str(m["org_id"]), "name": m["org_name"], "slug": m["org_slug"], "role": m["role"],
                    "brands": [_brand(b) for b in brands if b["org_id"] == m["org_id"]],
                }
                for m in memberships
            ]
        }

    # --- brands and their sites -----------------------------------------------------

    @app.post("/api/orgs/{org_id}/brands")
    def create_brand(org_id: uuid.UUID, body: BrandBody, member=Depends(admin_dep)) -> dict:
        name, slug = _brand_name_and_slug(body)
        try:
            with cloud_db.connect(settings.database_url) as conn:
                brand_id = cloud_db.create_brand(conn, org_id=org_id, name=name, slug=slug)
                brand = cloud_db.get_brand(conn, org_id, brand_id)
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(status_code=409, detail=f"Une marque utilise déjà l'identifiant {slug}.") from exc
        return {"brand": _brand(brand)}

    @app.put("/api/orgs/{org_id}/brands/{brand_id}")
    def update_brand(org_id: uuid.UUID, brand_id: uuid.UUID, body: BrandBody, member=Depends(admin_dep)) -> dict:
        name, slug = _brand_name_and_slug(body)
        try:
            with cloud_db.connect(settings.database_url) as conn:
                if not cloud_db.update_brand(conn, org_id, brand_id, name=name, slug=slug):
                    raise HTTPException(status_code=404, detail="Marque introuvable.")
                brand = cloud_db.get_brand(conn, org_id, brand_id)
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(status_code=409, detail=f"Une marque utilise déjà l'identifiant {slug}.") from exc
        return {"brand": _brand(brand)}

    @app.delete("/api/orgs/{org_id}/brands/{brand_id}")
    def delete_brand(org_id: uuid.UUID, brand_id: uuid.UUID, member=Depends(admin_dep)) -> dict:
        """The brand and everything that belongs to it, nothing else: a crawled image
        another brand also found keeps its file."""
        with cloud_db.connect(settings.database_url) as conn:
            if active_jobs(conn, brand_id, tuple(cloud_jobs.KINDS)):
                raise HTTPException(status_code=409, detail="Une tâche est en cours sur cette marque. Arrêtez-la d'abord.")
            orphans = cloud_db.delete_brand(conn, org_id, brand_id)
        if orphans is None:
            raise HTTPException(status_code=404, detail="Marque introuvable.")
        remove_files(cloud_storage.BUCKET_REFS, orphans.refs)
        remove_files(cloud_storage.BUCKET_SITE_IMAGES, orphans.site_images)
        remove_files(cloud_storage.BUCKET_REPORTS, orphans.reports)
        return {"ok": True}

    @app.get(f"{BRAND}/sites")
    def list_sites(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_sites(conn, scope.brand_id)
        return {"sites": [_site(row) for row in rows]}

    @app.post(f"{BRAND}/sites")
    def add_site(body: SiteBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        url = _checked_site_url(body.url)
        try:
            with cloud_db.connect(settings.database_url) as conn:
                site_id = cloud_db.create_site(conn, org_id=scope.org_id, brand_id=scope.brand_id, url=url,
                                               label=body.label.strip() or None)
                rows = cloud_db.list_sites(conn, scope.brand_id)
        except cloud_db.SiteTaken as exc:
            raise HTTPException(status_code=409, detail="Cette adresse appartient déjà à une autre marque.") from exc
        return {"site": _site(next(row for row in rows if row["id"] == site_id))}

    @app.put(f"{BRAND}/sites/{{site_id}}")
    def update_site(site_id: uuid.UUID, body: SiteLabelBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            if not cloud_db.update_site_label(conn, scope.brand_id, site_id, body.label.strip() or None):
                raise HTTPException(status_code=404, detail="Adresse introuvable.")
        return {"ok": True}

    @app.delete(f"{BRAND}/sites/{{site_id}}")
    def delete_site(site_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        """The address with its pages, images and their matches."""
        with cloud_db.connect(settings.database_url) as conn:
            if active_jobs(conn, scope.brand_id, ("crawl", "match", "index")):
                raise HTTPException(status_code=409, detail="Une lecture ou une comparaison est en cours. Attendez sa fin.")
            orphans = cloud_db.delete_site(conn, scope.org_id, scope.brand_id, site_id)
        if orphans is None:
            raise HTTPException(status_code=404, detail="Adresse introuvable.")
        remove_files(cloud_storage.BUCKET_SITE_IMAGES, orphans)
        return {"ok": True}

    # --- overview ----------------------------------------------------------------

    @app.get(f"{BRAND}/overview")
    def overview(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            org = cloud_db.get_organization(conn, scope.org_id)
            config = org_config(conn, scope.org_id)
            stats = cloud_db.get_stats(conn, scope.brand_id)
            matches = cloud_db.match_rows(conn, scope.brand_id)
            unmatched = cloud_db.unmatched_rows(conn, scope.brand_id)
            jobs = cloud_jobs.current(conn, scope.brand_id)
            runs = cloud_db.list_crawl_runs(conn, scope.brand_id, limit=1)
            sites = cloud_db.list_sites(conn, scope.brand_id)
            pending_index = conn.execute(
                "SELECT COUNT(*) AS c FROM reference_images WHERE brand_id = %s AND embedding IS NULL", (scope.brand_id,)
            ).fetchone()["c"]
            unreferenced_count = len(unreferenced_images(conn, scope.brand_id, config))
        last_run = runs[0] if runs else None
        return {
            "organization": {"id": str(scope.org_id), "name": org["name"], "slug": org["slug"]},
            "brand": {"id": str(scope.brand_id), "name": scope.name},
            "role": scope.member.role,
            "stats": {**stats.__dict__, "references_pending_index": pending_index},
            "dashboard": {**report_module.dashboard(matches, unmatched), "unreferenced_online": unreferenced_count},
            "jobs": jobs,
            "last_crawl": None if last_run is None else {
                "site_url": last_run["site_url"], "site_label": last_run["site_label"] or "",
                "status": last_run["status"],
                "started_at": _iso(last_run["started_at"]), "finished_at": _iso(last_run["finished_at"]),
                "pages_visited": last_run["pages_visited"], "images_new": last_run["images_new"],
            },
            "sites": [_site(site) for site in sites],
            "defaults": {
                "max_pages": config.crawl.max_pages,
                "max_pages_limit": config.crawl.max_pages_limit,
                "within_days": config.report.default_within_days,
            },
        }

    # --- library --------------------------------------------------------------------

    @app.get(f"{BRAND}/library")
    def library(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_references(conn, scope.brand_id)
            indexing = active_jobs(conn, scope.brand_id, ("index",))
        urls = sign(cloud_storage.BUCKET_REFS, [row["thumb_path"] for row in rows] + [row["storage_path"] for row in rows])
        today = datetime.now(timezone.utc).date()
        items = []
        for row in rows:
            expiry = _iso(row["expiry_date"])
            left = report_module.days_until(expiry, today)
            items.append({
                "id": str(row["id"]),
                "filename": row["filename"],
                "expiry_date": expiry or "",
                "days_left": left,
                "status": report_module.urgency_status(left),
                "credit": row["credit"] or "",
                "notes": row["notes"] or "",
                "width": row["width"],
                "height": row["height"],
                "indexed": bool(row["embedded"]),
                "compared": row["compared_at"] is not None,
                "thumb_url": urls.get(row["thumb_path"] or "") or urls.get(row["storage_path"], ""),
                "url": urls.get(row["storage_path"], ""),
            })
        return {"items": items, "indexing": indexing}

    @app.post(f"{BRAND}/library/upload")
    def upload(files: list[UploadFile] = File(...), scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        if not files:
            raise HTTPException(status_code=400, detail="Aucun fichier.")
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise HTTPException(status_code=400, detail=f"{MAX_FILES_PER_UPLOAD} fichiers au plus par envoi.")
        org_id, brand_id = scope.org_id, scope.brand_id
        client = settings.storage_client()
        saved: list[str] = []
        replaced: list[str] = []  # also in `saved`: a reference of that name was already in the library
        failed: list[dict] = []
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, org_id)
        for upload_file in files:
            raw_name = upload_file.filename or ""
            try:
                filename = safe_filename(raw_name)
            except HTTPException as exc:
                failed.append({"filename": raw_name or "(sans nom)", "reason": str(exc.detail)})
                continue
            data = upload_file.file.read(MAX_UPLOAD_BYTES + 1)
            if not data:
                failed.append({"filename": filename, "reason": "Fichier vide."})
                continue
            if len(data) > MAX_UPLOAD_BYTES:
                failed.append({"filename": filename, "reason": "Fichier trop lourd (30 Mo au plus)."})
                continue
            try:
                img, refused = fetch.decode_reference(data, config.crawl.max_image_pixels)
                if img is None:
                    failed.append({"filename": filename, "reason": refused})
                    continue
                features = reference_features(img)
                width, height = img.info.get("original_size", (features.width, features.height))
                storage_path = cloud_storage.ref_path(org_id, brand_id, filename)
                thumb_path = cloud_storage.ref_thumb_path(org_id, brand_id, filename)
                work_path = cloud_storage.ref_work_path(org_id, brand_id, filename)
                content_type = upload_file.content_type if (upload_file.content_type or "").startswith("image/") else None
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, storage_path, data, content_type=content_type)
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                                     content_type="image/jpeg")
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, work_path, fetch.make_working_copy(img),
                                     content_type="image/jpeg")
                with cloud_db.connect(settings.database_url) as conn:
                    existing = cloud_db.get_reference_by_filename(conn, brand_id, filename)
                    cloud_db.upsert_reference_image(
                        conn, org_id=org_id, brand_id=brand_id, filename=filename, storage_path=storage_path,
                        expiry_date=_iso(existing["expiry_date"]) if existing else None,
                        credit=existing["credit"] if existing else None,
                        notes=existing["notes"] if existing else None,
                        phash=features.phash, dhash=features.dhash,
                        phash_flip=features.phash_flip, dhash_flip=features.dhash_flip,
                        embedding=None, width=width, height=height, thumb_path=thumb_path, work_path=work_path,
                    )
                if existing and existing["storage_path"] != storage_path:
                    # Uploaded before brands existed: the new file replaces the old one.
                    remove_files(cloud_storage.BUCKET_REFS, [existing["storage_path"], existing["thumb_path"]])
            except Exception as exc:  # noqa: BLE001 - one bad file must not sink the others
                log.exception("upload of %r to brand %s failed", filename, brand_id)
                failed.append({"filename": filename, "reason": _upload_failure(exc)})
                continue
            saved.append(filename)
            if existing:
                replaced.append(filename)
        job = None
        if saved:
            with cloud_db.connect(settings.database_url) as conn:
                job = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=brand_id, kind="index",
                                         created_by=scope.member.user_id)
        return {"saved": saved, "replaced": replaced, "failed": failed, "job": job}

    @app.put(f"{BRAND}/library/{{filename}}")
    def update_meta(filename: str, body: MetaBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        filename = safe_filename(filename)
        try:
            expiry = parse_expiry(body.expiry_date)
        except RefValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with cloud_db.connect(settings.database_url) as conn:
            found = cloud_db.update_reference_meta(
                conn, scope.brand_id, filename, expiry_date=expiry,
                credit=body.credit.strip() or None, notes=body.notes.strip() or None,
            )
        if not found:
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        return {"ok": True, "expiry_date": expiry or ""}

    def delete_filenames(brand_id: uuid.UUID, filenames: list[str]) -> int:
        with cloud_db.connect(settings.database_url) as conn:
            deleted = cloud_db.delete_references(conn, brand_id, filenames)
        remove_files(cloud_storage.BUCKET_REFS, [row[key] for row in deleted for key in ("storage_path", "thumb_path", "work_path")])
        return len(deleted)

    @app.delete(f"{BRAND}/library/{{filename}}")
    def delete_ref(filename: str, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        if not delete_filenames(scope.brand_id, [safe_filename(filename)]):
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        return {"ok": True}

    @app.post(f"{BRAND}/library/delete")
    def delete_many(body: FilenamesBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        names = [safe_filename(name) for name in body.filenames]
        return {"deleted": delete_filenames(scope.brand_id, names)}

    @app.post(f"{BRAND}/library/expiry")
    def bulk_expiry(body: BulkExpiryBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        try:
            expiry = parse_expiry(body.expiry_date)
        except RefValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        names = [safe_filename(name) for name in body.filenames]
        with cloud_db.connect(settings.database_url) as conn:
            updated = cloud_db.set_expiry_for(conn, scope.brand_id, names, expiry)
        return {"updated": updated}

    @app.post(f"{BRAND}/library/import-csv")
    def import_csv(file: UploadFile = File(...), apply: bool = Query(False),
                   scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        """Preview (default) or apply expiry dates/credits/notes from a CSV.

        Every row comes back with a status so the interface can show what
        would change before anything is written.
        """
        raw = file.file.read(MAX_CSV_BYTES + 1)
        if len(raw) > MAX_CSV_BYTES:
            raise HTTPException(status_code=400, detail="CSV trop lourd (2 Mo au plus).")
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fields = {name.strip().lower() for name in (reader.fieldnames or [])}
        if "filename" not in fields:
            raise HTTPException(status_code=400, detail="Le CSV doit contenir une colonne filename.")
        parsed = []
        for line, row in enumerate(reader, start=2):
            row = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
            name = Path(row.get("filename", "")).name
            if not name:
                continue
            entry = {"line": line, "filename": name, "expiry_date": "", "credit": row.get("credit", ""),
                     "notes": row.get("notes", ""), "status": "ok", "message": ""}
            try:
                entry["expiry_date"] = parse_expiry(row.get("expiry_date", "")) or ""
            except RefValidationError as exc:
                entry.update(status="bad_date", message=str(exc))
            parsed.append(entry)
        with cloud_db.connect(settings.database_url) as conn:
            known = cloud_db.existing_filenames(conn, scope.brand_id, [entry["filename"] for entry in parsed])
            for entry in parsed:
                if entry["status"] == "ok" and entry["filename"] not in known:
                    entry.update(status="unknown_file", message="Aucune image de ce nom dans la bibliothèque.")
            applied = 0
            if apply:
                for entry in parsed:
                    if entry["status"] == "ok":
                        cloud_db.update_reference_meta(
                            conn, scope.brand_id, entry["filename"], expiry_date=entry["expiry_date"] or None,
                            credit=entry["credit"] or None, notes=entry["notes"] or None,
                        )
                        applied += 1
        return {"rows": parsed, "applied": applied}

    @app.get(f"{BRAND}/library/export-csv")
    def export_csv(scope: BrandScope = Depends(brand_member_dep)) -> Response:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_references(conn, scope.brand_id)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["filename", "expiry_date", "credit", "notes"])
        for row in rows:
            writer.writerow([row["filename"], _iso(row["expiry_date"]) or "", row["credit"] or "", row["notes"] or ""])
        return Response(
            content=("﻿" + buf.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="references.csv"'},
        )

    # --- jobs ---------------------------------------------------------------------

    @app.get(f"{BRAND}/jobs/current")
    def current_jobs(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            return cloud_jobs.current(conn, scope.brand_id)

    @app.get(f"{BRAND}/jobs")
    def recent_jobs(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            return {"jobs": cloud_jobs.recent(conn, scope.brand_id)}

    def enqueue(scope: BrandScope, kind: str, params: dict) -> dict:
        try:
            with cloud_db.connect(settings.database_url) as conn:
                return {"job": cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind=kind,
                                                  params=params, created_by=scope.member.user_id)}
        except cloud_jobs.JobConflict as exc:
            label = {"crawl": "Une lecture", "match": "Une comparaison", "report": "Un rapport"}.get(kind, "Une tâche")
            raise HTTPException(status_code=409, detail=f"{label} est déjà en cours ou en attente.") from exc

    @app.post(f"{BRAND}/jobs/crawl")
    def start_crawl(body: CrawlBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        """Read the chosen addresses of the brand, all of them when none is chosen."""
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, scope.org_id)
            known = {row["id"] for row in cloud_db.list_sites(conn, scope.brand_id)}
        if not known:
            raise HTTPException(status_code=400, detail="Ajoutez d'abord une adresse à cette marque.")
        if any(site_id not in known for site_id in body.site_ids):
            raise HTTPException(status_code=404, detail="Adresse introuvable pour cette marque.")
        max_pages = min(body.max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
        return enqueue(scope, "crawl", {"site_ids": [str(site_id) for site_id in body.site_ids], "max_pages": max_pages,
                                        "fresh": body.fresh, "then_match": body.then_match})

    @app.post(f"{BRAND}/jobs/match")
    def start_match(scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        return enqueue(scope, "match", {})

    @app.post(f"{BRAND}/jobs/{{job_id}}/cancel")
    def cancel_job(job_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            job = cloud_jobs.request_cancel(conn, scope.brand_id, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Aucune tâche active avec cet identifiant.")
        return {"job": job}

    @app.get(f"{BRAND}/scans")
    def scans(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            runs = cloud_db.list_crawl_runs(conn, scope.brand_id, limit=30)
        return {
            "scans": [
                {
                    "id": str(run["id"]), "site_id": str(run["site_id"]), "site_url": run["site_url"],
                    "site_label": run["site_label"] or "", "status": run["status"],
                    "started_at": _iso(run["started_at"]), "finished_at": _iso(run["finished_at"]),
                    "pages_visited": run["pages_visited"], "images_found": run["images_found"],
                    "images_new": run["images_new"], "blocked_by_robots": run["blocked_by_robots"],
                    "errors": list(run["errors"] or [])[:8], "error_count": len(run["errors"] or []),
                }
                for run in runs
            ]
        }

    # --- matches and reviews ----------------------------------------------------------

    @app.get(f"{BRAND}/matches")
    def matches(within_days: Optional[int] = Query(None, ge=0, le=3650),
                scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, scope.org_id)
            rows = cloud_db.match_rows(conn, scope.brand_id)
            unmatched = cloud_db.unmatched_rows(conn, scope.brand_id)
        window = config.report.default_within_days if within_days is None else within_days
        ref_urls = sign(cloud_storage.BUCKET_REFS, [r["ref_thumb_path"] for r in rows + unmatched]
                        + [r["ref_storage_path"] for r in rows + unmatched]
                        + [r["ref_work_path"] for r in rows])
        site_urls = sign(cloud_storage.BUCKET_SITE_IMAGES, [r["site_thumb_path"] for r in rows]
                         + [r["site_storage_path"] for r in rows])

        def shaped(row: dict) -> dict:
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

        result = report_module.group_matches([shaped(row) for row in rows], window)
        for key in ("confirmed", "to_verify", "later"):
            for group in result[key]:
                for hit in group["hits"]:
                    hit["site_image_ids"] = [str(item) for item in hit["site_image_ids"]]
                    hit.pop("content_hash", None)
                    hit["pages"] = hit["pages"][:20]
        result["not_found"] = report_module.not_found_items(
            [
                {
                    "reference_id": str(row["reference_id"]), "filename": row["filename"],
                    "expiry_date": row["expiry_date"], "credit": row["credit"] or "", "notes": row["notes"] or "",
                    "compared": row["compared"],
                    "ref_thumb": ref_urls.get(row["ref_thumb_path"] or "") or ref_urls.get(row["ref_storage_path"], ""),
                }
                for row in unmatched
            ],
            window,
        )
        return result

    @app.post(f"{BRAND}/reviews")
    def review(body: ReviewBody, scope: BrandScope = Depends(brand_member_dep)) -> dict:
        if body.decision not in {"", *report_module.DECISIONS}:
            raise HTTPException(status_code=400, detail="Décision inconnue.")
        if not body.site_image_ids:
            raise HTTPException(status_code=400, detail="Aucune image à annoter.")
        with cloud_db.connect(settings.database_url) as conn:
            touched = cloud_db.set_reviews(
                conn, brand_id=scope.brand_id, reference_id=body.reference_id, site_image_ids=body.site_image_ids,
                decision=body.decision, reviewed_by=scope.member.user_id,
            )
        if body.decision and not touched:
            raise HTTPException(status_code=404, detail="Correspondance introuvable.")
        return {"ok": True, "updated": touched}

    # --- images of the site that match nothing in the library -------------------------------

    @app.get(f"{BRAND}/site-images")
    def site_images(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        """What was read on the brand's sites without any counterpart in its library: rights unknown."""
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, scope.org_id)
            groups = unreferenced_images(conn, scope.brand_id, config)
        leads = [group["lead"] for group in groups]
        urls = sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in leads] + [row["storage_path"] for row in leads])
        items = []
        for group in groups:
            lead, rows = group["lead"], group["rows"]
            pages = sorted({page for row in rows for page in row["pages"]})
            sites = {row["site_id"]: row["site_label"] or urlparse(row["site_url"]).netloc for row in rows}
            items.append({
                "id": str(lead["id"]),
                "ids": [str(row["id"]) for row in rows],
                "url": lead["url"],
                "urls": [row["url"] for row in rows][:10],
                "url_count": len(rows),
                "filename": filename_from_url(lead["url"], lead["storage_path"] or ""),
                "pages": pages[:20],
                "page_count": len(pages),
                "site_ids": [str(site_id) for site_id in sites],
                "sites": list(sites.values()),
                "width": lead["width"],
                "height": lead["height"],
                "compared": all(row["compared"] for row in rows),
                "first_seen": _iso(min(row["first_seen"] for row in rows)),
                "thumb": urls.get(lead["thumb_path"] or "") or urls.get(lead["storage_path"] or "", ""),
                "image": urls.get(lead["storage_path"] or "", ""),
            })
        return {"items": items}

    @app.post(f"{BRAND}/site-images/adopt")
    def adopt_site_images(body: AdoptBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        """Add images read on the site to the library. Each one is linked to its occurrences at once
        (a sure match: same image), so it goes straight to "À traiter"."""
        try:
            expiry = parse_expiry(body.expiry_date)
        except RefValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        org_id, brand_id = scope.org_id, scope.brand_id
        client = settings.storage_client()
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, org_id)
            rows = cloud_db.site_images_by_id(conn, brand_id, body.site_image_ids)
            taken = {row["filename"] for row in cloud_db.list_references(conn, brand_id)}
        found = {row["id"] for row in rows}
        added: list[dict] = []
        failed: list[dict] = [
            {"site_image_id": str(image_id), "url": "", "reason": "Image introuvable pour cette marque."}
            for image_id in body.site_image_ids if image_id not in found
        ]
        for row in rows:
            try:
                wanted = (body.filenames.get(str(row["id"])) or "").strip()
                name = safe_filename(wanted) if wanted else safe_filename(filename_from_url(row["url"], row["storage_path"] or ""))
                filename = unique_filename(name, taken)
                data = original_from_site(row["url"], config) or cloud_storage.download(
                    client, cloud_storage.BUCKET_SITE_IMAGES, row["storage_path"])
                img, refused = fetch.decode_reference(data, config.crawl.max_image_pixels)
                if img is None:
                    failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": refused})
                    continue
                features = reference_features(img)
                width, height = img.info.get("original_size", (features.width, features.height))
                storage_path = cloud_storage.ref_path(org_id, brand_id, filename)
                thumb_path = cloud_storage.ref_thumb_path(org_id, brand_id, filename)
                work_path = cloud_storage.ref_work_path(org_id, brand_id, filename)
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, storage_path, data)
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                                     content_type="image/jpeg")
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, work_path, fetch.make_working_copy(img),
                                     content_type="image/jpeg")
                with cloud_db.connect(settings.database_url) as conn:
                    ref_id = cloud_db.upsert_reference_image(
                        conn, org_id=org_id, brand_id=brand_id, filename=filename, storage_path=storage_path,
                        expiry_date=expiry, credit=body.credit.strip() or None, notes=body.notes.strip() or None,
                        phash=features.phash, dhash=features.dhash,
                        phash_flip=features.phash_flip, dhash_flip=features.dhash_flip,
                        embedding=None, width=width, height=height, thumb_path=thumb_path, work_path=work_path,
                    )
                    same = cloud_db.same_image_ids(conn, brand_id, row["content_hash"], row["id"])
                    cloud_db.write_matches(conn, org_id, [(ref_id, image_id, "phash", 1.0, "haut") for image_id in same])
                taken.add(filename)
                added.append({"site_image_id": str(row["id"]), "filename": filename})
            except HTTPException as exc:
                failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": str(exc.detail)})
            except Exception as exc:  # noqa: BLE001 - one image must not sink the others
                log.exception("adopting site image %s into brand %s failed", row["id"], brand_id)
                failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": _upload_failure(exc)})
        job = None
        if added:
            with cloud_db.connect(settings.database_url) as conn:
                job = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=brand_id, kind="index",
                                         created_by=scope.member.user_id)
        return {"added": added, "failed": failed, "job": job}

    # --- exclusions ----------------------------------------------------------------------

    @app.get(f"{BRAND}/exclusions")
    def list_exclusions(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_exclusions(conn, scope.brand_id)
        urls = sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in rows])
        return {
            "exclusions": [
                {"id": str(row["id"]), "reason": row["reason"] or "", "site_url": row["site_url"] or "",
                 "thumb_url": urls.get(row["thumb_path"] or "", ""), "created_at": _iso(row["created_at"])}
                for row in rows
            ]
        }

    @app.post(f"{BRAND}/exclusions")
    def add_exclusion(body: ExclusionBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        """Never match this image (or a near copy of it) again, for this brand.

        Matches it already produced disappear at once; the next comparison
        recomputes everything with the new exclusion list.
        """
        with cloud_db.connect(settings.database_url) as conn:
            group_id = cloud_db.add_exclusion(conn, scope.org_id, scope.brand_id, body.site_image_id,
                                              reason=body.reason.strip() or None, created_by=scope.member.user_id)
            if group_id is None:
                raise HTTPException(status_code=404, detail="Image introuvable.")
            config = org_config(conn, scope.org_id)
            excluded = match_module.excluded_site_ids(
                cloud_db.site_hashes(conn, scope.brand_id), cloud_db.load_exclusions(conn, scope.brand_id), config.match
            )
            removed = conn.execute(
                "DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)", (scope.org_id, list(excluded))
            ).rowcount
        return {"id": str(group_id), "matches_removed": removed}

    @app.delete(f"{BRAND}/exclusions/{{exclusion_id}}")
    def delete_exclusion(exclusion_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            if not cloud_db.delete_exclusion(conn, scope.brand_id, exclusion_id):
                raise HTTPException(status_code=404, detail="Exclusion introuvable.")
        # The match signature now differs: the next comparison brings the image back.
        try:
            with cloud_db.connect(settings.database_url) as conn:
                job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="match",
                                         created_by=scope.member.user_id)
        except cloud_jobs.JobConflict:
            job = None
        return {"ok": True, "job": job}

    # --- reports ------------------------------------------------------------------------

    @app.post(f"{BRAND}/reports")
    def create_report(body: ReportBody, scope: BrandScope = Depends(brand_admin_dep)) -> dict:
        return enqueue(scope, "report", {"within_days": body.within_days})

    @app.get(f"{BRAND}/reports")
    def list_reports(scope: BrandScope = Depends(brand_member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            reports = cloud_db.list_reports(conn, scope.brand_id)
        paths = [r[key] for r in reports for key in ("storage_path_html", "storage_path_csv", "storage_path_not_found_csv")]
        urls = sign(cloud_storage.BUCKET_REPORTS, paths)
        return {
            "reports": [
                {
                    "id": str(r["id"]), "within_days": r["within_days"], "generated_at": _iso(r["generated_at"]),
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

    # --- settings (per organization, shared by its brands) -------------------------------

    @app.get("/api/orgs/{org_id}/settings")
    def get_settings(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        base = settings.base_config()
        with cloud_db.connect(settings.database_url) as conn:
            overrides = cloud_db.get_overrides(conn, org_id)
        effective = with_overrides(base, overrides)
        return {
            "overrides": overrides,
            "defaults": {"crawl": base.crawl.__dict__, "match": base.match.__dict__, "report": base.report.__dict__},
            "effective": {"crawl": effective.crawl.__dict__, "match": effective.match.__dict__,
                          "report": effective.report.__dict__},
        }

    @app.put("/api/orgs/{org_id}/settings")
    def put_settings(org_id: uuid.UUID, body: SettingsBody, member=Depends(admin_dep)) -> dict:
        try:
            clean = validate_overrides(body.overrides)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clean = {section: values for section, values in clean.items() if values}
        with cloud_db.connect(settings.database_url) as conn:
            cloud_db.set_overrides(conn, org_id, clean, member.user_id)
        return {"overrides": clean}

    # --- interface ------------------------------------------------------------------------

    def index_page() -> Response:
        page = dist / "index.html"
        if page.is_file():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<p>Nyra. Depuis frontend/, lancez <code>npm install</code> puis <code>npm run build</code>.</p>")

    not_found_page = dist / "404.html"

    @app.get("/", response_model=None)
    def index() -> Response:
        return index_page()

    # Client-side routes (/o/remy-martin/bibliotheque, /connexion, ...) all
    # get the app shell; the router renders its own "page not found". A path
    # that looks like a file and isn't one gets a real 404.
    @app.get("/{full_path:path}", response_model=None)
    def spa(full_path: str) -> Response:
        if full_path.startswith(("api/", "assets/")):
            raise HTTPException(status_code=404, detail="Not Found")
        if "." in full_path.rsplit("/", 1)[-1]:
            static = (dist / full_path).resolve()
            if static.is_file() and dist.resolve() in static.parents:
                return FileResponse(static)
            if not_found_page.is_file():
                return HTMLResponse(not_found_page.read_text(encoding="utf-8"), status_code=404)
            return HTMLResponse("<p>404 : page introuvable.</p>", status_code=404)
        return index_page()

    return app
