"""FastAPI app for the multi-tenant, Supabase-backed product.

Same product surface as `nyra.api` (library, crawl/match jobs,
matches + review, reports) but every route is scoped under
`/api/orgs/{org_id}/...`, gated by `cloud.auth`, and backed by
`cloud.db`/`cloud.storage` instead of SQLite/local disk. Reuses
`nyra.api`'s `JobRunner` (a plain in-process job tracker with no
SQLite coupling) and its match-grouping helpers (`_group_matches`,
`_not_found_groups`) rather than re-deriving that logic — one `JobRunner`
per organization, so two orgs can run jobs at the same time (the local
product only ever has one workspace, so one global runner was enough
there; here it wouldn't be).

Library uploads hash and store an image synchronously (seconds, not the
minutes a crawl takes) rather than going through a background job like
the local product's two-step "upload, then commit via `ingest`" — a
result of the CSV-plus-folder input the local product still reads from
not applying here; a web upload already carries one file at a time.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from nyra.api import JobRunner, _group_matches, _not_found_groups, frontend_dir, safe_filename
from nyra.config import Config, load_config
from nyra.match import compute_clip_embedding, compute_hashes
from nyra.report import days_until, urgency_status

from . import auth as cloud_auth
from . import db as cloud_db
from . import pipeline as cloud_pipeline
from . import storage as cloud_storage


class CloudSettings:
    def __init__(
        self,
        *,
        database_url: str,
        supabase_url: str,
        service_role_key: str,
        jwt_secret: Optional[str],
        anon_key: str = "",
    ):
        self.database_url = database_url
        self.supabase_url = supabase_url
        self.service_role_key = service_role_key
        self.jwt_secret = jwt_secret
        self.anon_key = anon_key

    def config(self) -> Config:
        return load_config()

    def storage_client(self):
        return cloud_storage.get_client(self.supabase_url, self.service_role_key)


class MetaBody(BaseModel):
    expiry_date: str = ""
    credit: str = ""
    notes: str = ""


class CrawlBody(BaseModel):
    site: str
    max_pages: Optional[int] = Field(default=None, ge=1, le=5000)
    fast: bool = False
    fresh: bool = False
    then_match: bool = True


class MatchBody(BaseModel):
    fast: bool = False


class ReportBody(BaseModel):
    within_days: Optional[int] = Field(default=None, ge=0, le=3650)


class SignupBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    organization_name: str = Field(min_length=1, max_length=80)
    slug: str = ""


class ReviewBody(BaseModel):
    reference_id: uuid.UUID
    site_image_ids: list[uuid.UUID] = Field(default_factory=list)
    decision: str = ""


def create_app(settings: CloudSettings) -> FastAPI:
    app = FastAPI(title="Nyra Cloud", docs_url=None, redoc_url=None)

    @app.exception_handler(psycopg.OperationalError)
    async def postgres_unavailable(request, exc: psycopg.OperationalError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Impossible de joindre Postgres. Vérifie l'hôte dans DATABASE_URL : c'est celui du mode Session, port 5432, copié depuis Connect."},
        )
    dist = frontend_dir() / "dist"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    jobs: dict[uuid.UUID, JobRunner] = {}

    def jobs_for(org_id: uuid.UUID) -> JobRunner:
        if org_id not in jobs:
            jobs[org_id] = JobRunner()
        return jobs[org_id]

    member_dep = cloud_auth.require_member(
        supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret, database_url=settings.database_url
    )
    admin_dep = cloud_auth.require_admin(
        supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret, database_url=settings.database_url
    )

    @app.get("/", response_model=None)
    def index() -> FileResponse | HTMLResponse:
        page = dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse(
            "<p>Nyra. Depuis frontend/, lance <code>npm install</code> puis <code>npm run build</code>.</p>",
            status_code=200,
        )

    @app.get("/api/healthz")
    def healthz() -> dict:
        cloud_db.ping(settings.database_url)
        return {"status": "ok"}

    @app.get("/api/auth/config")
    def auth_config() -> dict:
        return {
            "required": True,
            "supabaseUrl": settings.supabase_url,
            "anonKey": settings.anon_key,
        }

    @app.post("/api/signup")
    def signup(body: SignupBody) -> dict:
        email = body.email.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise HTTPException(status_code=400, detail="Email invalide.")
        try:
            slug = cloud_db.slugify(body.slug or body.organization_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        client = settings.storage_client()
        try:
            created = client.auth.admin.create_user(
                {"email": email, "password": body.password, "email_confirm": True}
            )
        except Exception as exc:
            message = str(exc).lower()
            if "already" in message or "registered" in message or "exists" in message:
                raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email.") from exc
            raise HTTPException(status_code=400, detail="La création du compte a échoué.") from exc

        user = getattr(created, "user", None)
        user_id = getattr(user, "id", None)
        if user_id is None:
            raise HTTPException(status_code=502, detail="Le compte n'a pas été créé.")
        user_uuid = uuid.UUID(str(user_id))

        try:
            with cloud_db.connect(settings.database_url) as conn:
                org_id = cloud_db.create_organization(
                    conn, name=body.organization_name.strip(), slug=slug
                )
                cloud_db.add_membership(conn, user_id=user_uuid, org_id=org_id, role="admin")
        except psycopg.errors.UniqueViolation as exc:
            try:
                client.auth.admin.delete_user(str(user_uuid))
            except Exception:
                pass
            raise HTTPException(status_code=409, detail="Cette adresse d'organisation est déjà utilisée.") from exc

        return {"slug": slug, "org_id": str(org_id)}

    @app.get("/api/orgs")
    def list_orgs(token: str = Depends(cloud_auth._bearer_token)) -> dict:
        try:
            claims = cloud_auth.verify_jwt(
                token, supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret
            )
        except cloud_auth.AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        with cloud_db.connect(settings.database_url) as conn:
            memberships = cloud_db.list_memberships_for_user(conn, claims.user_id)
        return {"organizations": [{"org_id": str(m["org_id"]), "name": m["org_name"], "role": m["role"]} for m in memberships]}

    @app.get("/api/orgs/{org_id}/overview")
    def overview(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        config = settings.config()
        client = settings.storage_client()
        with cloud_db.connect(settings.database_url) as conn:
            stats = cloud_db.get_stats(conn, org_id)
            refs = cloud_db.get_reference_images(conn, org_id)

        def media_url(storage_path: str | None) -> str:
            if not storage_path:
                return ""
            try:
                return cloud_storage.signed_url(client, cloud_storage.BUCKET_REFS, storage_path)
            except Exception:
                return ""

        library = [
            {
                "filename": r["filename"],
                "expiry_date": r["expiry_date"].isoformat() if r["expiry_date"] else "",
                "credit": r["credit"] or "",
                "notes": r["notes"] or "",
                "indexed": r["phash"] is not None,
                "width": r["width"],
                "height": r["height"],
                "url": media_url(r["storage_path"]),
            }
            for r in refs
        ]
        return {
            "stats": {
                "reference_images": stats.reference_images,
                "pages_crawled": stats.pages_crawled,
                "pages_pending": 0,
                "site_images": stats.site_images,
                "matches": stats.matches,
            },
            "library": library,
            "job": jobs_for(org_id).snapshot(),
            "role": member.role,
            "defaults": {"max_pages": config.crawl.max_pages, "within_days": config.report.default_within_days},
        }

    @app.get("/api/orgs/{org_id}/job")
    def job_status(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        return {"job": jobs_for(org_id).snapshot()}

    @app.post("/api/orgs/{org_id}/jobs/cancel")
    def cancel_job(org_id: uuid.UUID, member=Depends(admin_dep)) -> dict:
        jobs_for(org_id).request_cancel()
        return {"job": jobs_for(org_id).snapshot()}

    @app.post("/api/orgs/{org_id}/library/upload")
    async def upload(
        org_id: uuid.UUID, files: list[UploadFile] = File(...), fast: bool = Form(False), member=Depends(admin_dep)
    ) -> dict:
        if not files:
            raise HTTPException(status_code=400, detail="Aucun fichier.")
        config = settings.config()
        client = settings.storage_client()
        saved: list[str] = []
        failed: list[dict] = []

        with cloud_db.connect(settings.database_url) as conn:
            for upload_file in files:
                raw_name = upload_file.filename or ""
                try:
                    filename = safe_filename(raw_name)
                except HTTPException as exc:
                    failed.append({"filename": raw_name or "(sans nom)", "reason": str(exc.detail)})
                    continue

                data = await upload_file.read()
                if not data:
                    failed.append({"filename": filename, "reason": "Fichier vide."})
                    continue

                try:
                    from io import BytesIO

                    img = Image.open(BytesIO(data))
                    img.load()
                except (UnidentifiedImageError, OSError):
                    failed.append({"filename": filename, "reason": "Image illisible ou corrompue."})
                    continue

                rgb = img.convert("RGB")
                phash, dhash = compute_hashes(rgb)
                embedding = compute_clip_embedding(rgb, config.match) if not fast else None

                storage_path = cloud_storage.path_for(org_id, filename)
                cloud_storage.upload(client, cloud_storage.BUCKET_REFS, storage_path, data)

                existing = None
                for r in cloud_db.get_reference_images(conn, org_id):
                    if r["filename"] == filename:
                        existing = r
                        break

                cloud_db.upsert_reference_image(
                    conn,
                    org_id=org_id,
                    filename=filename,
                    storage_path=storage_path,
                    expiry_date=existing["expiry_date"].isoformat() if existing and existing["expiry_date"] else None,
                    credit=existing["credit"] if existing else None,
                    notes=existing["notes"] if existing else None,
                    phash=phash,
                    dhash=dhash,
                    embedding=embedding,
                    width=img.size[0],
                    height=img.size[1],
                )
                saved.append(filename)

        return {"saved": saved, "failed": failed}

    @app.put("/api/orgs/{org_id}/library/{filename}")
    def update_meta(org_id: uuid.UUID, filename: str, body: MetaBody, member=Depends(admin_dep)) -> dict:
        filename = safe_filename(filename)
        with cloud_db.connect(settings.database_url) as conn:
            existing = next((r for r in cloud_db.get_reference_images(conn, org_id) if r["filename"] == filename), None)
            if existing is None:
                raise HTTPException(status_code=404, detail="Référence introuvable.")
            cloud_db.upsert_reference_image(
                conn,
                org_id=org_id,
                filename=filename,
                storage_path=existing["storage_path"],
                expiry_date=body.expiry_date or None,
                credit=body.credit or None,
                notes=body.notes or None,
                phash=existing["phash"],
                dhash=existing["dhash"],
                embedding=existing["embedding"],
                width=existing["width"],
                height=existing["height"],
            )
        return {"ok": True}

    @app.delete("/api/orgs/{org_id}/library/{filename}")
    def delete_ref(org_id: uuid.UUID, filename: str, member=Depends(admin_dep)) -> dict:
        filename = safe_filename(filename)
        client = settings.storage_client()
        with cloud_db.connect(settings.database_url) as conn:
            existing = next((r for r in cloud_db.get_reference_images(conn, org_id) if r["filename"] == filename), None)
            if existing is None:
                raise HTTPException(status_code=404, detail="Référence introuvable.")
            cloud_db.delete_reference_image(conn, org_id=org_id, filename=filename)
        cloud_storage.delete(client, cloud_storage.BUCKET_REFS, [existing["storage_path"]])
        return {"ok": True}

    @app.post("/api/orgs/{org_id}/jobs/crawl")
    def start_crawl(org_id: uuid.UUID, body: CrawlBody, member=Depends(admin_dep)) -> dict:
        from urllib.parse import urlparse as _urlparse

        parsed = _urlparse(body.site.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Indique une URL qui commence par http:// ou https://.")

        runner = jobs_for(org_id)
        client = settings.storage_client()
        config = settings.config()

        def work() -> dict:
            def progress(stats) -> None:
                known = max(0, stats.images_stored - stats.images_new)
                runner.update(
                    message=f"Page {stats.pages_visited} · {stats.images_new} nouvelles, {known} déjà connues",
                    progress={
                        "done": stats.pages_visited, "total": body.max_pages or config.crawl.max_pages,
                        "images_new": stats.images_new, "blocked_by_robots": stats.blocked_by_robots,
                        "errors": len(stats.errors),
                    },
                )

            run_id, stats = cloud_pipeline.crawl_site(
                body.site, org_id, settings.database_url, client, config,
                max_pages=body.max_pages, compute_embeddings=not body.fast, resume=not body.fresh,
                progress=progress, should_stop=runner.cancelled,
            )
            result = {
                "run_id": str(run_id), "pages_visited": stats.pages_visited,
                "images_stored": stats.images_stored, "images_new": stats.images_new,
                "blocked_by_robots": stats.blocked_by_robots, "errors": stats.errors[:8],
            }
            if body.then_match and not runner.cancelled():
                def on_match(done: int, total: int) -> None:
                    runner.update(message=f"Comparaison {done}/{total}", progress={"done": done, "total": total})

                result["matches"] = cloud_pipeline.run_matching(
                    org_id, settings.database_url, config, use_clip=not body.fast,
                    progress=on_match, should_stop=runner.cancelled,
                )
            return result

        return {"job": runner.start("crawl", work)}

    @app.post("/api/orgs/{org_id}/jobs/match")
    def start_match(org_id: uuid.UUID, body: MatchBody, member=Depends(admin_dep)) -> dict:
        runner = jobs_for(org_id)
        config = settings.config()

        def work() -> dict:
            def progress(done: int, total: int) -> None:
                runner.update(message=f"Comparaison {done}/{total}", progress={"done": done, "total": total})

            count = cloud_pipeline.run_matching(
                org_id, settings.database_url, config, use_clip=not body.fast,
                progress=progress, should_stop=runner.cancelled,
            )
            return {"matches": count}

        return {"job": runner.start("match", work)}

    @app.get("/api/orgs/{org_id}/matches")
    def matches(org_id: uuid.UUID, within_days: Optional[int] = None, member=Depends(member_dep)) -> dict:
        config = settings.config()
        window = config.report.default_within_days if within_days is None else within_days
        if window < 0 or window > 3650:
            raise HTTPException(status_code=400, detail="Fenêtre d'échéance invalide.")

        client = settings.storage_client()
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()
        rows = []
        not_found_rows = []
        with cloud_db.connect(settings.database_url) as conn:
            for m in cloud_db.get_matches(conn, org_id):
                left = days_until(m["expiry_date"].isoformat() if m["expiry_date"] else None, today)
                pages = cloud_db.get_pages_for_image(conn, m["site_image_id"])
                rows.append({
                    "reference_id": m["reference_id"], "filename": m["filename"],
                    "expiry_date": m["expiry_date"].isoformat() if m["expiry_date"] else None,
                    "days_left": left, "status": urgency_status(left),
                    "credit": m["credit"] or "", "notes": m["notes"] or "", "pages": pages,
                    "site_url": m["site_url"], "level": m["level"], "score": m["score"],
                    "confidence": m["confidence"], "decision": m["decision"], "content_hash": m["content_hash"],
                    "site_image_id": m["site_image_id"],
                    "ref_image": cloud_storage.signed_url(client, cloud_storage.BUCKET_REFS, m["ref_storage_path"]) if m["ref_storage_path"] else None,
                    "site_image": cloud_storage.signed_url(client, cloud_storage.BUCKET_SITE_IMAGES, m["site_storage_path"]) if m["site_storage_path"] else None,
                })
            for r in cloud_db.get_unmatched_references(conn, org_id):
                left = days_until(r["expiry_date"].isoformat() if r["expiry_date"] else None, today)
                not_found_rows.append({
                    "reference_id": r["reference_id"], "filename": r["filename"],
                    "expiry_date": r["expiry_date"].isoformat() if r["expiry_date"] else None,
                    "days_left": left, "status": urgency_status(left),
                    "credit": r["credit"] or "", "notes": r["notes"] or "",
                    "compared": r["compared_at"] is not None,
                    "ref_image": cloud_storage.signed_url(client, cloud_storage.BUCKET_REFS, r["ref_storage_path"]) if r["ref_storage_path"] else None,
                })

        result = _group_matches(rows, window)
        result["not_found"] = _not_found_groups(not_found_rows, window)
        return result

    @app.post("/api/orgs/{org_id}/reviews")
    def review(org_id: uuid.UUID, body: ReviewBody, member=Depends(member_dep)) -> dict:
        if body.decision not in {"", "retenu", "ecarte", "traite"}:
            raise HTTPException(status_code=400, detail="Décision inconnue.")
        if not body.site_image_ids:
            raise HTTPException(status_code=400, detail="Aucune image à annoter.")
        with cloud_db.connect(settings.database_url) as conn:
            cloud_db.set_reviews(
                conn, org_id=org_id, reference_id=body.reference_id, site_image_ids=body.site_image_ids,
                decision=body.decision, reviewed_by=member.user_id,
            )
        return {"ok": True}

    @app.post("/api/orgs/{org_id}/reports")
    def create_report(org_id: uuid.UUID, body: ReportBody, member=Depends(admin_dep)) -> dict:
        config = settings.config()
        client = settings.storage_client()
        report_id = cloud_pipeline.generate_report(
            org_id, settings.database_url, client, config,
            within_days=body.within_days, generated_by=member.user_id,
        )
        with cloud_db.connect(settings.database_url) as conn:
            report = cloud_db.get_report(conn, org_id, report_id)
        if report is None:
            raise HTTPException(status_code=500, detail="Rapport introuvable après génération.")
        files = {
            "report.html": cloud_storage.signed_url(client, cloud_storage.BUCKET_REPORTS, report["storage_path_html"]),
            "matches.csv": cloud_storage.signed_url(client, cloud_storage.BUCKET_REPORTS, report["storage_path_csv"]),
            "not_found.csv": cloud_storage.signed_url(
                client, cloud_storage.BUCKET_REPORTS, report["storage_path_not_found_csv"]
            ),
        }
        return {"report_id": str(report_id), "files": files}

    @app.get("/api/orgs/{org_id}/reports")
    def list_reports(org_id: uuid.UUID, member=Depends(member_dep)) -> dict:
        with cloud_db.connect(settings.database_url) as conn:
            reports = cloud_db.list_reports(conn, org_id)
        return {
            "reports": [
                {"id": str(r["id"]), "within_days": r["within_days"], "generated_at": r["generated_at"].isoformat(), "stats": r["stats"]}
                for r in reports
            ]
        }

    @app.get("/api/orgs/{org_id}/reports/{report_id}/{name}")
    def download_report_file(org_id: uuid.UUID, report_id: uuid.UUID, name: str, member=Depends(member_dep)) -> RedirectResponse:
        if name not in {"report.html", "matches.csv", "not_found.csv"}:
            raise HTTPException(status_code=404, detail="Fichier inconnu.")
        with cloud_db.connect(settings.database_url) as conn:
            report = cloud_db.get_report(conn, org_id, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Rapport introuvable.")
        path_field = {
            "report.html": "storage_path_html", "matches.csv": "storage_path_csv", "not_found.csv": "storage_path_not_found_csv",
        }[name]
        client = settings.storage_client()
        url = cloud_storage.signed_url(client, cloud_storage.BUCKET_REPORTS, report[path_field])
        return RedirectResponse(url)

    @app.get("/api/orgs/{org_id}/media/ref/{filename}")
    def ref_media(org_id: uuid.UUID, filename: str, member=Depends(member_dep)) -> RedirectResponse:
        filename = safe_filename(filename)
        client = settings.storage_client()
        storage_path = cloud_storage.path_for(org_id, filename)
        if not cloud_storage.exists(client, cloud_storage.BUCKET_REFS, storage_path):
            raise HTTPException(status_code=404, detail="Image introuvable.")
        return RedirectResponse(cloud_storage.signed_url(client, cloud_storage.BUCKET_REFS, storage_path))

    @app.get("/signup", response_model=None)
    def signup_page() -> FileResponse | HTMLResponse:
        page = dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse(
            "<p>Nyra. Depuis frontend/, lance <code>npm install</code> puis <code>npm run build</code>.</p>",
            status_code=200,
        )

    not_found_page = dist / "404.html"

    @app.get("/{full_path:path}", response_model=None)
    def not_found(full_path: str) -> HTMLResponse:
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if not_found_page.is_file():
            return HTMLResponse(not_found_page.read_text(encoding="utf-8"), status_code=404)
        return HTMLResponse("<p>404 — page introuvable.</p>", status_code=404)

    return app
