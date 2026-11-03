"""The people of an organization: who is in it, invitations, roles. Reading is open to every member."""

from __future__ import annotations

import uuid
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit as cloud_audit
from .. import db as cloud_db
from .. import storage as cloud_storage
from .. import usage as cloud_usage
from .common import Ctx, admin_dep, enforce, get_ctx, member_dep, seen_as

router = APIRouter()

MEMBER = "/api/orgs/{org_id}/members"


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "client"] = "client"


class RoleBody(BaseModel):
    role: Literal["admin", "client"]


class EmailLimit(Exception):
    """The organization's invitations of the day are used up."""


def find_or_invite_user(database_url: str, supabase_url: str, service_key: str, public_url: str, email: str,
                        claim: Optional[Callable[[], bool]] = None) -> tuple[str, bool]:
    """Supabase Auth user id for this e-mail, inviting them if they have no account or never opened their invitation (it is sent again).
    Returns (id, invited). `claim` is asked before an e-mail is sent (never for an account that is in use); False raises EmailLimit."""
    with cloud_db.connect(database_url) as conn:
        row = conn.execute(f"SELECT id, {cloud_db.PENDING_SQL} AS pending FROM auth.users u WHERE lower(email) = lower(%s)", (email,)).fetchone()
    if row is not None and not row["pending"]:
        return str(row["id"]), False
    if claim is not None and not claim():
        raise EmailLimit()
    client = cloud_storage.get_client(supabase_url, service_key)
    options = {"redirect_to": public_url.rstrip("/") + "/login"} if public_url else {}
    return str(client.auth.admin.invite_user_by_email(email, options).user.id), True


def _member_json(row: dict, caller: uuid.UUID) -> dict:
    pending = bool(row["pending"])
    return {"user_id": str(row["user_id"]), "email": row["email"], "role": row["role"], "you": row["user_id"] == caller,
            "pending": pending, "invited_at": row["invited_at"].isoformat() if pending and row["invited_at"] else None}


@router.get(MEMBER)
def list_members(org_id: uuid.UUID, member=Depends(member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_members(conn, org_id)
    return {"members": [_member_json(row, member.user_id) for row in rows]}


def _send_invitation(ctx: Ctx, org_id: uuid.UUID, email: str) -> tuple[str, bool]:
    """`find_or_invite_user` for this organization: one of its day's e-mails is used when a mail leaves (429 when none is left, and
    given back when the provider refuses: 502). Returns (user id, whether a mail was sent)."""
    s = ctx.settings
    claimed = False

    def claim() -> bool:
        nonlocal claimed
        with cloud_db.connect(s.database_url) as conn:
            claimed = cloud_usage.claim_email(conn, org_id, cloud_usage.quota_for(conn, s.base_config().quota, org_id).emails_per_day)
        return claimed

    try:
        return find_or_invite_user(s.database_url, s.supabase_url, s.service_role_key, s.public_url, email, claim)
    except EmailLimit as exc:
        raise HTTPException(status_code=429, detail="Limite d'invitations atteinte pour aujourd'hui. Réessayez demain.") from exc
    except Exception as exc:  # noqa: BLE001 - the provider's refusal (bad address, rate limit) is shown to the admin
        if claimed:  # nothing was sent: give the place back
            with cloud_db.connect(s.database_url) as conn:
                cloud_usage.release_email(conn, org_id)
        raise HTTPException(status_code=502, detail=f"L'invitation n'a pas pu être envoyée : {str(exc)[:200]}") from exc


@router.post(MEMBER)
def invite_member(org_id: uuid.UUID, body: InviteBody, member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    email = body.email.strip().lower()
    if "@" not in email or " " in email:
        raise HTTPException(status_code=400, detail="Adresse e-mail invalide.")
    s = ctx.settings
    enforce(ctx, org_id, "members")
    with cloud_db.connect(s.database_url) as conn:
        if cloud_db.member_by_email(conn, org_id, email) is not None:  # before any mail: an invitation still pending is sent again with « Renvoyer »
            raise HTTPException(status_code=409, detail="Cette personne fait déjà partie de l'équipe.")
    user_id, invited = _send_invitation(ctx, org_id, email)
    with cloud_db.connect(s.database_url) as conn:
        existing = cloud_db.get_membership(conn, user_id=uuid.UUID(user_id), org_id=org_id)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Cette personne fait déjà partie de l'équipe.")
        cloud_db.add_membership(conn, user_id=uuid.UUID(user_id), org_id=org_id, role=body.role)
        cloud_audit.record(conn, org_id, member.user_id, "member.invite", "user", user_id, **seen_as(member), role=body.role, invited=invited)
    return {"invited": invited, "member": {"user_id": user_id, "email": email, "role": body.role, "you": False, "pending": invited,
                                           "invited_at": None}}


@router.post(MEMBER + "/{user_id}/resend")
def resend_invitation(org_id: uuid.UUID, user_id: uuid.UUID, member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The invitation mail again, for a member who never opened it. Counts against the organization's mails of the day like the first one."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = cloud_db.get_member(conn, org_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Cette personne ne fait pas partie de l'équipe.")
    if not row["pending"]:
        raise HTTPException(status_code=409, detail="Cette personne a déjà accepté l'invitation.")
    _, invited = _send_invitation(ctx, org_id, row["email"])
    if invited:  # false only when the invitation was opened in the meantime: no mail left, nothing to trace
        with cloud_db.connect(ctx.settings.database_url) as conn:
            cloud_audit.record(conn, org_id, member.user_id, "member.reinvite", "user", user_id, **seen_as(member))
    return {"invited": invited}


def _keep_one_admin(conn, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    # ponytail: two admins demoting each other at the same instant could both pass; a lock would close it.
    current = cloud_db.get_membership(conn, user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Cette personne ne fait pas partie de l'équipe.")
    if current["role"] == "admin" and cloud_db.count_admins(conn, org_id) <= 1:
        raise HTTPException(status_code=409, detail="Il faut garder au moins un administrateur.")


@router.put(MEMBER + "/{user_id}")
def change_role(org_id: uuid.UUID, user_id: uuid.UUID, body: RoleBody, member=Depends(admin_dep),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if body.role != "admin":
            _keep_one_admin(conn, org_id, user_id)
        if not cloud_db.set_member_role(conn, org_id=org_id, user_id=user_id, role=body.role):
            raise HTTPException(status_code=404, detail="Cette personne ne fait pas partie de l'équipe.")
        cloud_audit.record(conn, org_id, member.user_id, "member.role", "user", user_id, **seen_as(member), role=body.role)
    return {"role": body.role}


@router.delete(MEMBER + "/{user_id}")
def remove_member(org_id: uuid.UUID, user_id: uuid.UUID, member=Depends(admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    if user_id == member.user_id:
        raise HTTPException(status_code=409, detail="Vous ne pouvez pas vous retirer vous-même.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        _keep_one_admin(conn, org_id, user_id)
        if not cloud_db.remove_member(conn, org_id=org_id, user_id=user_id):
            raise HTTPException(status_code=404, detail="Cette personne ne fait pas partie de l'équipe.")
        cloud_audit.record(conn, org_id, member.user_id, "member.remove", "user", user_id, **seen_as(member))
    return {"removed": True}
