"""Local interface for RightsWatch. No accounts — it drives the existing pipeline."""

from __future__ import annotations

import csv
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rightswatch import db as db_module
from rightswatch.config import load_config
from rightswatch.report import days_until, urgency_status

STATIC_DIR = Path(__file__).resolve().parent / "static"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


class Workspace:
    def __init__(self, root: Path, db_path: Path, config_path: Optional[Path]):
        self.root = root
        self.db_path = db_path
        self.config_path = config_path
        self.library_dir = root / "data" / "library"
        self.images_dir = self.library_dir / "images"
        self.meta_path = self.library_dir / "meta.json"
        self.csv_path = self.library_dir / "refs.csv"
        self.cache_dir = root / "data" / "site_images"
        self.out_dir = root / "out"

    def ensure(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db_module.init_db(self.db_path)
        raw = sqlite3.connect(str(self.db_path))
        try:
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA busy_timeout=5000")
        finally:
            raw.close()

    def config(self):
        return load_config(self.config_path)

    def read_meta(self) -> dict:
        if not self.meta_path.exists():
            return {}
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def write_meta(self, meta: dict) -> None:
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_csv(self) -> int:
        meta = self.read_meta()
        files = sorted(p for p in self.images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["filename", "expiry_date", "credit", "notes"])
            writer.writeheader()
            for path in files:
                row = meta.get(path.name) or {}
                writer.writerow({
                    "filename": path.name,
                    "expiry_date": (row.get("expiry_date") or "").strip(),
                    "credit": (row.get("credit") or "").strip(),
                    "notes": (row.get("notes") or "").strip(),
                })
        return len(files)


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Optional[dict] = None

    def snapshot(self) -> Optional[dict]:
        with self._lock:
            if self.current is None:
                return None
            return json.loads(json.dumps(self.current))

    def update(self, **fields) -> None:
        with self._lock:
            if self.current is None:
                return
            progress = fields.pop("progress", None)
            self.current.update(fields)
            if progress:
                self.current["progress"].update(progress)

    def start(self, kind: str, work) -> dict:
        with self._lock:
            if self.current and self.current["status"] == "running":
                raise HTTPException(status_code=409, detail="Une tâche est déjà en cours.")
            job = {
                "id": uuid.uuid4().hex[:12],
                "kind": kind,
                "status": "running",
                "message": "Démarrage…",
                "progress": {},
                "error": None,
                "result": None,
            }
            self.current = job
            job_id = job["id"]

        def runner() -> None:
            try:
                result = work()
                self.update(status="done", message="Terminé.", result=result or {})
            except Exception as exc:
                self.update(status="error", message="La tâche s'est arrêtée.", error=str(exc))

        threading.Thread(target=runner, name=f"rightswatch-{job_id}", daemon=True).start()
        return self.snapshot() or job


def safe_filename(name: str) -> str:
    base = Path(name).name.strip()
    if not base or base in {".", ".."} or "/" in base or "\\" in base or "\x00" in base:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    if Path(base).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Format non pris en charge : {base}")
    return base


class MetaBody(BaseModel):
    expiry_date: str = ""
    credit: str = ""
    notes: str = ""


class IngestBody(BaseModel):
    fast: bool = False


class CrawlBody(BaseModel):
    site: str
    max_pages: Optional[int] = Field(default=None, ge=1, le=5000)
    fast: bool = False
    fresh: bool = False


class MatchBody(BaseModel):
    fast: bool = False


class ReportBody(BaseModel):
    within_days: Optional[int] = Field(default=None, ge=0, le=3650)


def create_app(
    *,
    root: Path | None = None,
    db_path: Path | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    root = (root or Path.cwd()).resolve()
    db_path = (db_path or (root / "rightswatch.db")).resolve()
    workspace = Workspace(root, db_path, config_path)
    workspace.ensure()
    jobs = JobRunner()

    app = FastAPI(title="RightsWatch", docs_url=None, redoc_url=None)
    app.state.workspace = workspace
    app.state.jobs = jobs

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<p>Interface introuvable.</p>", status_code=500)
        return FileResponse(page)

    @app.get("/api/overview")
    def overview() -> dict:
        config = workspace.config()
        stats = {"reference_images": 0, "pages_crawled": 0, "pages_pending": 0, "site_images": 0, "matches": 0}
        indexed: dict[str, dict] = {}
        with db_module.connect(workspace.db_path) as conn:
            counts = db_module.get_stats(conn)
            stats = {
                "reference_images": counts.reference_images,
                "pages_crawled": counts.pages_crawled,
                "pages_pending": conn.execute("SELECT COUNT(*) AS c FROM pages WHERE status != 'done'").fetchone()["c"],
                "site_images": counts.site_images,
                "matches": counts.matches,
            }
            for row in db_module.get_reference_images(conn):
                indexed[row["filename"]] = {
                    "expiry_date": row["expiry_date"] or "",
                    "credit": row["credit"] or "",
                    "notes": row["notes"] or "",
                    "width": row["width"],
                    "height": row["height"],
                }

        meta = workspace.read_meta()
        library = []
        seen: set[str] = set()

        def add_item(filename: str, stored: dict) -> None:
            if filename in seen:
                return
            seen.add(filename)
            extra = meta.get(filename) or {}
            library.append({
                "filename": filename,
                "expiry_date": extra.get("expiry_date") or stored.get("expiry_date") or "",
                "credit": extra.get("credit") if filename in meta else stored.get("credit", ""),
                "notes": extra.get("notes") if filename in meta else stored.get("notes", ""),
                "indexed": filename in indexed,
                "width": stored.get("width"),
                "height": stored.get("height"),
                "url": "/api/media/ref/" + quote(filename),
            })

        if workspace.images_dir.exists():
            for path in sorted(workspace.images_dir.iterdir(), key=lambda item: item.name.lower()):
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                    add_item(path.name, indexed.get(path.name) or {})
        for filename in sorted(indexed, key=str.lower):
            add_item(filename, indexed[filename])
        library.sort(key=lambda item: item["filename"].lower())

        return {
            "stats": stats,
            "library": library,
            "job": jobs.snapshot(),
            "defaults": {
                "max_pages": config.crawl.max_pages,
                "within_days": config.report.default_within_days,
            },
        }

    @app.get("/api/job")
    def job() -> dict:
        return {"job": jobs.snapshot()}

    @app.post("/api/library/upload")
    async def upload(files: list[UploadFile] = File(...)) -> dict:
        if not files:
            raise HTTPException(status_code=400, detail="Aucun fichier.")
        saved = []
        for upload_file in files:
            filename = safe_filename(upload_file.filename or "")
            dest = workspace.images_dir / filename
            data = await upload_file.read()
            if not data:
                raise HTTPException(status_code=400, detail=f"Fichier vide : {filename}")
            dest.write_bytes(data)
            try:
                from PIL import Image

                with Image.open(dest) as img:
                    img.verify()
            except Exception as exc:
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail=f"{filename} n'est pas une image lisible.") from exc
            meta = workspace.read_meta()
            meta.setdefault(filename, {"expiry_date": "", "credit": "", "notes": ""})
            workspace.write_meta(meta)
            saved.append(filename)
        return {"saved": saved}

    def ref_path(filename: str) -> Optional[Path]:
        local = workspace.images_dir / filename
        if local.is_file():
            return local
        with db_module.connect(workspace.db_path) as conn:
            row = conn.execute(
                "SELECT path FROM reference_images WHERE filename = ?",
                (filename,),
            ).fetchone()
        if row is None or not row["path"]:
            return None
        stored = Path(row["path"])
        if not stored.is_file():
            stored = workspace.root / stored
        return stored if stored.is_file() else None

    @app.put("/api/library/{filename}")
    def update_meta(filename: str, body: MetaBody) -> dict:
        filename = safe_filename(filename)
        if ref_path(filename) is None:
            raise HTTPException(status_code=404, detail="Image introuvable dans la bibliothèque.")
        expiry = body.expiry_date.strip()
        if expiry:
            try:
                datetime.strptime(expiry, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Date attendue au format YYYY-MM-DD.") from exc
        if (workspace.images_dir / filename).is_file():
            meta = workspace.read_meta()
            meta[filename] = {"expiry_date": expiry, "credit": body.credit.strip(), "notes": body.notes.strip()}
            workspace.write_meta(meta)
        with db_module.connect(workspace.db_path) as conn:
            conn.execute(
                """
                UPDATE reference_images
                SET expiry_date = ?, credit = ?, notes = ?, updated_at = ?
                WHERE filename = ?
                """,
                (expiry or None, body.credit.strip() or None, body.notes.strip() or None, db_module.now_iso(), filename),
            )
        return {"ok": True}

    @app.delete("/api/library/{filename}")
    def delete_ref(filename: str) -> dict:
        filename = safe_filename(filename)
        local = workspace.images_dir / filename
        if local.is_file():
            local.unlink()
        meta = workspace.read_meta()
        meta.pop(filename, None)
        workspace.write_meta(meta)
        with db_module.connect(workspace.db_path) as conn:
            conn.execute("DELETE FROM reference_images WHERE filename = ?", (filename,))
        return {"ok": True}

    @app.post("/api/library/import-csv")
    async def import_csv(file: UploadFile = File(...)) -> dict:
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(raw.splitlines())
        fieldnames = set(reader.fieldnames or [])
        if "filename" not in fieldnames:
            raise HTTPException(status_code=400, detail="Le CSV doit contenir une colonne filename.")
        meta = workspace.read_meta()
        applied = 0
        for row in reader:
            filename = Path((row.get("filename") or "").strip()).name
            if not filename:
                continue
            meta[filename] = {
                "expiry_date": (row.get("expiry_date") or "").strip(),
                "credit": (row.get("credit") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
            applied += 1
        workspace.write_meta(meta)
        return {"applied": applied}

    @app.post("/api/jobs/ingest")
    def start_ingest(body: IngestBody) -> dict:
        count = workspace.write_csv()
        if count == 0:
            raise HTTPException(status_code=400, detail="Dépose d'abord au moins une image.")

        def work() -> dict:
            from rightswatch import refs as refs_module

            source = refs_module.CsvRefSource(workspace.images_dir, workspace.csv_path)

            def progress(done: int, total: int) -> None:
                jobs.update(message=f"Indexation {done}/{total}", progress={"done": done, "total": total})

            try:
                ingested = refs_module.ingest(
                    source,
                    workspace.db_path,
                    workspace.config(),
                    compute_embeddings=not body.fast,
                    progress=progress,
                )
            except refs_module.RefValidationError as exc:
                raise RuntimeError(str(exc)) from exc
            return {"ingested": ingested}

        return {"job": jobs.start("ingest", work)}

    @app.post("/api/jobs/crawl")
    def start_crawl(body: CrawlBody) -> dict:
        site = body.site.strip()
        from urllib.parse import urlparse

        parsed = urlparse(site)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="Indique une URL qui commence par http:// ou https://.")

        def work() -> dict:
            from rightswatch import crawl as crawl_module

            def progress(stats) -> None:
                jobs.update(
                    message=f"{stats.pages_visited} page(s), {stats.images_stored} image(s)",
                    progress={
                        "pages_visited": stats.pages_visited,
                        "images_found": stats.images_found,
                        "images_stored": stats.images_stored,
                        "errors": len(stats.errors),
                    },
                )

            stats = crawl_module.crawl_site(
                site,
                workspace.db_path,
                workspace.cache_dir,
                workspace.config(),
                max_pages=body.max_pages,
                compute_embeddings=not body.fast,
                resume=not body.fresh,
                progress=progress,
            )
            return {
                "pages_visited": stats.pages_visited,
                "images_found": stats.images_found,
                "images_stored": stats.images_stored,
                "errors": stats.errors[:8],
            }

        return {"job": jobs.start("crawl", work)}

    @app.post("/api/jobs/match")
    def start_match(body: MatchBody) -> dict:
        def work() -> dict:
            from rightswatch import match as match_module

            def progress(done: int, total: int) -> None:
                jobs.update(message=f"Comparaison {done}/{total}", progress={"done": done, "total": total})

            count = match_module.run_matching(
                workspace.db_path,
                workspace.config(),
                use_clip=not body.fast,
                progress=progress,
            )
            return {"matches": count}

        return {"job": jobs.start("match", work)}

    @app.get("/api/matches")
    def matches(within_days: Optional[int] = None) -> dict:
        config = workspace.config()
        window = config.report.default_within_days if within_days is None else within_days
        if window < 0 or window > 3650:
            raise HTTPException(status_code=400, detail="Fenêtre d'échéance invalide.")
        today = datetime.now(timezone.utc).date()
        rows = []
        outside_window = 0
        with db_module.connect(workspace.db_path) as conn:
            for match in db_module.get_matches(conn):
                left = days_until(match["expiry_date"], today)
                if left is not None and left > window:
                    outside_window += 1
                    continue
                pages = db_module.get_pages_for_image(conn, match["site_image_id"])
                rows.append({
                    "filename": match["filename"],
                    "expiry_date": match["expiry_date"],
                    "days_left": left,
                    "status": urgency_status(left),
                    "credit": match["credit"],
                    "notes": match["notes"],
                    "pages": pages,
                    "site_url": match["site_url"],
                    "level": match["level"],
                    "score": match["score"],
                    "confidence": match["confidence"],
                    "ref_image": "/api/media/ref/" + quote(match["filename"]),
                    "site_image": f"/api/media/site/{match['site_image_id']}",
                })
        rows.sort(key=lambda row: (1, 0) if row["days_left"] is None else (0, row["days_left"]))
        confirmed = [row for row in rows if row["confidence"] != "a_verifier"]
        to_verify = [row for row in rows if row["confidence"] == "a_verifier"]
        return {"within_days": window, "confirmed": confirmed, "to_verify": to_verify, "outside_window": outside_window}

    @app.get("/api/downloads/{name}")
    def download(name: str, within_days: Optional[int] = None):
        if name not in {"report.html", "matches.csv"}:
            raise HTTPException(status_code=404, detail="Fichier inconnu.")
        from rightswatch import report as report_module

        html_path, csv_path = report_module.generate_report(
            workspace.db_path,
            workspace.out_dir,
            workspace.config(),
            within_days=within_days,
        )
        path = html_path if name == "report.html" else csv_path
        return FileResponse(path, filename=name)

    @app.get("/api/media/ref/{filename}")
    def ref_media(filename: str) -> FileResponse:
        filename = safe_filename(filename)
        path = ref_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="Image introuvable.")
        return FileResponse(path)

    @app.get("/api/media/site/{image_id}")
    def site_media(image_id: int) -> FileResponse:
        with db_module.connect(workspace.db_path) as conn:
            row = conn.execute("SELECT local_path FROM site_images WHERE id = ?", (image_id,)).fetchone()
        if row is None or not row["local_path"]:
            raise HTTPException(status_code=404, detail="Image introuvable.")
        path = Path(row["local_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Fichier image absent du cache.")
        return FileResponse(path)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
