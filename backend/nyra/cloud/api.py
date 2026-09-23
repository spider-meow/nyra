"""The web process: HTTP API + the built interface.

Every product route lives under `/api/orgs/{org_id}/...`, gated by
`cloud.auth` (a Supabase JWT, then a membership in that organization;
admin-only routes say so). This process never crawls or runs a model:
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import psycopg
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nyra import fetch, netguard
from nyra import match as match_module
from nyra import report as report_module
from nyra.config import Config, load_config, validate_overrides, with_overrides
from nyra.refs import RefValidationError, parse_expiry, reference_features

from . import auth as cloud_auth
from . import db as cloud_db
from . import jobs as cloud_jobs
from . import storage as cloud_storage

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
MAX_UPLOAD_BYTES = 30_000_000
MAX_FILES_PER_UPLOAD = 100
MAX_CSV_BYTES = 2_000_000


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


class CrawlBody(BaseModel):
    site: str = Field(max_length=2000)
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


class SettingsBody(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


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

    dist = frontend_dir() / "dist"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    auth_args = dict(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret, database_url=settings.database_url)
    member_dep = cloud_auth.require_member(**auth_args)
    admin_dep = cloud_auth.require_admin(**auth_args)
    user_dep = cloud_auth.require_user(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret)

    def org_config(conn, org_id: uuid.UUID) -> Config:
        return with_overrides(settings.base_config(), cloud_db.get_overrides(conn, org_id))

    def sign(bucket: str, paths) -> dict[str, str]:
        try:
            return cloud_storage.signed_urls(settings.storage_client(), bucket, paths)
        except Exception:  # noqa: BLE001 - missing thumbnails must not break a page
            return {}

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
        return {
            "organizations": [
                {"org_id": str(m["org_id"]), "name": m["org_name"], "slug": m["org_slug"], "role": m["role"]}
                for m in memberships
            ]
        }

    # --- overview ----------------------------------------------------------------

    @app.get("/api/orgs/{org_id}/overview")
    def overview(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            org = cloud_db.get_organization(conn, org_id)
            config = org_config(conn, org_id)
            stats = cloud_db.get_stats(conn, org_id)
            matches = cloud_db.match_rows(conn, org_id)
            unmatched = cloud_db.unmatched_rows(conn, org_id)
            jobs = cloud_jobs.current(conn, org_id)
            runs = cloud_db.list_crawl_runs(conn, org_id, limit=1)
            sites = cloud_db.list_sites(conn, org_id)
            pending_index = conn.execute(
                "SELECT COUNT(*) AS c FROM reference_images WHERE org_id = %s AND embedding IS NULL", (org_id,)
            ).fetchone()["c"]
        last_run = runs[0] if runs else None
        return {
            "organization": {"id": str(org_id), "name": org["name"], "slug": org["slug"]},
            "role": member.role,
            "stats": {**stats.__dict__, "references_pending_index": pending_index},
            "dashboard": report_module.dashboard(matches, unmatched),
            "jobs": jobs,
            "last_crawl": None if last_run is None else {
                "site_url": last_run["site_url"], "status": last_run["status"],
                "started_at": _iso(last_run["started_at"]), "finished_at": _iso(last_run["finished_at"]),
                "pages_visited": last_run["pages_visited"], "images_new": last_run["images_new"],
            },
            "sites": [site["url"] for site in sites],
            "defaults": {
                "max_pages": config.crawl.max_pages,
                "max_pages_limit": config.crawl.max_pages_limit,
                "within_days": config.report.default_within_days,
            },
        }

    # --- library --------------------------------------------------------------------

    @app.get("/api/orgs/{org_id}/library")
    def library(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_references(conn, org_id)
            indexing = bool(conn.execute(
                "SELECT 1 FROM jobs WHERE org_id = %s AND kind = 'index' AND status = ANY(%s) LIMIT 1",
                (org_id, list(cloud_jobs.ACTIVE)),
            ).fetchone())
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

    @app.post("/api/orgs/{org_id}/library/upload")
    def upload(org_id: uuid.UUID, files: list[UploadFile] = File(...), member=Depends(admin_dep)) -> dict:
        if not files:
            raise HTTPException(status_code=400, detail="Aucun fichier.")
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise HTTPException(status_code=400, detail=f"{MAX_FILES_PER_UPLOAD} fichiers au plus par envoi.")
        client = settings.storage_client()
        saved: list[str] = []
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
            img = fetch.decode(data, config.crawl.max_image_pixels)
            if img is None:
                failed.append({"filename": filename, "reason": "Image illisible ou trop grande."})
                continue
            features = reference_features(img)
            storage_path = cloud_storage.path_for(org_id, filename)
            thumb_path = cloud_storage.ref_thumb_path(org_id, filename)
            content_type = upload_file.content_type if (upload_file.content_type or "").startswith("image/") else None
            cloud_storage.upload(client, cloud_storage.BUCKET_REFS, storage_path, data, content_type=content_type)
            cloud_storage.upload(client, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                                 content_type="image/jpeg")
            with cloud_db.connect(settings.database_url) as conn:
                existing = cloud_db.get_reference_by_filename(conn, org_id, filename)
                cloud_db.upsert_reference_image(
                    conn, org_id=org_id, filename=filename, storage_path=storage_path,
                    expiry_date=_iso(existing["expiry_date"]) if existing else None,
                    credit=existing["credit"] if existing else None,
                    notes=existing["notes"] if existing else None,
                    phash=features.phash, dhash=features.dhash,
                    phash_flip=features.phash_flip, dhash_flip=features.dhash_flip,
                    embedding=None, width=features.width, height=features.height, thumb_path=thumb_path,
                )
            saved.append(filename)
        job = None
        if saved:
            with cloud_db.connect(settings.database_url) as conn:
                job = cloud_jobs.enqueue(conn, org_id=org_id, kind="index", created_by=member.user_id)
        return {"saved": saved, "failed": failed, "job": job}

    @app.put("/api/orgs/{org_id}/library/{filename}")
    def update_meta(org_id: uuid.UUID, filename: str, body: MetaBody, member=Depends(admin_dep)) -> dict:
        filename = safe_filename(filename)
        try:
            expiry = parse_expiry(body.expiry_date)
        except RefValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with cloud_db.connect(settings.database_url) as conn:
            found = cloud_db.update_reference_meta(
                conn, org_id, filename, expiry_date=expiry,
                credit=body.credit.strip() or None, notes=body.notes.strip() or None,
            )
        if not found:
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        return {"ok": True, "expiry_date": expiry or ""}

    def delete_filenames(org_id: uuid.UUID, filenames: list[str]) -> int:
        with cloud_db.connect(settings.database_url) as conn:
            deleted = cloud_db.delete_references(conn, org_id, filenames)
        paths = [row["storage_path"] for row in deleted] + [row["thumb_path"] for row in deleted]
        try:
            cloud_storage.delete(settings.storage_client(), cloud_storage.BUCKET_REFS, paths)
        except Exception:  # noqa: BLE001 - rows are gone; an orphan file is harmless
            pass
        return len(deleted)

    @app.delete("/api/orgs/{org_id}/library/{filename}")
    def delete_ref(org_id: uuid.UUID, filename: str, member=Depends(admin_dep)) -> dict:
        if not delete_filenames(org_id, [safe_filename(filename)]):
            raise HTTPException(status_code=404, detail="Référence introuvable.")
        return {"ok": True}

    @app.post("/api/orgs/{org_id}/library/delete")
    def delete_many(org_id: uuid.UUID, body: FilenamesBody, member=Depends(admin_dep)) -> dict:
        names = [safe_filename(name) for name in body.filenames]
        return {"deleted": delete_filenames(org_id, names)}

    @app.post("/api/orgs/{org_id}/library/expiry")
    def bulk_expiry(org_id: uuid.UUID, body: BulkExpiryBody, member=Depends(admin_dep)) -> dict:
        try:
            expiry = parse_expiry(body.expiry_date)
        except RefValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        names = [safe_filename(name) for name in body.filenames]
        with cloud_db.connect(settings.database_url) as conn:
            updated = cloud_db.set_expiry_for(conn, org_id, names, expiry)
        return {"updated": updated}

    @app.post("/api/orgs/{org_id}/library/import-csv")
    def import_csv(org_id: uuid.UUID, file: UploadFile = File(...), apply: bool = Query(False),
                   member=Depends(admin_dep)) -> dict:
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
            known = cloud_db.existing_filenames(conn, org_id, [entry["filename"] for entry in parsed])
            for entry in parsed:
                if entry["status"] == "ok" and entry["filename"] not in known:
                    entry.update(status="unknown_file", message="Aucune image de ce nom dans la bibliothèque.")
            applied = 0
            if apply:
                for entry in parsed:
                    if entry["status"] == "ok":
                        cloud_db.update_reference_meta(
                            conn, org_id, entry["filename"], expiry_date=entry["expiry_date"] or None,
                            credit=entry["credit"] or None, notes=entry["notes"] or None,
                        )
                        applied += 1
        return {"rows": parsed, "applied": applied}

    @app.get("/api/orgs/{org_id}/library/export-csv")
    def export_csv(org_id: uuid.UUID, member=Depends(member_dep)) -> Response:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_references(conn, org_id)
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

    @app.get("/api/orgs/{org_id}/jobs/current")
    def current_jobs(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            return cloud_jobs.current(conn, org_id)

    @app.get("/api/orgs/{org_id}/jobs")
    def recent_jobs(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            return {"jobs": cloud_jobs.recent(conn, org_id)}

    def enqueue(org_id: uuid.UUID, kind: str, params: dict, member) -> dict:
        try:
            with cloud_db.connect(settings.database_url) as conn:
                return {"job": cloud_jobs.enqueue(conn, org_id=org_id, kind=kind, params=params, created_by=member.user_id)}
        except cloud_jobs.JobConflict as exc:
            label = {"crawl": "Une lecture", "match": "Une comparaison", "report": "Un rapport"}.get(kind, "Une tâche")
            raise HTTPException(status_code=409, detail=f"{label} est déjà en cours ou en attente.") from exc

    @app.post("/api/orgs/{org_id}/jobs/crawl")
    def start_crawl(org_id: uuid.UUID, body: CrawlBody, member=Depends(admin_dep)) -> dict:
        site = body.site.strip()
        parsed = urlparse(site)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Indiquez une adresse qui commence par http:// ou https://.")
        try:
            netguard.check_url(site)
        except netguard.BlockedURL as exc:
            raise HTTPException(status_code=400, detail=f"Adresse refusée : {exc}.") from exc
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, org_id)
        max_pages = min(body.max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
        return enqueue(org_id, "crawl", {"site": site, "max_pages": max_pages, "fresh": body.fresh,
                                         "then_match": body.then_match}, member)

    @app.post("/api/orgs/{org_id}/jobs/match")
    def start_match(org_id: uuid.UUID, member=Depends(admin_dep)) -> dict:
        return enqueue(org_id, "match", {}, member)

    @app.post("/api/orgs/{org_id}/jobs/{job_id}/cancel")
    def cancel_job(org_id: uuid.UUID, job_id: uuid.UUID, member=Depends(admin_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            job = cloud_jobs.request_cancel(conn, org_id, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Aucune tâche active avec cet identifiant.")
        return {"job": job}

    @app.get("/api/orgs/{org_id}/scans")
    def scans(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            runs = cloud_db.list_crawl_runs(conn, org_id, limit=30)
        return {
            "scans": [
                {
                    "id": str(run["id"]), "site_url": run["site_url"], "status": run["status"],
                    "started_at": _iso(run["started_at"]), "finished_at": _iso(run["finished_at"]),
                    "pages_visited": run["pages_visited"], "images_found": run["images_found"],
                    "images_new": run["images_new"], "blocked_by_robots": run["blocked_by_robots"],
                    "errors": list(run["errors"] or [])[:8], "error_count": len(run["errors"] or []),
                }
                for run in runs
            ]
        }

    # --- matches and reviews ----------------------------------------------------------

    @app.get("/api/orgs/{org_id}/matches")
    def matches(org_id: uuid.UUID, within_days: Optional[int] = Query(None, ge=0, le=3650),
                member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            config = org_config(conn, org_id)
            rows = cloud_db.match_rows(conn, org_id)
            unmatched = cloud_db.unmatched_rows(conn, org_id)
        window = config.report.default_within_days if within_days is None else within_days
        ref_urls = sign(cloud_storage.BUCKET_REFS, [r["ref_thumb_path"] for r in rows + unmatched]
                        + [r["ref_storage_path"] for r in rows + unmatched])
        site_urls = sign(cloud_storage.BUCKET_SITE_IMAGES, [r["site_thumb_path"] for r in rows]
                         + [r["site_storage_path"] for r in rows])

        def shaped(row: dict) -> dict:
            return {
                "reference_id": str(row["reference_id"]), "filename": row["filename"],
                "expiry_date": row["expiry_date"], "credit": row["credit"] or "", "notes": row["notes"] or "",
                "site_image_id": str(row["site_image_id"]), "site_url": row["site_url"],
                "content_hash": row["content_hash"], "level": row["level"], "score": float(row["score"]),
                "confidence": row["confidence"], "decision": row["decision"], "pages": row["pages"],
                "ref_image": ref_urls.get(row["ref_storage_path"], ""),
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

    @app.post("/api/orgs/{org_id}/reviews")
    def review(org_id: uuid.UUID, body: ReviewBody, member=Depends(member_dep)) -> dict:
        if body.decision not in {"", *report_module.DECISIONS}:
            raise HTTPException(status_code=400, detail="Décision inconnue.")
        if not body.site_image_ids:
            raise HTTPException(status_code=400, detail="Aucune image à annoter.")
        with cloud_db.connect(settings.database_url) as conn:
            touched = cloud_db.set_reviews(
                conn, org_id=org_id, reference_id=body.reference_id, site_image_ids=body.site_image_ids,
                decision=body.decision, reviewed_by=member.user_id,
            )
        if body.decision and not touched:
            raise HTTPException(status_code=404, detail="Correspondance introuvable.")
        return {"ok": True, "updated": touched}

    # --- exclusions ----------------------------------------------------------------------

    @app.get("/api/orgs/{org_id}/exclusions")
    def list_exclusions(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            rows = cloud_db.list_exclusions(conn, org_id)
        urls = sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in rows])
        return {
            "exclusions": [
                {"id": str(row["id"]), "reason": row["reason"] or "", "site_url": row["site_url"] or "",
                 "thumb_url": urls.get(row["thumb_path"] or "", ""), "created_at": _iso(row["created_at"])}
                for row in rows
            ]
        }

    @app.post("/api/orgs/{org_id}/exclusions")
    def add_exclusion(org_id: uuid.UUID, body: ExclusionBody, member=Depends(admin_dep)) -> dict:
        """Never match this image (or a near copy of it) again.

        Matches it already produced disappear at once; the next comparison
        recomputes everything with the new exclusion list.
        """
        with cloud_db.connect(settings.database_url) as conn:
            group_id = cloud_db.add_exclusion(conn, org_id, body.site_image_id, reason=body.reason.strip() or None,
                                              created_by=member.user_id)
            if group_id is None:
                raise HTTPException(status_code=404, detail="Image introuvable.")
            config = org_config(conn, org_id)
            excluded = match_module.excluded_site_ids(
                cloud_db.site_hashes(conn, org_id), cloud_db.load_exclusions(conn, org_id), config.match
            )
            removed = conn.execute(
                "DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)", (org_id, list(excluded))
            ).rowcount
        return {"id": str(group_id), "matches_removed": removed}

    @app.delete("/api/orgs/{org_id}/exclusions/{exclusion_id}")
    def delete_exclusion(org_id: uuid.UUID, exclusion_id: uuid.UUID, member=Depends(admin_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            if not cloud_db.delete_exclusion(conn, org_id, exclusion_id):
                raise HTTPException(status_code=404, detail="Exclusion introuvable.")
        # The match signature now differs: the next comparison brings the image back.
        try:
            with cloud_db.connect(settings.database_url) as conn:
                job = cloud_jobs.enqueue(conn, org_id=org_id, kind="match", created_by=member.user_id)
        except cloud_jobs.JobConflict:
            job = None
        return {"ok": True, "job": job}

    # --- reports ------------------------------------------------------------------------

    @app.post("/api/orgs/{org_id}/reports")
    def create_report(org_id: uuid.UUID, body: ReportBody, member=Depends(admin_dep)) -> dict:
        return enqueue(org_id, "report", {"within_days": body.within_days}, member)

    @app.get("/api/orgs/{org_id}/reports")
    def list_reports(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            reports = cloud_db.list_reports(conn, org_id)
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

    # --- settings -----------------------------------------------------------------------

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
            return HTMLResponse("<p>404 — page introuvable.</p>", status_code=404)
        return index_page()

    return app
