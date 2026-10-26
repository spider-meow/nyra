"""What an organization has used against its limits, for its administrators (and the Nyra team)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from .. import db as cloud_db
from .. import usage as cloud_usage
from .common import Ctx, admin_dep, get_ctx

router = APIRouter()


@router.get("/api/orgs/{org_id}/usage")
def org_usage(org_id: uuid.UUID, member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The month's usage and the limits, line by line (`level`: ok, warning from 80 %, reached)."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        entries = cloud_usage.overview(conn, ctx.settings.base_config().quota, org_id)
    if not entries:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")
    return {"month": cloud_usage.month_start().isoformat(), "items": entries[0]["items"], "counters": entries[0]["counters"]}
