"""Reading the audit log: for the administrators of an organization, and for the Nyra team (back office)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import audit as cloud_audit
from .. import db as cloud_db
from .common import Ctx, admin_dep, get_ctx, iso, staff_dep

router = APIRouter()


def _page(ctx: Ctx, org_id: uuid.UUID, limit: int, offset: int, **filters) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if cloud_db.get_organization(conn, org_id) is None:
            raise HTTPException(status_code=404, detail="Organisation introuvable.")
        rows = cloud_audit.events(conn, org_id, limit=limit + 1, offset=offset, **filters)
    return {
        "events": [
            {"id": row["id"], "at": iso(row["created_at"]), "action": row["action"], "object_type": row["object_type"],
             "object_id": row["object_id"], "brand_id": str(row["brand_id"]) if row["brand_id"] else None,
             "brand": row["brand_name"] or "", "user_id": str(row["user_id"]) if row["user_id"] else None,
             "email": row["email"] or "", "detail": row["detail"]}
            for row in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }


def _filters(brand_id: Optional[uuid.UUID], action: Optional[str], user_id: Optional[uuid.UUID],
             since: Optional[datetime], until: Optional[datetime]) -> dict:
    return {"brand_id": brand_id, "action": action, "user_id": user_id, "since": since, "until": until}


@router.get("/api/orgs/{org_id}/audit")
def audit_log(org_id: uuid.UUID, limit: int = Query(50, ge=1, le=cloud_audit.MAX_PAGE),
              offset: int = Query(0, ge=0, le=cloud_audit.MAX_OFFSET), brand_id: Optional[uuid.UUID] = None,
              action: Optional[str] = Query(None, max_length=64), user_id: Optional[uuid.UUID] = None,
              since: Optional[datetime] = None, until: Optional[datetime] = None,
              member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """What the people of this organization did, newest first. Administrators only."""
    return _page(ctx, org_id, limit, offset, **_filters(brand_id, action, user_id, since, until))


@router.get("/api/staff/organizations/{org_id}/audit")
def staff_audit_log(org_id: uuid.UUID, limit: int = Query(50, ge=1, le=cloud_audit.MAX_PAGE),
                    offset: int = Query(0, ge=0, le=cloud_audit.MAX_OFFSET), brand_id: Optional[uuid.UUID] = None,
                    action: Optional[str] = Query(None, max_length=64), user_id: Optional[uuid.UUID] = None,
                    since: Optional[datetime] = None, until: Optional[datetime] = None,
                    claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The same log for the Nyra team (back office), with the team's own actions, which the organization's log leaves out."""
    return _page(ctx, org_id, limit, offset, with_staff=True, **_filters(brand_id, action, user_id, since, until))
