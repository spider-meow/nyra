"""backend/nyra/cloud/api.py end to end: auth and role gating, brands and
their addresses, library upload (which queues an index job), bulk actions,
CSV import preview, jobs, matches with batch-signed URLs, reviews, reports
and settings — against a real Postgres (TEST_DATABASE_URL) and the
in-memory fake Storage.
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
def org_with_users(cloud_database_url, cloud_org, cloud_brand):
    """(org_id, brand API prefix, admin, client, outsider)."""
    admin_id, client_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (admin_id, f"{admin_id}@x.com", client_id, f"{client_id}@x.com", outsider_id, f"{outsider_id}@x.com"),
        )
        cloud_db.add_membership(conn, user_id=admin_id, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=client_id, org_id=cloud_org, role="client")
    return cloud_org, f"/api/orgs/{cloud_org}/brands/{cloud_brand}", admin_id, client_id, outsider_id


def _upload_bytes() -> io.BytesIO:
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 300))
    ImageDraw.Draw(img).ellipse([50, 50, 250, 250], fill=(200, 10, 10))
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def _site(conn, org_id, brand_id, url="https://target.test/"):
    return cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id, url=url)


def test_healthz_and_auth_config_need_no_session(client):
    assert client.get("/api/healthz").json() == {"status": "ok"}
    assert client.get("/api/auth/config").json()["supabaseUrl"] == "https://x.supabase.co"


def test_there_is_no_public_signup(client):
    assert client.post("/api/signup", json={"email": "a@b.c", "password": "12345678"}).status_code in {404, 405}


def test_list_orgs_includes_slug_role_and_brands(client, org_with_users, cloud_brand):
    org_id, _, admin_id, _, _ = org_with_users
    orgs = client.get("/api/orgs", headers=_headers(admin_id)).json()["organizations"]
    slug = orgs[0]["slug"]
    assert orgs == [{"org_id": str(org_id), "name": "Test Org", "slug": slug, "role": "admin",
                     "brands": [{"id": str(cloud_brand), "name": "Test Org", "slug": slug}]}]


def test_session_membership_and_brand_are_enforced(client, org_with_users, cloud_database_url):
    org_id, base, admin_id, _, outsider_id = org_with_users
    assert client.get(f"{base}/overview").status_code == 401
    assert client.get(f"{base}/overview", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(f"{base}/overview", headers=_headers(outsider_id)).status_code == 403
    assert client.get(f"/api/orgs/{org_id}/brands/{uuid.uuid4()}/overview", headers=_headers(admin_id)).status_code == 404
    # Another organization's brand can't be reached through this organization.
    with cloud_db.connect(cloud_database_url) as conn:
        other_org = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        other_brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (other_org,)).fetchone()["id"]
    assert client.get(f"/api/orgs/{org_id}/brands/{other_brand}/library", headers=_headers(admin_id)).status_code == 404


def test_brands_are_created_renamed_and_admin_only(client, org_with_users):
    org_id, _, admin_id, client_id, _ = org_with_users
    url = f"/api/orgs/{org_id}/brands"
    assert client.post(url, headers=_headers(client_id), json={"name": "Louis XIII"}).status_code == 403
    brand = client.post(url, headers=_headers(admin_id), json={"name": "Louis XIII"}).json()["brand"]
    assert brand["slug"] == "louis-xiii"
    assert client.post(url, headers=_headers(admin_id), json={"name": "Louis  XIII"}).status_code == 409
    renamed = client.put(f"{url}/{brand['id']}", headers=_headers(admin_id), json={"name": "Louis XIII Cognac"}).json()
    assert renamed["brand"] == {"id": brand["id"], "name": "Louis XIII Cognac", "slug": "louis-xiii-cognac"}
    names = [b["name"] for b in client.get("/api/orgs", headers=_headers(client_id)).json()["organizations"][0]["brands"]]
    assert names == ["Louis XIII Cognac", "Test Org"]


def test_addresses_belong_to_one_brand_and_are_checked(client, org_with_users, monkeypatch):
    org_id, base, admin_id, client_id, _ = org_with_users
    from nyra import netguard

    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    body = {"url": "https://www.Louis-XIII.com/fr?utm_source=x", "label": "FR"}
    assert client.post(f"{base}/sites", headers=_headers(client_id), json=body).status_code == 403
    site = client.post(f"{base}/sites", headers=_headers(admin_id), json=body).json()["site"]
    assert site["url"] == "https://www.louis-xiii.com/fr" and site["label"] == "FR" and site["images"] == 0
    assert client.post(f"{base}/sites", headers=_headers(admin_id), json={"url": "ftp://x"}).status_code == 400

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    taken = client.post(f"/api/orgs/{org_id}/brands/{other['id']}/sites", headers=_headers(admin_id), json=body)
    assert taken.status_code == 409

    assert client.put(f"{base}/sites/{site['id']}", headers=_headers(admin_id), json={"label": "France"}).status_code == 200
    listed = client.get(f"{base}/sites", headers=_headers(client_id)).json()["sites"]
    assert [(s["url"], s["label"]) for s in listed] == [("https://www.louis-xiii.com/fr", "France")]
    assert client.delete(f"{base}/sites/{site['id']}", headers=_headers(admin_id)).status_code == 200
    assert client.get(f"{base}/sites", headers=_headers(client_id)).json()["sites"] == []


def test_private_addresses_are_refused_up_front(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    r = client.post(f"{base}/sites", headers=_headers(admin_id), json={"url": "http://169.254.169.254/"})
    assert r.status_code == 400
    assert "refusée" in r.json()["detail"]


def test_deleting_a_brand_removes_only_what_belongs_to_it(client, org_with_users, cloud_brand, cloud_database_url,
                                                          fake_storage_client):
    org_id, base, admin_id, client_id, _ = org_with_users
    brands = f"/api/orgs/{org_id}/brands"
    other = client.post(brands, headers=_headers(admin_id), json={"name": "Louis XIII"}).json()["brand"]
    other_base = f"{brands}/{other['id']}"
    for prefix in (base, other_base):
        client.post(f"{prefix}/library/upload", headers=_headers(admin_id),
                    files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    assert ("refs", f"{org_id}/{other['id']}/a.jpg") in fake_storage_client.store

    assert client.delete(other_base, headers=_headers(client_id)).status_code == 403
    assert client.delete(other_base, headers=_headers(admin_id)).status_code == 409  # its index job is queued
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE brand_id = %s", (other["id"],))
    assert client.delete(other_base, headers=_headers(admin_id)).status_code == 200
    assert client.delete(other_base, headers=_headers(admin_id)).status_code == 404

    assert ("refs", f"{org_id}/{other['id']}/a.jpg") not in fake_storage_client.store
    assert ("refs", f"{org_id}/{cloud_brand}/a.jpg") in fake_storage_client.store
    assert [item["filename"] for item in client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]] == ["a.jpg"]


def test_upload_is_admin_only_stores_a_thumbnail_and_queues_indexing(client, org_with_users, cloud_brand,
                                                                     fake_storage_client):
    org_id, base, admin_id, client_id, _ = org_with_users
    files = [("files", ("a.jpg", _upload_bytes(), "image/jpeg"))]
    assert client.post(f"{base}/library/upload", headers=_headers(client_id), files=files).status_code == 403

    r = client.post(
        f"{base}/library/upload", headers=_headers(admin_id),
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg")),
               ("files", ("bad.jpg", io.BytesIO(b"not an image"), "image/jpeg")),
               ("files", ("notes.txt", io.BytesIO(b"x"), "text/plain"))],
    )
    body = r.json()
    assert body["saved"] == ["a.jpg"]
    assert {item["filename"] for item in body["failed"]} == {"bad.jpg", "notes.txt"}
    assert body["job"]["kind"] == "index" and body["job"]["brand_id"] == str(cloud_brand)
    assert ("refs", f"{org_id}/{cloud_brand}/thumbs/a.jpg.jpg") in fake_storage_client.store
    # A light working copy next to the original, for indexing, the keypoint check and the screens.
    assert ("refs", f"{org_id}/{cloud_brand}/work/a.jpg.jpg") in fake_storage_client.store

    library = client.get(f"{base}/library", headers=_headers(client_id)).json()
    assert library["indexing"] is True
    item = library["items"][0]
    assert item["indexed"] is False
    assert item["thumb_url"].startswith(f"https://fake-storage.test/refs/{org_id}/{cloud_brand}/thumbs/")


def test_accented_names_get_a_safe_storage_key_and_one_bad_file_spares_the_others(
    client, org_with_users, cloud_brand, fake_storage_client, monkeypatch
):
    org_id, base, admin_id, client_id, _ = org_with_users
    from nyra.cloud import storage as cloud_storage

    real_upload = cloud_storage.upload

    def flaky(client_, bucket, path, data, *, content_type=None):
        if "casse" in path:
            raise RuntimeError("Invalid key")
        real_upload(client_, bucket, path, data, content_type=content_type)

    monkeypatch.setattr(cloud_storage, "upload", flaky)
    r = client.post(
        f"{base}/library/upload", headers=_headers(admin_id),
        files=[("files", ("Été n°1 [final].jpg", _upload_bytes(), "image/jpeg")),
               ("files", ("casse.jpg", _upload_bytes(), "image/jpeg"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == ["Été n°1 [final].jpg"]
    assert body["failed"] == [{"filename": "casse.jpg", "reason": "Erreur inattendue (RuntimeError) : Invalid key"}]
    keys = [path for bucket, path in fake_storage_client.store if bucket == "refs"]
    assert all(key.isascii() and "[" not in key for key in keys)
    items = client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]
    assert [item["filename"] for item in items] == ["Été n°1 [final].jpg"]


def test_upload_says_which_files_replaced_a_visual_of_the_same_name(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    first = client.post(f"{base}/library/upload", headers=_headers(admin_id), files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    assert first.json()["replaced"] == []
    again = client.post(
        f"{base}/library/upload", headers=_headers(admin_id),
        files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg")), ("files", ("b.jpg", _upload_bytes(), "image/jpeg"))],
    ).json()
    assert again["saved"] == ["a.jpg", "b.jpg"] and again["replaced"] == ["a.jpg"]


def test_reupload_moves_a_reference_stored_before_brands(client, org_with_users, cloud_brand, cloud_database_url,
                                                         fake_storage_client):
    org_id, base, admin_id, _, _ = org_with_users
    legacy = f"{org_id}/old.jpg"
    fake_storage_client.store[("refs", legacy)] = b"old"
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="old.jpg", storage_path=legacy,
                                        expiry_date="2027-01-01", credit="Studio", notes=None)
    client.post(f"{base}/library/upload", headers=_headers(admin_id), files=[("files", ("old.jpg", _upload_bytes(), "image/jpeg"))])
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.get_reference_by_filename(conn, cloud_brand, "old.jpg")
    assert ref["storage_path"] == f"{org_id}/{cloud_brand}/old.jpg"
    assert ref["credit"] == "Studio" and ref["expiry_date"].isoformat() == "2027-01-01"
    assert ("refs", legacy) not in fake_storage_client.store


def test_meta_update_validates_dates(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    client.post(f"{base}/library/upload", headers=_headers(admin_id), files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    url = f"{base}/library/a.jpg"
    assert client.put(url, headers=_headers(admin_id), json={"expiry_date": "31/12/2026"}).json()["expiry_date"] == "2026-12-31"
    assert client.put(url, headers=_headers(admin_id), json={"expiry_date": "2026-13-45"}).status_code == 400
    assert client.put(f"{base}/library/none.jpg", headers=_headers(admin_id), json={}).status_code == 404


def test_csv_import_previews_before_applying(client, org_with_users, cloud_brand, cloud_database_url):
    _, base, admin_id, _, _ = org_with_users
    client.post(f"{base}/library/upload", headers=_headers(admin_id), files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    csv_bytes = "filename;expiry_date;credit\na.jpg;01/02/2027;Studio\nb.jpg;2027-01-01;\na.jpg;13/13/2027;\n".encode()
    url = f"{base}/library/import-csv"
    preview = client.post(url, headers=_headers(admin_id), files={"file": ("m.csv", csv_bytes, "text/csv")}).json()
    assert [row["status"] for row in preview["rows"]] == ["ok", "unknown_file", "bad_date"]
    assert preview["rows"][0]["expiry_date"] == "2027-02-01"
    assert preview["applied"] == 0
    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_db.get_reference_by_filename(conn, cloud_brand, "a.jpg")["expiry_date"] is None

    applied = client.post(url + "?apply=true", headers=_headers(admin_id), files={"file": ("m.csv", csv_bytes, "text/csv")}).json()
    assert applied["applied"] == 1
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.get_reference_by_filename(conn, cloud_brand, "a.jpg")
    assert ref["expiry_date"].isoformat() == "2027-02-01" and ref["credit"] == "Studio"


def test_bulk_expiry_and_bulk_delete(client, org_with_users, cloud_brand, fake_storage_client):
    org_id, base, admin_id, _, _ = org_with_users
    client.post(f"{base}/library/upload", headers=_headers(admin_id),
                files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg")), ("files", ("b.jpg", _upload_bytes(), "image/jpeg"))])
    r = client.post(f"{base}/library/expiry", headers=_headers(admin_id),
                    json={"filenames": ["a.jpg", "b.jpg"], "expiry_date": "2027-06-30"})
    assert r.json()["updated"] == 2
    r = client.post(f"{base}/library/delete", headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg"]})
    assert r.json()["deleted"] == 2
    assert ("refs", f"{org_id}/{cloud_brand}/a.jpg") not in fake_storage_client.store
    assert ("refs", f"{org_id}/{cloud_brand}/thumbs/a.jpg.jpg") not in fake_storage_client.store


def test_crawl_is_queued_capped_deduplicated_and_cancellable(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    body = {"site_ids": [], "max_pages": 999999}
    no_site = client.post(f"{base}/jobs/crawl", headers=_headers(admin_id), json=body)
    assert no_site.status_code == 400
    with cloud_db.connect(cloud_database_url) as conn:
        fr = _site(conn, org_id, cloud_brand, "https://t.test/fr")
        _site(conn, org_id, cloud_brand, "https://t.test/uk")
    assert client.post(f"{base}/jobs/crawl", headers=_headers(admin_id),
                       json={"site_ids": [str(uuid.uuid4())]}).status_code == 404

    body = {"site_ids": [str(fr)], "max_pages": 999999}
    assert client.post(f"{base}/jobs/crawl", headers=_headers(client_id), json=body).status_code == 403
    job = client.post(f"{base}/jobs/crawl", headers=_headers(admin_id), json=body).json()["job"]
    assert job["status"] == "queued" and job["params"]["max_pages"] == 2000
    assert job["params"]["site_ids"] == [str(fr)]
    assert client.post(f"{base}/jobs/crawl", headers=_headers(admin_id), json=body).status_code == 409

    current = client.get(f"{base}/jobs/current", headers=_headers(client_id)).json()
    assert [item["id"] for item in current["active"]] == [job["id"]]
    cancelled = client.post(f"{base}/jobs/{job['id']}/cancel", headers=_headers(admin_id)).json()["job"]
    assert cancelled["status"] == "cancelled"


def test_each_brand_has_its_own_queue(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.post(f"{base}/jobs/match", headers=_headers(admin_id)).status_code == 200
    other_job = client.post(f"/api/orgs/{org_id}/brands/{other['id']}/jobs/match", headers=_headers(admin_id))
    assert other_job.status_code == 200
    # A brand can't cancel another brand's job.
    r = client.post(f"{base}/jobs/{other_job.json()['job']['id']}/cancel", headers=_headers(admin_id))
    assert r.status_code == 404


def test_matches_sign_urls_in_batch_and_reviews_are_scoped(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        found = cloud_db.upsert_reference_image(
            conn, org_id=org_id, brand_id=cloud_brand, filename="found.jpg", storage_path=f"{org_id}/found.jpg",
            thumb_path=f"{org_id}/thumbs/found.jpg.jpg", expiry_date="2026-10-01", credit=None, notes=None,
            phash=same_hash, dhash=same_hash,
        )
        cloud_db.upsert_reference_image(
            conn, org_id=org_id, brand_id=cloud_brand, filename="missing.jpg", storage_path=f"{org_id}/missing.jpg",
            expiry_date="2026-10-05", credit=None, notes=None, phash="0000ffff0000ffff", dhash="0000ffff0000ffff",
        )
        site = _site(conn, org_id, cloud_brand)
        image = conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, phash, dhash)
               VALUES (%s, %s, 'https://target.test/found.jpg', %s, %s, %s, %s) RETURNING id""",
            (org_id, site, f"{org_id}/abc.jpg", f"{org_id}/thumbs/abc.jpg", same_hash, same_hash),
        ).fetchone()["id"]
        cloud_db.write_matches(conn, org_id, [(found, image, "phash", 1.0, "haut")])
        cloud_db.stamp_compared(conn, [found], [image])

    data = client.get(f"{base}/matches?within_days=3650", headers=_headers(client_id)).json()
    group = data["confirmed"][0]
    assert group["filename"] == "found.jpg"
    assert group["ref_thumb"].startswith(f"https://fake-storage.test/refs/{org_id}/thumbs/")
    assert group["hits"][0]["site_thumb"].startswith(f"https://fake-storage.test/site-images/{org_id}/thumbs/")
    assert [item["filename"] for item in data["not_found"]] == ["missing.jpg"]

    review = {"reference_id": str(found), "site_image_ids": [str(image)], "decision": "retenu"}
    assert client.post(f"{base}/reviews", headers=_headers(client_id), json=review).status_code == 200
    hit = client.get(f"{base}/matches?within_days=3650", headers=_headers(admin_id)).json()["confirmed"][0]["hits"][0]
    assert hit["decision"] == "retenu"
    bogus = {**review, "site_image_ids": [str(uuid.uuid4())]}
    assert client.post(f"{base}/reviews", headers=_headers(client_id), json=bogus).status_code == 404

    # The organization's other brand sees none of it.
    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    other_base = f"/api/orgs/{org_id}/brands/{other['id']}"
    empty = client.get(f"{other_base}/matches?within_days=3650", headers=_headers(admin_id)).json()
    assert empty["confirmed"] == [] and empty["not_found"] == []
    assert client.post(f"{other_base}/reviews", headers=_headers(admin_id), json=review).status_code == 404


def test_reports_are_queued_and_listed_with_signed_links(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    r = client.post(f"{base}/reports", headers=_headers(admin_id), json={"within_days": 90})
    assert r.json()["job"]["kind"] == "report"
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.create_report(conn, org_id=org_id, brand_id=cloud_brand, within_days=90,
                               storage_path_html=f"{org_id}/r/report.html", storage_path_csv=f"{org_id}/r/matches.csv",
                               storage_path_not_found_csv=f"{org_id}/r/not_found.csv",
                               stats={"expired_online": 2}, generated_by=admin_id)
    reports = client.get(f"{base}/reports", headers=_headers(client_id)).json()["reports"]
    assert reports[0]["files"]["report.html"].startswith("https://fake-storage.test/reports/")
    assert reports[0]["stats"]["expired_online"] == 2


def test_settings_are_per_organization_validated_and_admin_only(client, org_with_users):
    org_id, _, admin_id, client_id, _ = org_with_users
    url = f"/api/orgs/{org_id}/settings"
    assert client.put(url, headers=_headers(client_id), json={"overrides": {}}).status_code == 403
    bad = {"overrides": {"match": {"clip_similarity_floor": 0.99}}}
    assert client.put(url, headers=_headers(admin_id), json=bad).status_code == 400
    good = {"overrides": {"match": {"phash_threshold": 6}, "crawl": {"pre_actions": ["#age-yes"]}}}
    assert client.put(url, headers=_headers(admin_id), json=good).status_code == 200
    effective = client.get(url, headers=_headers(client_id)).json()["effective"]
    assert effective["match"]["phash_threshold"] == 6
    assert effective["crawl"]["pre_actions"] == ["#age-yes"]


def test_overview_leads_with_the_dashboard(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.enqueue(conn, org_id=org_id, brand_id=cloud_brand, kind="match")
        _site(conn, org_id, cloud_brand)
    data = client.get(f"{base}/overview", headers=_headers(admin_id)).json()
    assert data["role"] == "admin"
    assert data["brand"] == {"id": str(cloud_brand), "name": "Test Org"}
    assert set(data["dashboard"]) >= {"expired_online", "urgent_online", "pending_review", "upcoming"}
    assert data["jobs"]["active"][0]["kind"] == "match"
    assert [site["url"] for site in data["sites"]] == ["https://target.test/"]
    assert data["defaults"]["max_pages_limit"] == 2000


def test_client_routes_get_the_app_shell_and_security_headers(client):
    r = client.get("/o/some-org/m/some-brand/bibliotheque")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/does-not-exist").status_code == 404


def test_exclusions_purge_matches_and_can_be_undone(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    logo = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="logo.png",
                                              storage_path=f"{org_id}/logo.png", expiry_date=None, credit=None,
                                              notes=None, phash=logo, dhash=logo)
        site = _site(conn, org_id, cloud_brand, "https://t.test/")
        images = [
            conn.execute(
                "INSERT INTO site_images (org_id, site_id, url, phash, dhash) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (org_id, site, url, phash, phash),
            ).fetchone()["id"]
            for url, phash in (("https://t.test/logo.png", logo), ("https://t.test/logo@2x.png", "ffff0000ffff0001"))
        ]
        cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut") for image in images])

    body = {"site_image_id": str(images[0]), "reason": "Logo du site"}
    assert client.post(f"{base}/exclusions", headers=_headers(client_id), json=body).status_code == 403
    r = client.post(f"{base}/exclusions", headers=_headers(admin_id), json=body)
    assert r.json()["matches_removed"] == 2  # the near copy goes too

    listed = client.get(f"{base}/exclusions", headers=_headers(client_id)).json()["exclusions"]
    assert [item["reason"] for item in listed] == ["Logo du site"]

    # Exclusions are per brand: another brand of the organization neither sees nor can use this image.
    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    other_base = f"/api/orgs/{org_id}/brands/{other['id']}"
    assert client.get(f"{other_base}/exclusions", headers=_headers(admin_id)).json()["exclusions"] == []
    assert client.post(f"{other_base}/exclusions", headers=_headers(admin_id), json=body).status_code == 404

    r = client.delete(f"{base}/exclusions/{listed[0]['id']}", headers=_headers(admin_id))
    assert r.status_code == 200 and r.json()["job"]["kind"] == "match"
    assert client.get(f"{base}/exclusions", headers=_headers(admin_id)).json()["exclusions"] == []


def test_site_images_without_a_reference_are_listed_and_can_join_the_library(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    org_id, base, admin_id, client_id, _ = org_with_users
    data = _upload_bytes().getvalue()
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand, "https://t.test/fr/")
        images = {}
        for url, content_hash, phash in (
            ("https://cdn.t.test/Campagne%20%C3%A9t%C3%A9.jpg?w=600", "same", "ffff0000ffff0000"),
            ("https://cdn.t.test/Campagne%20%C3%A9t%C3%A9.jpg?w=1200", "same", "ffff0000ffff0000"),  # same bytes
            ("https://t.test/logo.png", "logo", "0f0f0f0f0f0f0f0f"),
        ):
            path = f"{org_id}/{content_hash}.jpg"
            fake_storage_client.store[("site-images", path)] = data
            images[url] = conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, url, path, content_hash, phash, phash),
            ).fetchone()["id"]

    items = client.get(f"{base}/site-images", headers=_headers(client_id)).json()["items"]
    assert len(items) == 2  # the two URLs of the same image count once
    campaign = next(item for item in items if item["url_count"] == 2)
    assert campaign["filename"] == "Campagne été.jpg" and campaign["sites"] == ["t.test"]
    assert client.get(f"{base}/overview", headers=_headers(client_id)).json()["dashboard"]["unreferenced_online"] == 2

    # A logo set aside leaves the list.
    logo = str(images["https://t.test/logo.png"])
    client.post(f"{base}/exclusions", headers=_headers(admin_id), json={"site_image_id": logo, "reason": "Logo"})
    assert [item["url_count"] for item in client.get(f"{base}/site-images", headers=_headers(admin_id)).json()["items"]] == [2]

    body = {"site_image_ids": [campaign["id"]], "expiry_date": "2020-01-01", "credit": "Studio"}
    assert client.post(f"{base}/site-images/adopt", headers=_headers(client_id), json=body).status_code == 403
    adopted = client.post(f"{base}/site-images/adopt", headers=_headers(admin_id), json=body).json()
    assert adopted["added"] == [{"site_image_id": campaign["id"], "filename": "Campagne été.jpg"}]
    assert adopted["failed"] == [] and adopted["job"]["kind"] == "index"

    # It left this list and is now an expired visual online, both URLs linked.
    assert client.get(f"{base}/site-images", headers=_headers(admin_id)).json()["items"] == []
    matches = client.get(f"{base}/matches?within_days=3650", headers=_headers(admin_id)).json()
    group = matches["confirmed"][0]
    assert group["filename"] == "Campagne été.jpg" and group["credit"] == "Studio"
    assert sum(len(hit["site_image_ids"]) for hit in group["hits"]) == 2
    dashboard = client.get(f"{base}/overview", headers=_headers(admin_id)).json()["dashboard"]
    assert dashboard["expired_online"] == 1 and dashboard["unreferenced_online"] == 0

    # Adding the same image again gets its own name instead of replacing the first visual.
    again = client.post(f"{base}/site-images/adopt", headers=_headers(admin_id), json={"site_image_ids": [campaign["id"]]}).json()
    assert again["added"][0]["filename"] == "Campagne été (2).jpg"
