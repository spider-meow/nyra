"""The monthly read of a brand: read by every member, switched on and off by the administrators (see `cloud/schedule.py`)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from .. import db as cloud_db
from .. import schedule
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, brand_member_dep, get_ctx, iso

router = APIRouter()


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    day_of_month: Optional[StrictInt] = None  # needed (1 to 28) to switch on; ignored when switching off


def _state(conn, scope: BrandScope) -> dict:
    row = conn.execute("SELECT enabled, day_of_month, last_run_at FROM brand_schedules WHERE brand_id = %s",
                       (scope.brand_id,)).fetchone()
    default = schedule.default_day(scope.brand_id)
    enabled = bool(row and row["enabled"])
    day = row["day_of_month"] if row else default
    last = row["last_run_at"] if row else None
    next_at = schedule.next_run(scope.brand_id, day, last, datetime.now(timezone.utc)) if enabled else None
    # `last_run_at` is the rule of the due date (it is also set as a mark when a never-read brand is switched on after its
    # date): what is shown as the last read is the end of the last scheduled read that really finished.
    read = conn.execute(
        """SELECT finished_at FROM jobs WHERE brand_id = %s AND kind = 'crawl' AND status = 'done'
           AND params @> '{"scheduled": true}' ORDER BY finished_at DESC NULLS LAST LIMIT 1""",
        (scope.brand_id,),
    ).fetchone()
    return {"enabled": enabled, "day_of_month": day, "default_day": default, "next_run_at": iso(next_at),
            "last_read_at": iso(read["finished_at"]) if read else None}


@router.get(f"{BRAND}/schedule")
def get_schedule(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return _state(conn, scope)


@router.put(f"{BRAND}/schedule")
def put_schedule(body: ScheduleBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    day = body.day_of_month
    if (day is None and body.enabled) or (day is not None and not 1 <= day <= 28):
        raise HTTPException(status_code=400, detail="Le jour du mois doit être compris entre 1 et 28.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = conn.execute("SELECT enabled, day_of_month FROM brand_schedules WHERE brand_id = %s FOR UPDATE",
                           (scope.brand_id,)).fetchone()
        if body.enabled and not (row and row["enabled"] and row["day_of_month"] == day):
            schedule.enable(conn, scope.brand_id, scope.org_id, day)
            scope.audit(conn, "schedule.enable", "brand_schedule", scope.brand_id, day_of_month=day)
        elif not body.enabled and row and row["enabled"]:
            schedule.disable(conn, scope.brand_id)
            scope.audit(conn, "schedule.disable", "brand_schedule", scope.brand_id)
        # Anything else asks for what already is: nothing changes, so there is nothing to trace.
        return _state(conn, scope)
