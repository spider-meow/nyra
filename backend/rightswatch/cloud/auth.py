"""Verifies a Supabase Auth JWT and resolves the caller's role for a
given organization.

Supabase projects sign JWTs one of two ways, and which one a given
project uses isn't something this code can assume:

- **Legacy shared secret (HS256)** — still the default for most existing
  projects. Verified locally with `SUPABASE_JWT_SECRET`, no network call.
- **JWT signing keys (RS256/ES256)** — the newer, recommended setup.
  Verified locally too, against the project's public JWKS
  (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), fetched once and
  cached by `PyJWKClient` rather than on every request.

`verify_jwt` picks whichever `SUPABASE_JWT_SECRET` being set (or not)
implies, so this works against either kind of project without the
caller needing to know which one it is. Either way, verification never
calls Supabase's Auth API per request — a network round trip on every
authenticated call would be a real latency and availability cost for no
benefit once the signing key is known.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from . import db as cloud_db

EXPECTED_AUDIENCE = "authenticated"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    user_id: uuid.UUID
    email: Optional[str]


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(supabase_url: str) -> PyJWKClient:
    if supabase_url not in _jwks_clients:
        jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_clients[supabase_url] = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_clients[supabase_url]


def verify_jwt(token: str, *, supabase_url: str, jwt_secret: Optional[str] = None) -> Claims:
    try:
        if jwt_secret:
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"], audience=EXPECTED_AUDIENCE)
        else:
            signing_key = _jwks_client(supabase_url).get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token, signing_key.key, algorithms=["RS256", "ES256"], audience=EXPECTED_AUDIENCE
            )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid or expired token: {exc}") from exc

    sub = payload.get("sub")
    if not sub:
        raise AuthError("Token has no subject claim")
    return Claims(user_id=uuid.UUID(sub), email=payload.get("email"))


def _bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Jeton d'authentification manquant.")
    return authorization.removeprefix("Bearer ").strip()


def require_member(*, supabase_url: str, jwt_secret: Optional[str], database_url: str):
    """FastAPI dependency factory: verifies the JWT, then checks the
    caller has *any* membership in the org named by the route's
    `org_id` path parameter. Use `require_admin` instead where only
    admins should act."""

    def dependency(org_id: uuid.UUID, token: str = Depends(_bearer_token)) -> Member:
        try:
            claims = verify_jwt(token, supabase_url=supabase_url, jwt_secret=jwt_secret)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        with cloud_db.connect(database_url) as conn:
            membership = cloud_db.get_membership(conn, user_id=claims.user_id, org_id=org_id)
        if membership is None:
            raise HTTPException(status_code=403, detail="Pas membre de cette organisation.")
        return Member(user_id=claims.user_id, org_id=org_id, role=membership["role"])

    return dependency


def require_admin(*, supabase_url: str, jwt_secret: Optional[str], database_url: str):
    member_dep = require_member(supabase_url=supabase_url, jwt_secret=jwt_secret, database_url=database_url)

    def dependency(member: Member = Depends(member_dep)) -> Member:
        if member.role != "admin":
            raise HTTPException(status_code=403, detail="Réservé aux administrateurs de l'organisation.")
        return member

    return dependency
