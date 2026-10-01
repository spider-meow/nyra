"""The built interface (single-page app) and what wraps every response: compression,
security headers, error handlers.

`create_app` calls these in a fixed order, because the order is the behavior: the
middleware stack is built in the order added, `/assets` is mounted before the API
routes, and the catch-all page route must come after every API route.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from ...observability import sentry_ingest_origin, warn_if_sentry_env_is_wrong
from .common import log


def _content_security_policy(supabase_url: str, sentry_dsn: str) -> str:
    sentry = sentry_ingest_origin(sentry_dsn)  # the browser reports to this one host, if Sentry is on
    supabase = f"{urlparse(supabase_url).scheme}://{urlparse(supabase_url).netloc}" if supabase_url else ""
    return "; ".join([
        "default-src 'self'",
        f"img-src 'self' data: blob: {supabase}".strip(),
        " ".join(origin for origin in ("connect-src 'self'", supabase, sentry) if origin),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ])


def add_security(app: FastAPI, supabase_url: str, sentry_dsn: str = "") -> None:
    """Gzip, security headers and error handlers."""
    csp = _content_security_policy(supabase_url, sentry_dsn)
    warn_if_sentry_env_is_wrong(sentry_dsn)
    # Added before `security_headers` so it sits inside it: the router's whole responses reach it
    # (the "http" middleware below streams, and gzip only honours `minimum_size` on unstreamed bodies).
    # Small bodies (errors, health check) stay as they are; Content-Disposition and status codes are untouched.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)  # 9 blocks the event loop for little gain

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", csp)
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            # Vite puts a content hash in every file name under /assets: a given URL never changes.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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


def mount_assets(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


def add_pages(app: FastAPI, dist: Path) -> None:
    """The app shell at `/` and for every client-side route. Register last: it matches any path."""

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
