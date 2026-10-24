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

import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException

from nyra.config import Config, load_config

from . import mailer, ratelimit
from . import storage as cloud_storage
from .routes import (
    audit,
    brands,
    collections,
    interface,
    jobs,
    labels,
    library,
    logos,
    matches,
    members,
    occurrences,
    reports_settings,
    session,
    site_images,
    staff,
    usage,
)
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
        public_url: str = "",
        resend_api_key: str = "",
        mail_from: str = "",
        config_path: Optional[Path] = None,
    ):
        self.database_url = database_url
        self.supabase_url = supabase_url
        self.service_role_key = service_role_key
        self.jwt_secret = jwt_secret
        self.anon_key = anon_key
        self.sentry_browser_dsn = sentry_browser_dsn
        self.public_url = public_url
        self.resend_api_key = resend_api_key  # secret: never logged (see mailer.py)
        self.mail_from = mail_from
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
    mail_from = mailer.sender_from_env(os.environ.get("NYRA_MAIL_FROM", ""), bool(os.environ.get("RESEND_API_KEY")))
    return CloudSettings(
        database_url=os.environ["DATABASE_URL"],
        supabase_url=os.environ["SUPABASE_URL"],
        service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        jwt_secret=os.environ.get("SUPABASE_JWT_SECRET") or None,
        anon_key=os.environ.get("SUPABASE_ANON_KEY", ""),
        sentry_browser_dsn=os.environ.get("SENTRY_BROWSER_DSN", ""),
        public_url=os.environ.get("NYRA_PUBLIC_URL", ""),
        resend_api_key=os.environ.get("RESEND_API_KEY", ""),
        mail_from=mail_from,
        config_path=config_path,
    )


def _limiter_dependency(ctx: Ctx):
    """The rate limit, counted per verified user (see cloud/ratelimit.py); read from the environment when the app is built."""

    def identify(token: str) -> Optional[str]:
        try:
            return str(ctx.user(token).user_id)
        except (HTTPException, ValueError):  # not valid: the route itself answers 401 as before
            return None

    return Depends(ratelimit.enforce(ratelimit.RateLimiter(ratelimit.limits_from_env(os.environ)), identify))


def create_app(settings: CloudSettings) -> FastAPI:
    ctx = Ctx(settings)
    app = FastAPI(title="Nyra", docs_url=None, redoc_url=None, openapi_url=None, dependencies=[_limiter_dependency(ctx)])
    app.state.ctx = ctx
    dist = frontend_dir() / "dist"
    # The order is the behavior: middleware and handlers first, `/assets`, then the API routes
    # (the order of `/library/export-csv` against `/library/{filename}` matters), the catch-all page last.
    interface.add_security(app, settings.supabase_url, settings.sentry_browser_dsn)
    interface.mount_assets(app, dist)
    for module in (session, staff, audit, brands, members, collections, library, occurrences, jobs, matches, site_images, logos, reports_settings, usage):
        app.include_router(module.router)
    if os.environ.get("NYRA_LABELS") == "1":  # labels (types and contents) are switched off until they are calibrated
        app.include_router(labels.router)
    interface.add_pages(app, dist)
    return app
