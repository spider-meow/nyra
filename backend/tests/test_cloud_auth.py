"""backend/nyra/cloud/auth.py.

`verify_jwt`'s HS256 path (legacy Supabase JWT secret) and its JWKS/RS256
path (newer Supabase signing keys) need no live Supabase project — a
locally-signed test token and, for the JWKS path, a mocked `PyJWKClient`
are enough. The `require_member`/`require_admin` FastAPI dependency tests
need a real membership lookup, so they use the same TEST_DATABASE_URL-gated
fixtures as test_cloud_db.py and skip cleanly without one.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import patch

import pytest

pytest.importorskip("jwt")

import jwt as pyjwt

from nyra.cloud import auth

SECRET = "test-secret-at-least-32-bytes-long!!"
SUPABASE_URL = "https://example.supabase.co"


def _token(user_id, *, secret=SECRET, aud="authenticated", exp_delta=3600, **extra) -> str:
    payload = {"sub": str(user_id), "aud": aud, "exp": int(time.time()) + exp_delta, **extra}
    return pyjwt.encode(payload, secret, algorithm="HS256")


# --- HS256 (legacy shared secret) --------------------------------------

def test_verify_jwt_hs256_valid_token():
    user_id = uuid.uuid4()
    token = _token(user_id, email="a@b.com")
    claims = auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret=SECRET)
    assert claims.user_id == user_id
    assert claims.email == "a@b.com"


def test_verify_jwt_hs256_expired():
    token = _token(uuid.uuid4(), exp_delta=-10)
    with pytest.raises(auth.AuthError):
        auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret=SECRET)


def test_verify_jwt_hs256_wrong_secret():
    token = _token(uuid.uuid4())
    with pytest.raises(auth.AuthError):
        auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret="a-completely-different-secret!!")


def test_verify_jwt_hs256_wrong_audience():
    token = _token(uuid.uuid4(), aud="anon")
    with pytest.raises(auth.AuthError):
        auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret=SECRET)


def test_verify_jwt_missing_subject():
    token = pyjwt.encode({"aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256")
    with pytest.raises(auth.AuthError):
        auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret=SECRET)


# --- JWKS / RS256 (newer signing keys), mocked (no live Supabase needed) --

def test_verify_jwt_rs256_via_jwks():
    cryptography = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    user_id = uuid.uuid4()
    token = pyjwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600},
        pem, algorithm="RS256", headers={"kid": "test-key"},
    )

    class FakeSigningKey:
        key = private_key.public_key()

    class FakeJWKClient:
        def __init__(self, *a, **kw):
            pass

        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey()

    with patch("nyra.cloud.auth.PyJWKClient", FakeJWKClient):
        claims = auth.verify_jwt(token, supabase_url=SUPABASE_URL, jwt_secret=SECRET)
    assert claims.user_id == user_id


# --- FastAPI dependency chain (needs a real membership lookup) -----------

def test_require_member_and_admin_dependencies(cloud_database_url, cloud_org):
    fastapi = pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from nyra.cloud import db as cloud_db

    admin_id, client_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (admin_id, "admin@x.com", client_id, "client@x.com", outsider_id, "out@x.com"),
        )
        cloud_db.add_membership(conn, user_id=admin_id, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=client_id, org_id=cloud_org, role="client")

    app = FastAPI()
    member_dep = auth.require_member(supabase_url=SUPABASE_URL, jwt_secret=SECRET, database_url=cloud_database_url)
    admin_dep = auth.require_admin(supabase_url=SUPABASE_URL, jwt_secret=SECRET, database_url=cloud_database_url)

    @app.get("/api/orgs/{org_id}/whoami")
    def whoami(member=Depends(member_dep)):
        return {"role": member.role}

    @app.get("/api/orgs/{org_id}/admin-only")
    def admin_only(member=Depends(admin_dep)):
        return {"ok": True}

    client = TestClient(app)
    headers = lambda uid: {"Authorization": f"Bearer {_token(uid)}"}  # noqa: E731

    assert client.get(f"/api/orgs/{cloud_org}/whoami", headers=headers(admin_id)).json() == {"role": "admin"}
    assert client.get(f"/api/orgs/{cloud_org}/whoami", headers=headers(client_id)).json() == {"role": "client"}

    outsider_resp = client.get(f"/api/orgs/{cloud_org}/whoami", headers=headers(outsider_id))
    assert outsider_resp.status_code == 403

    no_auth_resp = client.get(f"/api/orgs/{cloud_org}/whoami")
    assert no_auth_resp.status_code == 401

    assert client.get(f"/api/orgs/{cloud_org}/admin-only", headers=headers(admin_id)).status_code == 200
    assert client.get(f"/api/orgs/{cloud_org}/admin-only", headers=headers(client_id)).status_code == 403
