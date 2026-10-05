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

from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from nyra.config import Config, load_config

from . import storage as cloud_storage
from .routes import brands, interface, jobs, library, matches, occurrences, reports_settings, session, site_images
from .routes.common import Ctx


def frontend_dir() -> Path:
    """The built interface lives next to the backend, not inside the Python package."""
    packaged = Path(__file__).resolve().parents[3] / "frontend"
    if packaged.is_dir():
        return packaged
    return Path.cwd() / "frontend"


class CloudSettings:
    def __init__(
        self,
        *,
        database_url: str,
        supabase_url: str,
        service_role_key: str,
        jwt_secret: Optional[str],
        anon_key: str = "",
        sentry_browser_dsn: str = "",
        config_path: Optional[Path] = None,
    ):
        self.database_url = database_url
        self.supabase_url = supabase_url
        self.service_role_key = service_role_key
        self.jwt_secret = jwt_secret
        self.anon_key = anon_key
        self.sentry_browser_dsn = sentry_browser_dsn
        self.config_path = config_path
        self._storage = None

    def base_config(self) -> Config:
        return load_config(self.config_path)

    def storage_client(self):
        if self._storage is None:
            self._storage = cloud_storage.get_client(self.supabase_url, self.service_role_key)
        return self._storage


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
        sentry_browser_dsn=os.environ.get("SENTRY_BROWSER_DSN", ""),
        config_path=config_path,
    )


def create_app(settings: CloudSettings) -> FastAPI:
    app = FastAPI(title="Nyra", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = Ctx(settings)
    dist = frontend_dir() / "dist"
    # The order is the behavior: middleware and handlers first, `/assets`, then the API routes
    # (the order of `/library/export-csv` against `/library/{filename}` matters), the catch-all page last.
    interface.add_security(app, settings.supabase_url, settings.sentry_browser_dsn)
    interface.mount_assets(app, dist)
    for module in (session, brands, library, occurrences, jobs, matches, site_images, reports_settings):
        app.include_router(module.router)
    interface.add_pages(app, dist)
    return app
