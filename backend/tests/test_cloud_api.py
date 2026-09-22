"""backend/nyra/cloud/api.py end to end: auth, role gating, library
upload, matches (with the "not found" flag), reviews, and report
generation/download — against a real Postgres (TEST_DATABASE_URL) and a
fake in-memory Supabase Storage (`fake_storage_client`), no live Supabase
project or network needed.
"""

from __future__ import annotations

import io
import time
import uuid

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

import jwt as pyjwt
from fastapi.testclient import TestClient

from nyra.cloud import db as cloud_db
from nyra.cloud.api import CloudSettings, create_app

SECRET = "test-secret-at-least-32-bytes-long-enough!!"


def _token(user_id) -> str:
    return pyjwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256"
    )


@pytest.fixture()
def app_and_client(cloud_database_url, fake_storage_client):
    settings = CloudSettings(
        database_url=cloud_database_url, supabase_url="https://x.supabase.co",
        service_role_key="key", jwt_secret=SECRET,
    )
    app = create_app(settings)
    settings.storage_client = lambda: fake_storage_client  # swap in the fake, no network
    return app, TestClient(app)


@pytest.fixture()
def org_with_users(cloud_database_url, cloud_org):
    admin_id, client_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (admin_id, "admin@x.com", client_id, "client@x.com", outsider_id, "out@x.com"),
        )
        cloud_db.add_membership(conn, user_id=admin_id, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=client_id, org_id=cloud_org, role="client")
    return cloud_org, admin_id, client_id, outsider_id


def _upload_bytes() -> io.BytesIO:
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 300))
    draw = ImageDraw.Draw(img)
    draw.ellipse([50, 50, 250, 250], fill=(200, 10, 10))
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_healthz_needs_no_auth(app_and_client):
    _, client = app_and_client
    assert client.get("/api/healthz").json() == {"status": "ok"}


def test_list_orgs_reflects_memberships(app_and_client, org_with_users):
    _, client = app_and_client
    org_id, admin_id, _, _ = org_with_users
    r = client.get("/api/orgs", headers={"Authorization": f"Bearer {_token(admin_id)}"})
    assert r.status_code == 200
    orgs = r.json()["organizations"]
    assert len(orgs) == 1
    assert orgs[0]["role"] == "admin"


def test_upload_requires_admin(app_and_client, org_with_users):
    _, client = app_and_client
    org_id, admin_id, client_id, _ = org_with_users

    admin_headers = {"Authorization": f"Bearer {_token(admin_id)}"}
    client_headers = {"Authorization": f"Bearer {_token(client_id)}"}

    r = client.post(
        f"/api/orgs/{org_id}/library/upload", headers=client_headers,
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))], data={"fast": "true"},
    )
    assert r.status_code == 403

    r = client.post(
        f"/api/orgs/{org_id}/library/upload", headers=admin_headers,
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))], data={"fast": "true"},
    )
    assert r.status_code == 200
    assert r.json()["saved"] == ["a.jpg"]


def test_upload_rejects_bad_files_without_failing_the_batch(app_and_client, org_with_users):
    _, client = app_and_client
    org_id, admin_id, _, _ = org_with_users
    headers = {"Authorization": f"Bearer {_token(admin_id)}"}

    r = client.post(
        f"/api/orgs/{org_id}/library/upload", headers=headers,
        files=[
            ("files", ("good.jpg", _upload_bytes(), "image/jpeg")),
            ("files", ("bad.txt", io.BytesIO(b"not an image"), "text/plain")),
        ],
        data={"fast": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == ["good.jpg"]
    assert len(body["failed"]) == 1


def test_outsider_cannot_read_org(app_and_client, org_with_users):
    _, client = app_and_client
    org_id, _, _, outsider_id = org_with_users
    r = client.get(f"/api/orgs/{org_id}/overview", headers={"Authorization": f"Bearer {_token(outsider_id)}"})
    assert r.status_code == 403


def test_no_auth_header_is_401(app_and_client, org_with_users):
    _, client = app_and_client
    org_id, _, _, _ = org_with_users
    assert client.get(f"/api/orgs/{org_id}/overview").status_code == 401


def test_matches_flags_unmatched_reference_and_client_can_review(
    app_and_client, org_with_users, fake_storage_client, cloud_database_url
):
    _, client = app_and_client
    org_id, admin_id, client_id, _ = org_with_users
    admin_headers = {"Authorization": f"Bearer {_token(admin_id)}"}
    client_headers = {"Authorization": f"Bearer {_token(client_id)}"}

    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        found_ref = cloud_db.upsert_reference_image(
            conn, org_id=org_id, filename="found.jpg", storage_path="refs/found.jpg",
            expiry_date="2026-10-01", credit=None, notes=None, phash=same_hash, dhash=same_hash,
        )
        cloud_db.upsert_reference_image(
            conn, org_id=org_id, filename="missing.jpg", storage_path="refs/missing.jpg",
            expiry_date="2026-10-05", credit=None, notes=None,
            phash="0000ffff0000ffff", dhash="0000ffff0000ffff",
        )
        site_id = cloud_db.upsert_site(conn, org_id=org_id, url="https://target.test")
        site_img_id, _ = cloud_db.upsert_site_image(
            conn, org_id=org_id, site_id=site_id, url="https://target.test/found.jpg",
            phash=same_hash, dhash=same_hash,
        )
        page_id = cloud_db.upsert_page(conn, org_id=org_id, site_id=site_id, url="https://target.test/p1")
        cloud_db.mark_page_crawled(conn, page_id, http_status=200)
        cloud_db.link_image_page(conn, site_img_id, page_id)
        cloud_db.write_matches(conn, org_id, [(found_ref, site_img_id, "phash", 1.0, "haut")])
        cloud_db.stamp_compared(conn, [found_ref], [site_img_id])
        fake_storage_client.store[("refs", "refs/found.jpg")] = _upload_bytes().getvalue()

    r = client.get(f"/api/orgs/{org_id}/matches", headers=client_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data["confirmed"]) == 1
    assert data["confirmed"][0]["filename"] == "found.jpg"
    assert data["confirmed"][0]["ref_image"].startswith("https://fake-storage.test/refs/")
    assert len(data["not_found"]) == 1
    assert data["not_found"][0]["filename"] == "missing.jpg"

    # a client (not just an admin) can record a review decision
    r = client.post(
        f"/api/orgs/{org_id}/reviews", headers=client_headers,
        json={"reference_id": str(found_ref), "site_image_ids": [str(site_img_id)], "decision": "retenu"},
    )
    assert r.status_code == 200

    r = client.get(f"/api/orgs/{org_id}/matches", headers=admin_headers)
    hit = r.json()["confirmed"][0]["hits"][0]
    assert hit["decision"] == "retenu"


def test_report_generation_and_download_redirect(app_and_client, org_with_users, fake_storage_client, cloud_database_url):
    _, client = app_and_client
    org_id, admin_id, _, _ = org_with_users
    headers = {"Authorization": f"Bearer {_token(admin_id)}"}

    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(
            conn, org_id=org_id, filename="a.jpg", storage_path="refs/a.jpg",
            expiry_date="2026-10-01", credit=None, notes=None,
        )

    r = client.post(f"/api/orgs/{org_id}/reports", headers=headers, json={"within_days": 90})
    assert r.status_code == 200
    report_id = r.json()["report_id"]

    r = client.get(f"/api/orgs/{org_id}/reports", headers=headers)
    assert len(r.json()["reports"]) == 1

    r = client.get(f"/api/orgs/{org_id}/reports/{report_id}/report.html", headers=headers, follow_redirects=False)
    assert r.status_code == 307
    assert "fake-storage.test/reports/" in r.headers["location"]


def test_delete_reference_removes_row_and_storage_object(app_and_client, org_with_users, fake_storage_client, cloud_database_url):
    _, client = app_and_client
    org_id, admin_id, _, _ = org_with_users
    headers = {"Authorization": f"Bearer {_token(admin_id)}"}

    r = client.post(
        f"/api/orgs/{org_id}/library/upload", headers=headers,
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))], data={"fast": "true"},
    )
    assert r.json()["saved"] == ["a.jpg"]
    assert ("refs", f"{org_id}/a.jpg") in fake_storage_client.store

    r = client.delete(f"/api/orgs/{org_id}/library/a.jpg", headers=headers)
    assert r.status_code == 200
    assert ("refs", f"{org_id}/a.jpg") not in fake_storage_client.store

    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_db.get_reference_images(conn, org_id) == []
