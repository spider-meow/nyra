"""The Nyra team's back office: clients and their brands, accounts, and who is a platform admin.

Everything here is for platform admins only (`platform_staff`). The people of one organization are
managed with the ordinary `/api/orgs/{org_id}/members` routes, which accept a platform admin too.
"""

from __future__ import annotations

import socket
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import alerts as cloud_alerts
from .. import audit as cloud_audit
from .. import db as cloud_db
from .. import hostmetrics
from .. import insights as cloud_insights
from .. import usage as cloud_usage
from .common import Ctx, get_ctx, iso, staff_dep
from .members import find_or_invite_user

router = APIRouter()

# The web process's own reading (CPU needs the previous one): a single Sampler holding one reading.
_WEB = hostmetrics.Sampler()


class OrganizationBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(default="", max_length=63)
    admin_email: str = Field(min_length=3, max_length=254)


class EmailBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)


def _checked_email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email or " " in email:
        raise HTTPException(status_code=400, detail="Adresse e-mail invalide.")
    return email


def _find_or_invite(ctx: Ctx, email: str) -> tuple[uuid.UUID, bool]:
    s = ctx.settings
    try:
        user_id, invited = find_or_invite_user(s.database_url, s.supabase_url, s.service_role_key, s.public_url, email)
    except Exception as exc:  # noqa: BLE001 - the provider's refusal (bad address, rate limit) is shown to the platform admin
        raise HTTPException(status_code=502, detail=f"L'invitation n'a pas pu être envoyée : {str(exc)[:200]}") from exc
    return uuid.UUID(user_id), invited


@router.get("/api/staff/organizations")
def list_organizations(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Every client with its brands (name, creation date, who created it): the base of the billing."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        orgs, brands = cloud_db.organizations_overview(conn)
    by_org = defaultdict(list)
    for brand in brands:
        by_org[brand["org_id"]].append({
            "id": str(brand["id"]), "name": brand["name"], "slug": brand["slug"],
            "created_at": iso(brand["created_at"]), "created_by": brand["created_by"],
        })
    return {"organizations": [
        {"id": str(org["id"]), "name": org["name"], "slug": org["slug"], "created_at": iso(org["created_at"]),
         "members": org["members"], "brands": by_org[org["id"]]}
        for org in orgs
    ]}


@router.post("/api/staff/organizations")
def create_organization(body: OrganizationBody, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """A client organization with a first brand of the same name (billed like any brand) and its first admin."""
    name = body.name.strip()
    try:
        slug = cloud_db.slugify(body.slug or name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identifiant invalide : lettres, chiffres et tirets.") from exc
    email = _checked_email(body.admin_email)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if cloud_db.get_organization_by_slug(conn, slug):
            raise HTTPException(status_code=409, detail=f"Une organisation utilise déjà l'identifiant {slug}.")
    user_id, invited = _find_or_invite(ctx, email)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        org_id = cloud_db.create_organization(conn, name=name, slug=slug, created_by=claims.user_id)
        cloud_db.add_membership(conn, user_id=user_id, org_id=org_id, role="admin")
        cloud_audit.record(conn, org_id, claims.user_id, "organization.create", "organization", org_id, invited=invited)
    return {"id": str(org_id), "slug": slug, "invited": invited}


@router.get("/api/staff/users")
def list_users(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        users, memberships, staff = cloud_db.users_overview(conn)
    by_user = defaultdict(list)
    for m in memberships:
        by_user[m["user_id"]].append({"org_id": str(m["org_id"]), "org_name": m["org_name"], "role": m["role"], "since": iso(m["since"])})
    return {"users": [
        {"user_id": str(u["id"]), "email": u["email"] or "", "last_sign_in_at": iso(u["last_sign_in_at"]),
         "staff": u["id"] in staff, "memberships": by_user[u["id"]]}
        for u in users
    ]}


@router.get("/api/staff/admins")
def list_admins(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = conn.execute(
            "SELECT s.user_id, u.email FROM platform_staff s JOIN auth.users u ON u.id = s.user_id ORDER BY lower(u.email)"
        ).fetchall()
    return {"admins": [{"user_id": str(row["user_id"]), "email": row["email"], "you": row["user_id"] == claims.user_id} for row in rows]}


@router.post("/api/staff/admins")
def add_admin(body: EmailBody, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    user_id, invited = _find_or_invite(ctx, _checked_email(body.email))
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_insights.set_staff(conn, user_id, True)
        cloud_audit.record(conn, None, claims.user_id, "staff.add", "user", user_id)  # no organization: org_id is null
    return {"invited": invited}


@router.delete("/api/staff/admins/{user_id}")
def remove_admin(user_id: uuid.UUID, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    if user_id == claims.user_id:
        raise HTTPException(status_code=409, detail="Vous ne pouvez pas vous retirer vous-même.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_insights.is_staff(conn, user_id):
            raise HTTPException(status_code=404, detail="Cette personne n'est pas platform admin.")
        cloud_insights.set_staff(conn, user_id, False)
        cloud_audit.record(conn, None, claims.user_id, "staff.remove", "user", user_id)
    return {"removed": True}


@router.get("/api/staff/server")
def server_usage(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """What each container uses, as `docker stats` would show: the web process live, the workers as last published,
    and the `alerts` on right now (`cloud/alerts.py`)."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        workers = hostmetrics.published(conn)
        state = cloud_alerts.current(conn, workers)
    web = {"instance": socket.gethostname(), "role": "web", "reading": _WEB.read(), "age_seconds": 0.0}
    return {"services": [web, *workers], "alerts": state["alerts"]}


class QuotasBody(BaseModel):
    limits: dict[str, float | None] = Field(default_factory=dict, max_length=20)


@router.get("/api/staff/usage")
def usage_overview(claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Every organization's usage of the month against its limits (the defaults of config.yaml unless overridden)."""
    base = ctx.settings.base_config().quota
    with cloud_db.connect(ctx.settings.database_url) as conn:
        organizations = cloud_usage.overview(conn, base, limit=cloud_usage.ORGS_LIMIT + 1)
    return {"month": cloud_usage.month_start().isoformat(), "defaults": cloud_usage.defaults(base),
            "organizations": organizations[:cloud_usage.ORGS_LIMIT], "truncated": len(organizations) > cloud_usage.ORGS_LIMIT}


@router.put("/api/staff/organizations/{org_id}/quotas")
def set_quotas(org_id: uuid.UUID, body: QuotasBody, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Replace the limits of one organization: a value sets it, an empty one goes back to the default. Platform admins only."""
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            kept = cloud_usage.set_overrides(conn, org_id, body.limits, claims.user_id)
            if kept is not None:
                cloud_audit.record(conn, org_id, claims.user_id, "quota.set", "organization", org_id, limits=len(kept))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if kept is None:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")
    return {"overrides": kept}
