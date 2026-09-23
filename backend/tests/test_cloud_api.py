"""backend/nyra/cloud/api.py end to end: auth and role gating, library upload
(which queues an index job), bulk actions, CSV import preview, jobs,
matches with batch-signed URLs, reviews, reports and settings — against a
real Postgres (TEST_DATABASE_URL) and the in-memory fake Storage.
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
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud.api import CloudSettings, create_app

SECRET = "test-secret-at-least-32-bytes-long-enough!!"


def _token(user_id) -> str:
    return pyjwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256"
    )


def _headers(user_id) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture()
def client(cloud_database_url, fake_storage_client):
    settings = CloudSettings(
        database_url=cloud_database_url, supabase_url="https://x.supabase.co",
        service_role_key="key", jwt_secret=SECRET,
    )
    settings.storage_client = lambda: fake_storage_client  # no network
    return TestClient(create_app(settings))


@pytest.fixture()
def org_with_users(cloud_database_url, cloud_org):
    admin_id, client_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (admin_id, f"{admin_id}@x.com", client_id, f"{client_id}@x.com", outsider_id, f"{outsider_id}@x.com"),
        )
        cloud_db.add_membership(conn, user_id=admin_id, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=client_id, org_id=cloud_org, role="client")
    return cloud_org, admin_id, client_id, outsider_id


def _upload_bytes() -> io.BytesIO:
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 300))
    ImageDraw.Draw(img).ellipse([50, 50, 250, 250], fill=(200, 10, 10))
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_healthz_and_auth_config_need_no_session(client):
    assert client.get("/api/healthz").json() == {"status": "ok"}
    assert client.get("/api/auth/config").json()["supabaseUrl"] == "https://x.supabase.co"


def test_there_is_no_public_signup(client):
    assert client.post("/api/signup", json={"email": "a@b.c", "password": "12345678"}).status_code in {404, 405}


def test_list_orgs_includes_slug_and_role(client, org_with_users):
    org_id, admin_id, _, _ = org_with_users
    orgs = client.get("/api/orgs", headers=_headers(admin_id)).json()["organizations"]
    assert orgs == [{"org_id": str(org_id), "name": "Test Org", "slug": orgs[0]["slug"], "role": "admin"}]


def test_session_and_membership_are_enforced(client, org_with_users):
    org_id, _, _, outsider_id = org_with_users
    assert client.get(f"/api/orgs/{org_id}/overview").status_code == 401
    assert client.get(f"/api/orgs/{org_id}/overview", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(f"/api/orgs/{org_id}/overview", headers=_headers(outsider_id)).status_code == 403


def test_upload_is_admin_only_stores_a_thumbnail_and_queues_indexing(client, org_with_users, fake_storage_client):
    org_id, admin_id, client_id, _ = org_with_users
    files = [("files", ("a.jpg", _upload_bytes(), "image/jpeg"))]
    assert client.post(f"/api/orgs/{org_id}/library/upload", headers=_headers(client_id), files=files).status_code == 403

    r = client.post(
        f"/api/orgs/{org_id}/library/upload", headers=_headers(admin_id),
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg")),
               ("files", ("bad.jpg", io.BytesIO(b"not an image"), "image/jpeg")),
               ("files", ("notes.txt", io.BytesIO(b"x"), "text/plain"))],
    )
    body = r.json()
    assert body["saved"] == ["a.jpg"]
    assert {item["filename"] for item in body["failed"]} == {"bad.jpg", "notes.txt"}
    assert body["job"]["kind"] == "index"
    assert ("refs", f"{org_id}/thumbs/a.jpg.jpg") in fake_storage_client.store

    library = client.get(f"/api/orgs/{org_id}/library", headers=_headers(client_id)).json()
    assert library["indexing"] is True
    item = library["items"][0]
    assert item["indexed"] is False
    assert item["thumb_url"].startswith(f"https://fake-storage.test/refs/{org_id}/thumbs/")


def test_meta_update_validates_dates(client, org_with_users, cloud_database_url):
    org_id, admin_id, _, _ = org_with_users
    client.post(f"/api/orgs/{org_id}/library/upload", headers=_headers(admin_id),
                files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    url = f"/api/orgs/{org_id}/library/a.jpg"
    assert client.put(url, headers=_headers(admin_id), json={"expiry_date": "31/12/2026"}).json()["expiry_date"] == "2026-12-31"
    assert client.put(url, headers=_headers(admin_id), json={"expiry_date": "2026-13-45"}).status_code == 400
    assert client.put(f"/api/orgs/{org_id}/library/none.jpg", headers=_headers(admin_id), json={}).status_code == 404


def test_csv_import_previews_before_applying(client, org_with_users, cloud_database_url):
    org_id, admin_id, _, _ = org_with_users
    client.post(f"/api/orgs/{org_id}/library/upload", headers=_headers(admin_id),
                files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    csv_bytes = "filename;expiry_date;credit\na.jpg;01/02/2027;Studio\nb.jpg;2027-01-01;\na.jpg;13/13/2027;\n".encode()
    url = f"/api/orgs/{org_id}/library/import-csv"
    preview = client.post(url, headers=_headers(admin_id), files={"file": ("m.csv", csv_bytes, "text/csv")}).json()
    assert [row["status"] for row in preview["rows"]] == ["ok", "unknown_file", "bad_date"]
    assert preview["rows"][0]["expiry_date"] == "2027-02-01"
    assert preview["applied"] == 0
    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_db.get_reference_by_filename(conn, org_id, "a.jpg")["expiry_date"] is None

    applied = client.post(url + "?apply=true", headers=_headers(admin_id), files={"file": ("m.csv", csv_bytes, "text/csv")}).json()
    assert applied["applied"] == 1
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.get_reference_by_filename(conn, org_id, "a.jpg")
    assert ref["expiry_date"].isoformat() == "2027-02-01" and ref["credit"] == "Studio"


def test_bulk_expiry_and_bulk_delete(client, org_with_users, fake_storage_client, cloud_database_url):
    org_id, admin_id, _, _ = org_with_users
    client.post(f"/api/orgs/{org_id}/library/upload", headers=_headers(admin_id),
                files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg")), ("files", ("b.jpg", _upload_bytes(), "image/jpeg"))])
    r = client.post(f"/api/orgs/{org_id}/library/expiry", headers=_headers(admin_id),
                    json={"filenames": ["a.jpg", "b.jpg"], "expiry_date": "2027-06-30"})
    assert r.json()["updated"] == 2
    r = client.post(f"/api/orgs/{org_id}/library/delete", headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg"]})
    assert r.json()["deleted"] == 2
    assert ("refs", f"{org_id}/a.jpg") not in fake_storage_client.store
    assert ("refs", f"{org_id}/thumbs/a.jpg.jpg") not in fake_storage_client.store


def test_crawl_is_queued_capped_deduplicated_and_cancellable(client, org_with_users, cloud_database_url, monkeypatch):
    org_id, admin_id, client_id, _ = org_with_users
    from nyra import netguard

    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    body = {"site": "https://example.com", "max_pages": 999999}
    assert client.post(f"/api/orgs/{org_id}/jobs/crawl", headers=_headers(client_id), json=body).status_code == 403
    job = client.post(f"/api/orgs/{org_id}/jobs/crawl", headers=_headers(admin_id), json=body).json()["job"]
    assert job["status"] == "queued" and job["params"]["max_pages"] == 2000
    assert client.post(f"/api/orgs/{org_id}/jobs/crawl", headers=_headers(admin_id), json=body).status_code == 409

    current = client.get(f"/api/orgs/{org_id}/jobs/current", headers=_headers(client_id)).json()
    assert [item["id"] for item in current["active"]] == [job["id"]]
    cancelled = client.post(f"/api/orgs/{org_id}/jobs/{job['id']}/cancel", headers=_headers(admin_id)).json()["job"]
    assert cancelled["status"] == "cancelled"


def test_crawl_of_a_private_address_is_refused_up_front(client, org_with_users):
    org_id, admin_id, _, _ = org_with_users
    r = client.post(f"/api/orgs/{org_id}/jobs/crawl", headers=_headers(admin_id), json={"site": "http://169.254.169.254/"})
    assert r.status_code == 400
    assert "refusée" in r.json()["detail"]


def test_matches_sign_urls_in_batch_and_reviews_are_scoped(client, org_with_users, cloud_database_url):
    org_id, admin_id, client_id, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        found = cloud_db.upsert_reference_image(
            conn, org_id=org_id, filename="found.jpg", storage_path=f"{org_id}/found.jpg", thumb_path=f"{org_id}/thumbs/found.jpg.jpg",
            expiry_date="2026-10-01", credit=None, notes=None, phash=same_hash, dhash=same_hash,
        )
        cloud_db.upsert_reference_image(
            conn, org_id=org_id, filename="missing.jpg", storage_path=f"{org_id}/missing.jpg",
            expiry_date="2026-10-05", credit=None, notes=None, phash="0000ffff0000ffff", dhash="0000ffff0000ffff",
        )
        site = cloud_db.upsert_site(conn, org_id=org_id, url="https://target.test/")
        image = conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, phash, dhash)
               VALUES (%s, %s, 'https://target.test/found.jpg', %s, %s, %s, %s) RETURNING id""",
            (org_id, site, f"{org_id}/abc.jpg", f"{org_id}/thumbs/abc.jpg", same_hash, same_hash),
        ).fetchone()["id"]
        cloud_db.write_matches(conn, org_id, [(found, image, "phash", 1.0, "haut")])
        cloud_db.stamp_compared(conn, [found], [image])

    data = client.get(f"/api/orgs/{org_id}/matches?within_days=3650", headers=_headers(client_id)).json()
    group = data["confirmed"][0]
    assert group["filename"] == "found.jpg"
    assert group["ref_thumb"].startswith(f"https://fake-storage.test/refs/{org_id}/thumbs/")
    assert group["hits"][0]["site_thumb"].startswith(f"https://fake-storage.test/site-images/{org_id}/thumbs/")
    assert [item["filename"] for item in data["not_found"]] == ["missing.jpg"]

    review = {"reference_id": str(found), "site_image_ids": [str(image)], "decision": "retenu"}
    assert client.post(f"/api/orgs/{org_id}/reviews", headers=_headers(client_id), json=review).status_code == 200
    hit = client.get(f"/api/orgs/{org_id}/matches?within_days=3650", headers=_headers(admin_id)).json()["confirmed"][0]["hits"][0]
    assert hit["decision"] == "retenu"
    bogus = {**review, "site_image_ids": [str(uuid.uuid4())]}
    assert client.post(f"/api/orgs/{org_id}/reviews", headers=_headers(client_id), json=bogus).status_code == 404


def test_reports_are_queued_and_listed_with_signed_links(client, org_with_users, cloud_database_url):
    org_id, admin_id, client_id, _ = org_with_users
    r = client.post(f"/api/orgs/{org_id}/reports", headers=_headers(admin_id), json={"within_days": 90})
    assert r.json()["job"]["kind"] == "report"
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.create_report(conn, org_id=org_id, within_days=90, storage_path_html=f"{org_id}/r/report.html",
                               storage_path_csv=f"{org_id}/r/matches.csv", storage_path_not_found_csv=f"{org_id}/r/not_found.csv",
                               stats={"expired_online": 2}, generated_by=admin_id)
    reports = client.get(f"/api/orgs/{org_id}/reports", headers=_headers(client_id)).json()["reports"]
    assert reports[0]["files"]["report.html"].startswith("https://fake-storage.test/reports/")
    assert reports[0]["stats"]["expired_online"] == 2


def test_settings_are_validated_and_admin_only(client, org_with_users):
    org_id, admin_id, client_id, _ = org_with_users
    url = f"/api/orgs/{org_id}/settings"
    assert client.put(url, headers=_headers(client_id), json={"overrides": {}}).status_code == 403
    bad = {"overrides": {"match": {"clip_similarity_floor": 0.99}}}
    assert client.put(url, headers=_headers(admin_id), json=bad).status_code == 400
    good = {"overrides": {"match": {"phash_threshold": 6}, "crawl": {"pre_actions": ["#age-yes"]}}}
    assert client.put(url, headers=_headers(admin_id), json=good).status_code == 200
    effective = client.get(url, headers=_headers(client_id)).json()["effective"]
    assert effective["match"]["phash_threshold"] == 6
    assert effective["crawl"]["pre_actions"] == ["#age-yes"]


def test_overview_leads_with_the_dashboard(client, org_with_users, cloud_database_url):
    org_id, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.enqueue(conn, org_id=org_id, kind="match")
    data = client.get(f"/api/orgs/{org_id}/overview", headers=_headers(admin_id)).json()
    assert data["role"] == "admin"
    assert set(data["dashboard"]) >= {"expired_online", "urgent_online", "pending_review", "upcoming"}
    assert data["jobs"]["active"][0]["kind"] == "match"
    assert data["defaults"]["max_pages_limit"] == 2000


def test_client_routes_get_the_app_shell_and_security_headers(client):
    r = client.get("/o/some-org/bibliotheque")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/does-not-exist").status_code == 404
