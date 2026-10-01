"""Session routes: health check, sign-in configuration, who am I, the caller's organizations."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import observability
from .. import auth as cloud_auth
from .. import db as cloud_db
from .. import insights as cloud_insights
from .common import Ctx, brand_json, get_ctx, staff_dep, user_dep

router = APIRouter()


@router.get("/api/healthz")
def healthz(ctx: Ctx = Depends(get_ctx)) -> dict:
    cloud_db.ping(ctx.settings.database_url)
    return {"status": "ok"}


@router.get("/api/auth/config")
def auth_config(ctx: Ctx = Depends(get_ctx)) -> dict:
    """Public settings of the page: sign-in, and `sentry` (null = no browser reporting)."""
    return {
        "supabaseUrl": ctx.settings.supabase_url,
        "anonKey": ctx.settings.anon_key,
        "sentry": observability.browser_sentry(ctx.settings.sentry_browser_dsn),
    }


@router.get("/api/me")
def me(claims: cloud_auth.Claims = Depends(user_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        staff = cloud_insights.is_staff(conn, claims.user_id)
    return {"user_id": str(claims.user_id), "email": claims.email, "staff": staff}


@router.get("/api/staff/insights")
def staff_insights(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return cloud_insights.platform_insights(conn)


@router.get("/api/orgs")
def list_orgs(claims: cloud_auth.Claims = Depends(user_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        memberships = cloud_db.list_memberships_for_user(conn, claims.user_id)
        brands = cloud_db.list_brands(conn, [m["org_id"] for m in memberships])
    return {
        "organizations": [
            {
                "org_id": str(m["org_id"]), "name": m["org_name"], "slug": m["org_slug"], "role": m["role"],
                "brands": [brand_json(b) for b in brands if b["org_id"] == m["org_id"]],
            }
            for m in memberships
        ]
    }
