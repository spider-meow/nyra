"""Session routes: health check, sign-in configuration, who am I, the caller's organizations."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ... import observability
from .. import alerts as cloud_alerts
from .. import auth as cloud_auth
from .. import db as cloud_db
from .. import insights as cloud_insights
from .common import Ctx, brand_json, get_ctx, staff_dep, user_dep

router = APIRouter()


@router.get("/api/healthz")
def healthz(ctx: Ctx = Depends(get_ctx)) -> dict:
    cloud_db.ping(ctx.settings.database_url)
    return {"status": "ok"}


@router.get("/api/workerz")
def workerz(ctx: Ctx = Depends(get_ctx)) -> JSONResponse:
    """For an uptime monitor: 503 while a critical alert is on (`cloud/alerts.py`: no worker, a job waiting for nothing,
    a full disk). `alerts` lists them all with their severity; it holds no organization, brand or user."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        state = cloud_alerts.current(conn)
    critical = [a["code"] for a in state["alerts"] if a["severity"] == "critical"]
    return JSONResponse({"status": critical[0] if critical else "ok", "problems": critical,
                         **state, "alerts": cloud_alerts.public(state["alerts"])},
                        status_code=503 if critical else 200)


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
