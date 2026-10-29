"""The e-mails Nyra sends to a member (today only the monthly one, see `cloud/digest.py`): two switches on the membership.

`monthly_email_wanted` is the person's own choice, `monthly_email_allowed` the Nyra team's. Both default to yes. The mail leaves
for an administrator who is allowed AND wants it (`digest.receives_monthly`). The service e-mails (invitation, new password) are
Supabase Auth's: they are not here and cannot be switched off.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from .. import audit as cloud_audit
from .. import auth as cloud_auth
from .. import db as cloud_db
from .common import Ctx, get_ctx, member_dep, seen_as, staff_dep

router = APIRouter()

ME = "/api/orgs/{org_id}/me/notifications"
TEAM = "/api/staff/organizations/{org_id}/members/{user_id}/notifications"


class MineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_email: StrictBool


class TeamBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_email_allowed: StrictBool


def _state(conn, member: cloud_auth.Member) -> dict:
    """`eligible`: only an administrator receives the monthly mail; `allowed` is shown, never changed, by the person."""
    row = conn.execute("SELECT monthly_email_wanted, monthly_email_allowed FROM memberships WHERE user_id = %s AND org_id = %s",
                       (member.user_id, member.org_id)).fetchone()
    if row is None and member.staff:  # a platform admin has no line to set: nothing to show, nothing sent to them
        return {"monthly_email": {"wanted": False, "allowed": False, "eligible": False}}
    if row is None:
        raise HTTPException(status_code=404, detail="Vous ne faites pas partie de cette organisation.")
    return {"monthly_email": {"wanted": row["monthly_email_wanted"], "allowed": row["monthly_email_allowed"], "eligible": member.role == "admin"}}


@router.get(ME)
def get_notifications(member: cloud_auth.Member = Depends(member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        return _state(conn, member)


@router.put(ME)
def put_notifications(body: MineBody, member: cloud_auth.Member = Depends(member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The caller's own line, never another's: the member is the one the session names. Same value: nothing changes, no trace."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        changed = conn.execute(
            "UPDATE memberships SET monthly_email_wanted = %s WHERE user_id = %s AND org_id = %s AND monthly_email_wanted IS DISTINCT FROM %s",
            (body.monthly_email, member.user_id, member.org_id, body.monthly_email)).rowcount
        if changed:
            cloud_audit.record(conn, member.org_id, member.user_id, "notifications.update", "user", member.user_id, **seen_as(member), monthly_email=body.monthly_email)
        return _state(conn, member)


@router.put(TEAM)
def put_team_notifications(org_id: uuid.UUID, user_id: uuid.UUID, body: TeamBody, claims=Depends(staff_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The Nyra team allows or blocks the monthly mail for one member of this organization. Same value: no trace."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = conn.execute("SELECT monthly_email_allowed FROM memberships WHERE org_id = %s AND user_id = %s FOR UPDATE", (org_id, user_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Cette personne ne fait pas partie de l'organisation.")
        if row["monthly_email_allowed"] != body.monthly_email_allowed:
            conn.execute("UPDATE memberships SET monthly_email_allowed = %s WHERE org_id = %s AND user_id = %s", (body.monthly_email_allowed, org_id, user_id))
            cloud_audit.record(conn, org_id, claims.user_id, "notifications.staff_update", "user", user_id, monthly_email_allowed=body.monthly_email_allowed)
    return {"monthly_email_allowed": body.monthly_email_allowed}
