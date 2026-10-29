"""backend/nyra/cloud/api.py end to end: auth and role gating, brands and
their addresses, library upload (which queues an index job), bulk actions,
CSV import preview, jobs, matches with batch-signed URLs, reviews, reports
and settings — against a real Postgres (TEST_DATABASE_URL) and the
in-memory fake Storage.
"""

from __future__ import annotations

import base64
import csv
import io
import itertools
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

import jwt as pyjwt
from conftest import SECRET
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nyra.cloud import db as cloud_db
from nyra.cloud import hostmetrics
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud import storage as cloud_storage
from nyra.cloud.api import CloudSettings, create_app


def _token(user_id) -> str:
    return pyjwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256"
    )


def _headers(user_id) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


@pytest.fixture()
def org_with_users(cloud_database_url, cloud_org, cloud_brand):
    """(org_id, brand API prefix, admin, client, outsider). The admin is also a platform admin (creates brands)."""
    admin_id, client_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s), (%s, %s)",
            (admin_id, f"{admin_id}@x.com", client_id, f"{client_id}@x.com", outsider_id, f"{outsider_id}@x.com"),
        )
        cloud_db.add_membership(conn, user_id=admin_id, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=client_id, org_id=cloud_org, role="client")
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (admin_id,))
    yield cloud_org, f"/api/orgs/{cloud_org}/brands/{cloud_brand}", admin_id, client_id, outsider_id
    # platform_staff is shared by every test: a row left behind shows up in the next test's list of platform admins.
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM platform_staff WHERE user_id = ANY(%s)", ([admin_id, client_id, outsider_id],))


_uploads = itertools.count(1)


def _upload_bytes() -> io.BytesIO:
    """A new picture at each call (the ellipse starts further right): the library refuses the same bytes twice."""
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 300))
    ImageDraw.Draw(img).ellipse([50 + next(_uploads) % 40, 50, 250, 250], fill=(200, 10, 10))
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def _site(conn, org_id, brand_id, url="https://target.test/"):
    return cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id, url=url)


def test_healthz_and_auth_config_need_no_session(client):
    assert client.get("/api/healthz").json() == {"status": "ok"}
    assert client.get("/api/auth/config").json()["supabaseUrl"] == "https://x.supabase.co"


def _worker_publishes(conn, instance="w1", memory=1 << 30):
    """A worker that has just published its reading (a 2.5 GiB limit, as in docker-compose)."""
    hostmetrics.publish(conn, instance, "worker", {"cpu_percent": 1.0, "cpu_limit": 1.5, "memory": memory, "memory_limit": 2560 << 20,
                                                   "host": {"cpus": 2, "load": [0, 0, 0], "memory_total": 4 << 30, "memory_available": 2 << 30,
                                                            "disk_total": 100, "disk_used": 10}})


def test_workerz_says_503_when_a_job_has_waited_for_a_worker_that_is_not_there(client, cloud_org, cloud_brand,
                                                                               cloud_database_url, monkeypatch):
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.1)
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        _worker_publishes(conn)
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    assert client.get("/api/workerz").status_code == 200  # just queued: a worker may be about to take it
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET created_at = now() - interval '10 minutes' WHERE id = %s", (job["id"],))
    answer = client.get("/api/workerz")
    assert answer.status_code == 503 and answer.json()["status"] == "no worker" and answer.json()["problems"] == ["no worker"]
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.claim(conn)
    assert client.get("/api/workerz").status_code == 200


def test_workerz_waits_five_minutes_of_silence_and_ignores_an_environment_without_worker(client, cloud_database_url, monkeypatch):
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.1)
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        conn.execute("DELETE FROM service_metrics")
    answer = client.get("/api/workerz")  # dev, CI, a fresh table: no worker ever published, nothing to page about
    assert answer.status_code == 200 and [a["code"] for a in answer.json()["alerts"]] == ["worker_absent"]
    with cloud_db.connect(cloud_database_url) as conn:
        _worker_publishes(conn)
        conn.execute("UPDATE service_metrics SET sampled_at = now() - interval '2 minutes'")  # a deployment in progress
    assert client.get("/api/workerz").status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE service_metrics SET sampled_at = now() - interval '6 minutes'")
    answer = client.get("/api/workerz")
    assert answer.status_code == 503 and answer.json()["status"] == "no worker" and answer.json()["problems"] == ["no worker"]
    assert [(a["code"], a["severity"]) for a in answer.json()["alerts"]] == [("worker_absent", "critical")]


def test_workerz_says_503_when_the_disk_is_eighty_percent_full(client, cloud_database_url, monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        _worker_publishes(conn)
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.79)
    assert client.get("/api/workerz").status_code == 200
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.8)
    answer = client.get("/api/workerz")
    assert answer.status_code == 503 and answer.json()["status"] == "disk almost full" and answer.json()["disk_percent"] == 80
    assert answer.json()["problems"] == ["disk almost full"]


def test_a_worker_near_its_memory_limit_is_a_warning_the_monitor_does_not_page_for(client, cloud_database_url, monkeypatch):
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.1)
    with cloud_db.connect(cloud_database_url) as conn:
        _worker_publishes(conn, memory=2400 << 20)
    answer = client.get("/api/workerz")
    assert answer.status_code == 200 and answer.json()["problems"] == []
    assert [(a["code"], a["severity"]) for a in answer.json()["alerts"]] == [("worker_memory", "warning")]
    assert "instance" not in answer.json()["alerts"][0] and "w1" not in answer.text  # no host name on the public probe


def test_workerz_reports_both_problems_and_survives_an_unreadable_disk(client, cloud_org, cloud_brand, cloud_database_url,
                                                                       monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        _worker_publishes(conn)
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
        conn.execute("UPDATE jobs SET created_at = now() - interval '10 minutes' WHERE id = %s", (job["id"],))
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.9)
    answer = client.get("/api/workerz")
    assert answer.status_code == 503 and answer.json()["problems"] == ["no worker", "disk almost full"]
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: (_ for _ in ()).throw(OSError("no disk")))
    answer = client.get("/api/workerz")
    assert answer.status_code == 503 and answer.json()["problems"] == ["no worker"] and answer.json()["disk_percent"] is None


def test_the_serveur_page_gets_the_alerts_and_only_platform_admins_may_read_them(client, org_with_users, cloud_database_url, monkeypatch):
    _, _, admin_id, client_id, _ = org_with_users  # admin_id is a platform admin
    monkeypatch.setattr(hostmetrics, "disk_ratio", lambda: 0.9)
    monkeypatch.setattr(hostmetrics.os, "getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)  # absent on Windows
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM service_metrics")
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
    assert client.get("/api/staff/server").status_code == 401
    assert client.get("/api/staff/server", headers=_headers(client_id)).status_code == 403
    body = client.get("/api/staff/server", headers=_headers(admin_id)).json()
    assert [a["code"] for a in body["alerts"]] == ["worker_absent", "disk_full"] and body["services"][0]["role"] == "web"
    with cloud_db.connect(cloud_database_url) as conn:
        _worker_publishes(conn, "w1", memory=2400 << 20)
        _worker_publishes(conn, "w2", memory=2400 << 20)
    memory = [a for a in client.get("/api/staff/server", headers=_headers(admin_id)).json()["alerts"] if a["code"] == "worker_memory"]
    assert sorted(a["instance"] for a in memory) == ["w1", "w2"]


def test_the_history_keeps_one_row_per_minute_and_drops_what_is_older_than_30_days(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM service_metrics_history")
        conn.execute("""INSERT INTO service_metrics_history (instance, role, sampled_at, memory, load1, disk_ratio)
                        VALUES ('old', 'worker', now() - interval '31 days', 1, 0, 0)""")
        for _ in range(4):  # a worker publishes every 15 s: one row for the minute, not four
            _worker_publishes(conn, "w-history")
        rows = conn.execute("SELECT instance, disk_ratio FROM service_metrics_history").fetchall()
    assert rows == [{"instance": "w-history", "disk_ratio": 0.1}]


def test_the_server_history_is_for_platform_admins_and_has_a_point_per_step(client, org_with_users, cloud_org, cloud_brand, cloud_database_url):
    _, _, admin_id, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM service_metrics_history")
        conn.execute("""INSERT INTO service_metrics_history (instance, role, sampled_at, cpu_percent, memory, load1, disk_ratio)
                        SELECT 'w1', 'worker', now() - make_interval(mins => m), 10 * m, 1000, 0.5, 0.25 FROM generate_series(1, 20) m""")
        conn.execute("""INSERT INTO jobs (org_id, brand_id, kind, status, created_at, started_at, finished_at)
                        VALUES (%s, %s, 'crawl', 'done', now() - interval '30 min', now() - interval '20 min', now() - interval '10 min')""", (cloud_org, cloud_brand))
    assert client.get("/api/staff/server/history").status_code == 401
    assert client.get("/api/staff/server/history", headers=_headers(client_id)).status_code == 403
    assert client.get("/api/staff/server/history?range=2y", headers=_headers(admin_id)).status_code == 422
    body = client.get("/api/staff/server/history?range=1h", headers=_headers(admin_id)).json()
    assert body["step"] == 60 and body["thresholds"] == {"disk": 0.8, "worker_memory": 0.85} and len(body["containers"]) == 20 and {c["instance"] for c in body["containers"]} == {"w1"}
    assert body["host"][0]["disk"] == 0.25 and max(c["cpu"] for c in body["containers"]) == 200.0
    day = client.get("/api/staff/server/history", headers=_headers(admin_id)).json()  # 10-minute steps: the 20 rows make 3 or 4 points
    assert day["range"] == "24h" and 2 <= len(day["containers"]) <= 4 and len(day["queue"]) <= 200
    assert max(point["wait_max"] or 0 for point in body["queue"]) >= 600  # the job waited 10 minutes
    assert any(point["queued"] >= 1 for point in body["queue"]) and any(point["running"] >= 1 for point in body["queue"])


def test_there_is_no_public_signup(client):
    assert client.post("/api/signup", json={"email": "a@b.c", "password": "12345678"}).status_code in {404, 405}


def test_list_orgs_includes_slug_role_and_brands(client, org_with_users, cloud_brand):
    org_id, _, admin_id, _, _ = org_with_users
    orgs = client.get("/api/orgs", headers=_headers(admin_id)).json()["organizations"]
    slug = orgs[0]["slug"]
    assert orgs == [{"org_id": str(org_id), "name": "Test Org", "slug": slug, "role": "admin",
                     "brands": [{"id": str(cloud_brand), "name": "Test Org", "slug": slug, "logo": None}]}]


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


def test_brands_are_created_and_deleted_by_platform_admins_and_renamed_by_org_admins(
        client, org_with_users, cloud_database_url):
    org_id, _, admin_id, client_id, outsider_id = org_with_users
    url = f"/api/orgs/{org_id}/brands"
    with cloud_db.connect(cloud_database_url) as conn:
        org_admin_id = uuid.uuid4()
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (org_admin_id, f"{org_admin_id}@x.com"))
        cloud_db.add_membership(conn, user_id=org_admin_id, org_id=org_id, role="admin")
    assert client.post(url, headers=_headers(client_id), json={"name": "Louis XIII"}).status_code == 403
    assert client.post(url, headers=_headers(org_admin_id), json={"name": "Louis XIII"}).status_code == 403  # not a platform admin
    assert client.post(f"/api/orgs/{uuid.uuid4()}/brands", headers=_headers(admin_id), json={"name": "X"}).status_code == 404
    brand = client.post(url, headers=_headers(admin_id), json={"name": "Louis XIII"}).json()["brand"]
    assert brand["slug"] == "louis-xiii"
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT created_by FROM brands WHERE id = %s", (brand["id"],)).fetchone()["created_by"] == admin_id
    assert client.delete(f"{url}/{brand['id']}", headers=_headers(org_admin_id)).status_code == 403
    assert client.delete(f"{url}/{brand['id']}", headers=_headers(admin_id)).status_code == 200
    brand = client.post(url, headers=_headers(admin_id), json={"name": "Louis XIII"}).json()["brand"]
    assert client.post(url, headers=_headers(admin_id), json={"name": "Louis  XIII"}).status_code == 409
    renamed = client.put(f"{url}/{brand['id']}", headers=_headers(admin_id), json={"name": "Louis XIII Cognac"}).json()
    assert renamed["brand"] == {"id": brand["id"], "name": "Louis XIII Cognac", "slug": "louis-xiii-cognac", "logo": None}
    names = [b["name"] for b in client.get("/api/orgs", headers=_headers(client_id)).json()["organizations"][0]["brands"]]
    assert names == ["Louis XIII Cognac", "Test Org"]


def test_team_is_listed_for_members_and_managed_by_admins_who_cannot_leave_the_org_without_an_admin(
        client, org_with_users, cloud_database_url, monkeypatch):
    org_id, _, admin_id, client_id, outsider_id = org_with_users
    base = f"/api/orgs/{org_id}/members"
    monkeypatch.setattr("nyra.cloud.routes.members.find_or_invite_user",
                        lambda *args: (str(outsider_id), True))
    listed = client.get(base, headers=_headers(client_id)).json()["members"]
    assert {m["role"] for m in listed} == {"admin", "client"}
    assert [m["you"] for m in listed if m["user_id"] == str(client_id)] == [True]
    assert client.get(base, headers=_headers(outsider_id)).status_code == 403
    assert client.post(base, json={"email": "a@b.co"}, headers=_headers(client_id)).status_code == 403

    assert client.post(base, json={"email": "pas-un-mail"}, headers=_headers(admin_id)).status_code == 400
    added = client.post(base, json={"email": "A@B.co", "role": "client"}, headers=_headers(admin_id))
    assert added.status_code == 200 and added.json()["invited"] is True
    assert client.post(base, json={"email": "a@b.co"}, headers=_headers(admin_id)).status_code == 409

    assert client.put(f"{base}/{outsider_id}", json={"role": "admin"}, headers=_headers(admin_id)).status_code == 200
    assert client.put(f"{base}/{outsider_id}", json={"role": "client"}, headers=_headers(admin_id)).status_code == 200
    assert client.put(f"{base}/{admin_id}", json={"role": "client"}, headers=_headers(admin_id)).status_code == 409
    assert client.delete(f"{base}/{admin_id}", headers=_headers(admin_id)).status_code == 409
    assert client.delete(f"{base}/{outsider_id}", headers=_headers(admin_id)).status_code == 200
    assert client.delete(f"{base}/{outsider_id}", headers=_headers(admin_id)).status_code == 404


def test_addresses_belong_to_one_brand_and_are_checked(client, org_with_users, monkeypatch):
    org_id, base, admin_id, client_id, _ = org_with_users
    from nyra import netguard

    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    body = {"url": "https://www.Louis-XIII.com/fr?utm_source=x", "label": "FR"}
    assert client.post(f"{base}/sites", headers=_headers(client_id), json=body).status_code == 403
    added = client.post(f"{base}/sites", headers=_headers(admin_id), json=body).json()
    site = added["site"]
    assert added["warning"] is None
    inner = client.post(f"{base}/sites", headers=_headers(admin_id), json={"url": "https://www.louis-xiii.com/fr/vins"}).json()
    assert "https://www.louis-xiii.com/fr contient déjà cette adresse" in inner["warning"]
    assert client.delete(f"{base}/sites/{inner['site']['id']}", headers=_headers(admin_id)).status_code == 200
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
                                                                     fake_storage_client, cloud_database_url):
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
    assert library["indexing"] is False  # queued, no worker on it yet: not work in progress
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (body["job"]["id"],))
    assert client.get(f"{base}/library", headers=_headers(client_id)).json()["indexing"] is True
    item = library["items"][0]
    assert item["indexed"] is False
    assert "thumb_url" not in item and "url" not in item  # signed on demand, see the next tests
    thumbs = client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": ["a.jpg"]})
    assert thumbs.json()["urls"]["a.jpg"].startswith(f"https://fake-storage.test/refs/{org_id}/{cloud_brand}/thumbs/")


def test_original_url_is_signed_on_demand_for_members_of_the_brand_only(client, org_with_users, cloud_brand,
                                                                        fake_storage_client):
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    brands = f"/api/orgs/{org_id}/brands"
    other = client.post(brands, headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    other_base = f"{brands}/{other['id']}"
    _upload(client, base, admin_id, "a.jpg")
    _upload(client, other_base, admin_id, "b.jpg")

    for user in (admin_id, client_id):  # a client-role member can read it too
        r = client.get(f"{base}/library/a.jpg/url", headers=_headers(user))
        assert r.status_code == 200
        assert r.json()["url"].startswith(f"https://fake-storage.test/refs/{org_id}/{cloud_brand}/a.jpg?")
    assert client.get(f"{base}/library/a.jpg/url", headers=_headers(outsider_id)).status_code == 403
    assert client.get(f"{base}/library/a.jpg/url").status_code == 401
    assert client.get(f"{base}/library/none.jpg/url", headers=_headers(client_id)).status_code == 404
    assert client.get(f"{base}/library/notes.txt/url", headers=_headers(client_id)).status_code == 400
    # A reference of another brand is not reachable through this one.
    assert client.get(f"{base}/library/b.jpg/url", headers=_headers(client_id)).status_code == 404
    assert client.get(f"{other_base}/library/b.jpg/url", headers=_headers(client_id)).status_code == 200
    # The one-segment routes keep working next to it.
    assert client.get(f"{base}/library/export-csv", headers=_headers(admin_id)).status_code == 200


@pytest.fixture()
def signed_paths(fake_storage_client, monkeypatch):
    """The paths Storage was asked to sign. Like Supabase, it gives no URL for an object that is not there."""
    monkeypatch.setattr(cloud_storage, "_signed", {})  # a fresh URL cache
    proxy = type(fake_storage_client.from_("refs"))
    asked: list[str] = []
    real = proxy.create_signed_urls

    def strict(self, paths, expires_in, options=None):
        asked.extend(paths)
        return [item if (self.bucket, item["path"]) in self.store else {**item, "signedURL": None, "error": "Object not found"}
                for item in real(self, paths, expires_in, options)]

    monkeypatch.setattr(proxy, "create_signed_urls", strict)
    return asked


def _put_references(conn, store, org_id, brand_id, count: int) -> None:
    for number in range(count):
        name = f"r{number:04d}.jpg"
        paths = {key: f"{org_id}/{brand_id}/{key}/{name}" for key in ("thumbs", "work")}
        for path in paths.values():
            store[("refs", path)] = b"x"
        cloud_db.upsert_reference_image(
            conn, org_id=org_id, brand_id=brand_id, filename=name, storage_path=f"{org_id}/{brand_id}/{name}",
            thumb_path=paths["thumbs"], work_path=paths["work"], expiry_date=None, credit=None, notes=None,
        )


def test_library_list_signs_nothing_and_thumbs_sign_only_what_is_asked(
        client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client, signed_paths):
    org_id, base, admin_id, client_id, outsider_id = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        _put_references(conn, fake_storage_client.store, org_id, cloud_brand, 1000)  # was: 1,000 signings per list load

    items = client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]
    assert len(items) == 1000 and all("thumb_url" not in item for item in items)
    assert signed_paths == []

    names = [item["filename"] for item in items[:100]]
    r = client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": names})
    assert set(r.json()["urls"]) == set(names)
    assert all(f"/thumbs/{name}?" in url for name, url in r.json()["urls"].items())
    assert len(signed_paths) == 100

    assert client.post(f"{base}/library/thumbs", headers=_headers(client_id),
                       json={"filenames": [f"r{n:04d}.jpg" for n in range(201)]}).status_code == 422
    # Names are matched exactly against this brand's references: an unknown or odd one has no entry, it is not an error.
    odd = client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": ["notes.txt", "../x.jpg", names[0]]})
    assert odd.status_code == 200 and set(odd.json()["urls"]) == {names[0]}
    assert client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": ["x" * 201]}).status_code == 422
    assert client.post(f"{base}/library/thumbs", headers=_headers(outsider_id), json={"filenames": names}).status_code == 403
    assert client.post(f"{base}/library/thumbs", json={"filenames": names}).status_code == 401


def test_thumbs_answer_502_once_when_storage_fails_instead_of_an_empty_200(
        client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client, monkeypatch):
    org_id, base, _, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        _put_references(conn, fake_storage_client.store, org_id, cloud_brand, 3)
    calls = []

    def broken(self, paths, expires_in, options=None):
        calls.append(len(paths))
        raise RuntimeError("storage down")

    monkeypatch.setattr(cloud_storage, "_signed", {})  # a fresh URL cache, so nothing is served from it
    monkeypatch.setattr(type(fake_storage_client.from_("refs")), "create_signed_urls", broken)
    r = client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": ["r0000.jpg", "r0001.jpg"]})
    assert r.status_code == 502 and calls == [2]  # one attempt, an error the interface retries: not a cached blank


def test_thumbs_are_scoped_to_the_brand_and_fall_back_when_the_thumbnail_object_is_missing(
        client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client, signed_paths):
    org_id, base, admin_id, client_id, _ = org_with_users
    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    _upload(client, base, admin_id, "gone.jpg", "bare.jpg", "ok.jpg")
    _upload(client, f"/api/orgs/{org_id}/brands/{other['id']}", admin_id, "elsewhere.jpg")
    store = fake_storage_client.store
    del store[("refs", f"{org_id}/{cloud_brand}/thumbs/gone.jpg.jpg")]  # thumbnail object missing, working copy there
    for kind in ("thumbs", "work"):
        del store[("refs", f"{org_id}/{cloud_brand}/{kind}/bare.jpg.jpg")]  # only the original is left

    r = client.post(f"{base}/library/thumbs", headers=_headers(client_id),
                    json={"filenames": ["gone.jpg", "bare.jpg", "ok.jpg", "elsewhere.jpg", "unknown.jpg"]})
    urls = r.json()["urls"]
    assert set(urls) == {"gone.jpg", "bare.jpg", "ok.jpg"}  # another brand's file and an unknown one: no entry
    assert f"/refs/{org_id}/{cloud_brand}/work/gone.jpg.jpg?" in urls["gone.jpg"]
    assert f"/refs/{org_id}/{cloud_brand}/bare.jpg?" in urls["bare.jpg"]
    assert "/thumbs/ok.jpg.jpg?" in urls["ok.jpg"]

    del store[("refs", f"{org_id}/{cloud_brand}/bare.jpg")]  # nothing left at all: no entry, no crash
    cloud_storage.forget_signed("refs", [f"{org_id}/{cloud_brand}/bare.jpg"])  # (a URL already handed out is reused)
    r = client.post(f"{base}/library/thumbs", headers=_headers(client_id), json={"filenames": ["bare.jpg", "ok.jpg"]})
    assert r.status_code == 200 and list(r.json()["urls"]) == ["ok.jpg"]


def test_accented_names_get_a_safe_storage_key_and_one_bad_file_spares_the_others(
    client, org_with_users, cloud_brand, fake_storage_client, monkeypatch
):
    org_id, base, admin_id, client_id, _ = org_with_users
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


def test_upload_replaces_a_visual_of_the_same_name_only_when_asked(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    first = _upload(client, base, admin_id, "a.jpg")
    assert first.json()["replaced"] == [] and first.json()["renamed"] == []
    again = _upload(client, base, admin_id, "a.jpg", "b.jpg", replace=True).json()
    assert again["saved"] == ["a.jpg", "b.jpg"] and again["replaced"] == ["a.jpg"] and again["renamed"] == []


def test_upload_refuses_the_same_file_under_another_name_and_says_which_visual_it_is(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    data = _upload_bytes().getvalue()
    send = lambda name, **form: client.post(f"{base}/library/upload", headers=_headers(admin_id), data=form or None,  # noqa: E731
                                            files=[("files", (name, io.BytesIO(data), "image/jpeg"))]).json()
    assert send("a.jpg")["saved"] == ["a.jpg"]
    for body in (send("copie.jpg"), send("a.jpg"), send("a.jpg", replace="true")):  # another name, the same name, a replacement
        assert body["saved"] == [] and body["job"] is None
        assert body["failed"][0]["reason"] == "Identique à « a.jpg », déjà dans la bibliothèque : rien n'a été ajouté."
    items = client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]
    assert [item["filename"] for item in items] == ["a.jpg"]


def test_upload_keeps_both_visuals_when_a_name_is_taken(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    client.put(f"{base}/library/a.jpg", headers=_headers(admin_id), json={"credit": "Studio", "expiry_date": "2027-05-05"})
    # taken by the library, taken by the same upload, capitals do not count, the extension stays last
    body = _upload(client, base, admin_id, "a.jpg", "A.JPG", "a.jpg", "b.jpg", "b.jpg").json()
    assert body["saved"] == ["a (1).jpg", "A (2).JPG", "a (3).jpg", "b.jpg", "b (1).jpg"] and body["replaced"] == []
    assert body["renamed"] == [{"from": "a.jpg", "to": "a (1).jpg"}, {"from": "A.JPG", "to": "A (2).JPG"},
                               {"from": "a.jpg", "to": "a (3).jpg"}, {"from": "b.jpg", "to": "b (1).jpg"}]
    items = {i["filename"]: i for i in client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]}
    assert sorted(items) == ["A (2).JPG", "a (1).jpg", "a (3).jpg", "a.jpg", "b (1).jpg", "b.jpg"]
    assert items["a.jpg"]["credit"] == "Studio" and items["a.jpg"]["expiry_date"] == "2027-05-05"  # the first one is untouched
    assert items["a (1).jpg"]["credit"] == "" and items["a (1).jpg"]["expiry_date"] == ""


def test_two_uploads_of_one_name_at_the_same_time_keep_both(client, org_with_users):
    from concurrent.futures import ThreadPoolExecutor

    _, base, admin_id, client_id, _ = org_with_users
    with ThreadPoolExecutor(max_workers=4) as pool:
        bodies = [future.result().json() for future in [pool.submit(_upload, client, base, admin_id, "c.jpg") for _ in range(4)]]
    assert sorted(name for body in bodies for name in body["saved"]) == ["c (1).jpg", "c (2).jpg", "c (3).jpg", "c.jpg"]
    items = client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]
    assert sorted(item["filename"] for item in items) == ["c (1).jpg", "c (2).jpg", "c (3).jpg", "c.jpg"]  # none replaced the other


def test_a_tiff_upload_is_kept_as_a_jpeg_and_a_16_bit_grey_one_is_not_white(client, org_with_users, fake_storage_client):
    import numpy as np

    _, base, admin_id, _, _ = org_with_users
    ramp = io.BytesIO()
    Image.fromarray(np.tile(np.linspace(0, 65535, 300).astype("uint16"), (200, 1))).save(ramp, format="TIFF")
    color = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 30, 30)).save(color, format="TIFF")
    sent = client.post(f"{base}/library/upload", headers=_headers(admin_id),
                       files=[("files", ("scan.tif", ramp.getvalue(), "image/tiff")), ("files", ("couleur.tiff", color.getvalue(), "image/tiff"))])
    assert sent.status_code == 200 and sent.json()["saved"] == ["scan.tif", "couleur.tiff"] and sent.json()["failed"] == []
    refs = {path.rsplit("/", 1)[-1]: data for (bucket, path), data in fake_storage_client.store.items() if bucket == "refs"}
    assert refs["scan.tif"][:2] == b"\xff\xd8" and refs["couleur.tiff"][:2] == b"\xff\xd8"  # JPEG magic: a picture a browser shows
    shown = Image.open(io.BytesIO(refs["scan.tif"])).convert("L")
    assert shown.getpixel((2, 100)) < 40 and shown.getpixel((297, 100)) > 215  # the 16-bit ramp is a ramp, not white


def test_numbered_names_stay_within_the_length_limit_and_stop_at_the_hundredth():
    from nyra.cloud.routes import library as library_routes
    from nyra.cloud.routes.common import MAX_FILENAME

    assert library_routes.numbered_name("photo.jpg", 2) == "photo (2).jpg"
    assert library_routes.numbered_name("archive.tar.jpg", 1) == "archive.tar (1).jpg"
    long = "x" * (MAX_FILENAME - 4) + ".jpg"
    cut = library_routes.numbered_name(long, 12)
    assert len(cut) == MAX_FILENAME and cut.endswith(" (12).jpg")


def test_the_hundred_and_first_copy_of_a_name_is_refused(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    from nyra.cloud.routes import library as library_routes

    with cloud_db.connect(cloud_database_url) as conn:
        names = ["z.jpg", *(library_routes.numbered_name("z.jpg", n) for n in range(1, library_routes.MAX_SAME_NAME + 1))]
        for name in names:
            conn.execute("""INSERT INTO reference_images (org_id, brand_id, filename, storage_path) VALUES (%s, %s, %s, %s)""",
                         (org_id, cloud_brand, name, f"{org_id}/{name}"))
    body = _upload(client, base, admin_id, "z.jpg").json()
    assert body["saved"] == [] and body["failed"][0]["filename"] == "z.jpg" and "100 visuels portent déjà ce nom" in body["failed"][0]["reason"]


def test_reupload_moves_a_reference_stored_before_brands(client, org_with_users, cloud_brand, cloud_database_url,
                                                         fake_storage_client):
    org_id, base, admin_id, _, _ = org_with_users
    legacy = f"{org_id}/old.jpg"
    fake_storage_client.store[("refs", legacy)] = b"old"
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="old.jpg", storage_path=legacy,
                                        expiry_date="2027-01-01", credit="Studio", notes=None)
    _upload(client, base, admin_id, "old.jpg", replace=True)
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


def test_a_common_expiry_applies_to_the_files_of_one_upload_only(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    _upload(client, base, admin_id, "old.jpg")
    client.put(f"{base}/library/old.jpg", headers=_headers(admin_id), json={"expiry_date": "2030-01-01"})
    files = [("files", (name, _upload_bytes(), "image/jpeg")) for name in ("a.jpg", "b.jpg")]
    sent = client.post(f"{base}/library/upload", headers=_headers(admin_id), files=files, data={"expiry_date": "2027-03-31"})
    assert sent.status_code == 200 and sent.json()["saved"] == ["a.jpg", "b.jpg"]
    items = {i["filename"]: i for i in client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"]}
    assert items["a.jpg"]["expiry_date"] == items["b.jpg"]["expiry_date"] == "2027-03-31"
    assert items["old.jpg"]["expiry_date"] == "2030-01-01"  # not in this upload: untouched
    bad = client.post(f"{base}/library/upload", headers=_headers(admin_id), files=files, data={"expiry_date": "pas une date"})
    assert bad.status_code == 400
    free = client.post(f"{base}/library/upload", headers=_headers(admin_id), data={"unlimited_rights": "true", "replace": "true"},
                       files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])
    assert free.json()["replaced"] == ["a.jpg"]  # a replaced visual takes the common value too
    items = {i["filename"]: i for i in client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"]}
    assert items["a.jpg"]["unlimited_rights"] is True and items["a.jpg"]["expiry_date"] == ""
    _upload(client, base, admin_id, "b.jpg", replace=True)  # no common value: the replaced visual keeps its own
    items = {i["filename"]: i for i in client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"]}
    assert items["b.jpg"]["expiry_date"] == "2027-03-31"


def test_bulk_credit_sets_and_clears_the_credit_of_the_named_visuals_only(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg", "c.jpg")

    def credits():
        items = client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"]
        return {item["filename"]: item["credit"] for item in items}

    url = f"{base}/library/credit"
    done = client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg"], "credit": "  Studio Dupont  "})
    assert done.status_code == 200 and done.json() == {"updated": 2}
    assert credits() == {"a.jpg": "Studio Dupont", "b.jpg": "Studio Dupont", "c.jpg": ""}  # trimmed; c.jpg untouched
    assert client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg"], "credit": ""}).json() == {"updated": 1}
    assert credits()["a.jpg"] == "" and credits()["b.jpg"] == "Studio Dupont"  # empty clears, only the named one
    assert client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg"], "credit": "x" * 501}).status_code == 422
    assert client.post(url, headers=_headers(client_id), json={"filenames": ["a.jpg"], "credit": "Studio"}).status_code == 403


def test_unlimited_rights_exclude_a_date_and_go_through_csv_both_ways(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg")
    url = f"{base}/library/a.jpg"
    assert client.put(url, headers=_headers(admin_id), json={"unlimited_rights": True, "expiry_date": "2027-01-01"}).status_code == 400
    assert client.put(url, headers=_headers(admin_id), json={"unlimited_rights": True}).json()["unlimited_rights"] is True
    items = {i["filename"]: i for i in client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"]}
    assert items["a.jpg"]["status"] == "illimite" and items["a.jpg"]["days_left"] is None
    assert items["b.jpg"]["status"] == "inconnue"  # no date entered is not unlimited
    _upload(client, base, admin_id, "a.jpg", replace=True)  # a replaced picture keeps its rights
    assert client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"][0]["unlimited_rights"] is True
    exported = client.get(f"{base}/library/export-csv", headers=_headers(admin_id)).content.decode("utf-8-sig")
    assert "a.jpg,illimité," in exported
    client.put(url, headers=_headers(admin_id), json={"expiry_date": "2027-01-01"})  # a date takes the rights back
    assert client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"][0]["unlimited_rights"] is False
    applied = client.post(f"{base}/library/import-csv?apply=true", headers=_headers(admin_id),
                          files={"file": ("m.csv", exported.encode("utf-8"), "text/csv")}).json()
    assert applied["applied"] == 2
    assert client.get(f"{base}/library", headers=_headers(admin_id)).json()["items"][0]["unlimited_rights"] is True
    bulk = client.post(f"{base}/library/expiry", headers=_headers(admin_id),
                       json={"filenames": ["a.jpg", "b.jpg"], "unlimited_rights": True})
    assert bulk.json()["updated"] == 2


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


def _upload(client, base, admin_id, *names, replace=False):
    return client.post(f"{base}/library/upload", headers=_headers(admin_id), data={"replace": "true"} if replace else None,
                       files=[("files", (name, _upload_bytes(), "image/jpeg")) for name in names])


def _tags(client, base, user_id) -> dict:
    items = client.get(f"{base}/library", headers=_headers(user_id)).json()["items"]
    return {item["filename"]: item["tags"] for item in items}


def test_tags_are_set_per_reference_normalized_and_left_alone_when_omitted(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    assert _tags(client, base, client_id) == {"a.jpg": []}
    url = f"{base}/library/a.jpg"
    r = client.put(url, headers=_headers(admin_id), json={"tags": ["  Magnum ", "classic", "MAGNUM"]})
    assert r.json()["tags"] == ["magnum", "classic"]
    assert _tags(client, base, client_id) == {"a.jpg": ["magnum", "classic"]}
    # A save that says nothing about tags (dates, CSV...) must not wipe them.
    client.put(url, headers=_headers(admin_id), json={"expiry_date": "2027-01-01"})
    assert _tags(client, base, client_id) == {"a.jpg": ["magnum", "classic"]}
    client.put(url, headers=_headers(admin_id), json={"tags": []})
    assert _tags(client, base, client_id) == {"a.jpg": []}
    too_long = client.put(url, headers=_headers(admin_id), json={"tags": ["x" * 41]})
    assert too_long.status_code == 400 and "trop long" in too_long.json()["detail"]
    too_many = client.put(url, headers=_headers(admin_id), json={"tags": [f"t{i}" for i in range(21)]})
    assert too_many.status_code == 400


def test_bulk_tags_add_and_remove_only_touch_the_named_references(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg", "c.jpg")
    url = f"{base}/library/tags"
    r = client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg", "none.jpg"], "add": ["Miniature"]})
    assert r.status_code == 200 and r.json()["updated"] == 2
    client.post(url, headers=_headers(admin_id), json={"filenames": ["b.jpg"], "add": ["magnum", "miniature"]})
    assert _tags(client, base, client_id) == {"a.jpg": ["miniature"], "b.jpg": ["miniature", "magnum"], "c.jpg": []}
    client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg", "c.jpg"], "remove": ["MINIATURE"]})
    assert _tags(client, base, client_id) == {"a.jpg": [], "b.jpg": ["magnum"], "c.jpg": []}
    assert client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg"]}).status_code == 400
    assert client.post(url, headers=_headers(admin_id), json={"filenames": ["a.jpg"], "add": ["x" * 41]}).status_code == 400


def test_bulk_tags_refuse_to_go_over_the_limit_and_change_nothing(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg")
    full = [f"t{i}" for i in range(20)]
    client.put(f"{base}/library/b.jpg", headers=_headers(admin_id), json={"tags": full})
    r = client.post(f"{base}/library/tags", headers=_headers(admin_id), json={"filenames": ["a.jpg", "b.jpg"], "add": ["extra"]})
    assert r.status_code == 400 and "b.jpg" in r.json()["detail"]
    assert _tags(client, base, client_id) == {"a.jpg": [], "b.jpg": full}


def test_only_admins_edit_tags(client, org_with_users):
    _, base, admin_id, client_id, outsider_id = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    assert client.put(f"{base}/library/a.jpg", headers=_headers(client_id), json={"tags": ["x"]}).status_code == 403
    assert client.post(f"{base}/library/tags", headers=_headers(client_id), json={"filenames": ["a.jpg"], "add": ["x"]}).status_code == 403
    assert client.post(f"{base}/library/tags", headers=_headers(outsider_id), json={"filenames": ["a.jpg"], "add": ["x"]}).status_code in (403, 404)
    assert _tags(client, base, client_id) == {"a.jpg": []}


def test_replacing_an_image_keeps_its_tags(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    client.put(f"{base}/library/a.jpg", headers=_headers(admin_id), json={"tags": ["classic"]})
    assert _upload(client, base, admin_id, "a.jpg", replace=True).json()["replaced"] == ["a.jpg"]
    assert _tags(client, base, client_id) == {"a.jpg": ["classic"]}


def test_csv_tags_column_replaces_tags_and_no_column_leaves_them(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg")
    client.put(f"{base}/library/b.jpg", headers=_headers(admin_id), json={"tags": ["keep"]})
    url = f"{base}/library/import-csv"

    with_tags = 'filename,expiry_date,tags\na.jpg,2027-01-01,"Miniature, classic"\nb.jpg,2027-01-01,\n'.encode()
    preview = client.post(url, headers=_headers(admin_id), files={"file": ("m.csv", with_tags, "text/csv")}).json()
    assert [row["tags"] for row in preview["rows"]] == [["miniature", "classic"], []]
    assert _tags(client, base, client_id) == {"a.jpg": [], "b.jpg": ["keep"]}  # a preview writes nothing
    client.post(url + "?apply=true", headers=_headers(admin_id), files={"file": ("m.csv", with_tags, "text/csv")})
    assert _tags(client, base, client_id) == {"a.jpg": ["miniature", "classic"], "b.jpg": []}

    without = b"filename;expiry_date\na.jpg;2028-02-02\n"
    client.post(url + "?apply=true", headers=_headers(admin_id), files={"file": ("m.csv", without, "text/csv")})
    assert _tags(client, base, client_id) == {"a.jpg": ["miniature", "classic"], "b.jpg": []}

    bad = f"filename,tags\na.jpg,{'x' * 41}\n".encode()
    rows = client.post(url, headers=_headers(admin_id), files={"file": ("m.csv", bad, "text/csv")}).json()["rows"]
    assert rows[0]["status"] == "bad_tags"


def test_csv_export_neutralizes_formulas_and_imports_back_to_the_same_text(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg", "b.jpg")
    cases = {"a.jpg": ('=HYPERLINK("http://x.test","go")', "@SUM(A1)"), "b.jpg": ("-sale", "+33 6 12")}
    for name, (credit, notes) in cases.items():
        tags = ["=cmd"] if name == "b.jpg" else []
        client.put(f"{base}/library/{name}", headers=_headers(admin_id), json={"credit": credit, "notes": notes, "tags": tags})
    raw = client.get(f"{base}/library/export-csv", headers=_headers(client_id)).content.decode("utf-8-sig")
    cells = {row[0]: row for row in csv.reader(io.StringIO(raw))}
    assert cells["a.jpg"][2] == "'=HYPERLINK(\"http://x.test\",\"go\")" and cells["a.jpg"][3] == "'@SUM(A1)"
    assert cells["b.jpg"][2] == "'-sale" and cells["b.jpg"][3] == "+33 6 12"  # a number after + stays as it is
    assert cells["b.jpg"][4] == "'=cmd"
    assert not any(cell[:1] in "=@" for row in cells.values() for cell in row if cell)
    # Importing the exported file restores the original text, apostrophes included only where they were not added.
    for name in cases:
        client.put(f"{base}/library/{name}", headers=_headers(admin_id), json={"credit": "x", "notes": "x", "tags": []})
    client.post(f"{base}/library/import-csv?apply=true", headers=_headers(admin_id), files={"file": ("e.csv", raw.encode(), "text/csv")})
    items = {item["filename"]: item for item in client.get(f"{base}/library", headers=_headers(client_id)).json()["items"]}
    for name, (credit, notes) in cases.items():
        assert (items[name]["credit"], items[name]["notes"]) == (credit, notes)
    assert items["b.jpg"]["tags"] == ["=cmd"]


def test_csv_import_ignores_cells_beyond_the_header(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    ragged = b"filename,tags\na.jpg,x,extra,cells\n"
    r = client.post(f"{base}/library/import-csv", headers=_headers(admin_id), files={"file": ("m.csv", ragged, "text/csv")})
    assert r.status_code == 200 and r.json()["rows"][0]["tags"] == ["x"]


def test_csv_export_carries_the_tags(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    _upload(client, base, admin_id, "a.jpg")
    client.put(f"{base}/library/a.jpg", headers=_headers(admin_id), json={"tags": ["miniature", "classic"]})
    text = client.get(f"{base}/library/export-csv", headers=_headers(client_id)).content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "filename,expiry_date,credit,notes,tags"
    assert lines[1] == 'a.jpg,,,,"miniature, classic"'


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


def test_indexing_what_is_pending_is_for_admins_and_joins_the_job_already_waiting(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    assert client.post(f"{base}/jobs/index", headers=_headers(client_id)).status_code == 403
    assert client.post(f"{base}/jobs/index").status_code == 401
    first = client.post(f"{base}/jobs/index", headers=_headers(admin_id))
    assert first.status_code == 200 and first.json()["job"]["kind"] == "index" and first.json()["job"]["status"] == "queued"
    again = client.post(f"{base}/jobs/index", headers=_headers(admin_id))
    assert again.status_code == 200 and again.json()["job"]["id"] == first.json()["job"]["id"]


def test_logos_are_set_aside_only_when_the_brand_asks_and_can_be_put_back(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    from nyra import logos as detector
    from nyra.cloud.store import CloudMatchStore

    org_id, base, admin_id, client_id, _ = org_with_users
    text = detector._text()
    logo_look, photo_look = text[1], text[len(detector.LOGO_PROMPTS)]  # what a logo and a photograph look like to CLIP
    data = _upload_bytes().getvalue()
    ids = {}
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand, "https://t.test/fr/")
        for name, url, content_hash, phash, look in (
            ("logo", "https://t.test/logo.png", "logo", "0f0f0f0f0f0f0f0f", logo_look),
            ("twin", "https://t.test/logo-2x.png", "logo", "0f0f0f0f0f0f0f0f", logo_look),  # the same bytes under another address
            ("photo", "https://t.test/photo.jpg", "photo", "ffff0000ffff0000", photo_look),
        ):
            fake_storage_client.store[("site-images", f"{org_id}/{content_hash}.jpg")] = data
            ids[name] = conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, content_hash, phash, dhash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, url, f"{org_id}/{content_hash}.jpg", f"{org_id}/thumbs/{content_hash}.jpg", content_hash, phash, phash, look),
            ).fetchone()["id"]
    store = CloudMatchStore(org_id=org_id, brand_id=cloud_brand, database_url=cloud_database_url)
    listed = lambda: client.get(f"{base}/site-images", headers=_headers(admin_id)).json()["items"]  # noqa: E731
    unreferenced = lambda: client.get(f"{base}/overview", headers=_headers(admin_id)).json()["dashboard"]["unreferenced_online"]  # noqa: E731

    # Off by default: the logos are recognised and listed, and still compared and shown.
    assert client.get(f"{base}/logos", headers=_headers(client_id)).status_code == 403
    state = client.get(f"{base}/logos", headers=_headers(admin_id)).json()
    assert state["enabled"] is False and len(state["items"]) == 1  # the two addresses of one file count once
    assert state["items"][0]["score"] >= detector.THRESHOLD and state["items"][0]["kept"] is False
    assert len(listed()) == 2 and unreferenced() == 2 and store.set_aside_ids() == set()

    # Turned on (by an admin): they leave the list, the counter and the comparison, and a comparison is queued.
    assert client.put(f"{base}/logos/setting", headers=_headers(client_id), json={"enabled": True}).status_code == 403
    turned_on = client.put(f"{base}/logos/setting", headers=_headers(admin_id), json={"enabled": True}).json()
    assert turned_on["enabled"] is True and turned_on["job"]["kind"] == "match"
    assert [item["url_count"] for item in listed()] == [1] and unreferenced() == 1
    assert store.set_aside_ids() == {ids["logo"], ids["twin"]}

    # Put back by a person: both addresses return; set aside again: both go.
    keep = {"site_image_ids": [str(state["items"][0]["id"])], "kept": True}
    assert client.post(f"{base}/logos/keep", headers=_headers(client_id), json=keep).status_code == 403
    assert client.post(f"{base}/logos/keep", headers=_headers(admin_id), json={**keep, "site_image_ids": [str(uuid.uuid4())]}).status_code == 404
    assert client.post(f"{base}/logos/keep", headers=_headers(admin_id), json=keep).status_code == 200
    assert len(listed()) == 2 and unreferenced() == 2 and store.set_aside_ids() == set()
    assert client.get(f"{base}/logos", headers=_headers(admin_id)).json()["items"][0]["kept"] is True
    assert client.post(f"{base}/logos/keep", headers=_headers(admin_id), json={**keep, "kept": False}).status_code == 200
    assert unreferenced() == 1 and store.set_aside_ids() == {ids["logo"], ids["twin"]}

    # Turned off again: everything is back.
    assert client.put(f"{base}/logos/setting", headers=_headers(admin_id), json={"enabled": False}).json()["enabled"] is False
    assert len(listed()) == 2 and unreferenced() == 2 and store.set_aside_ids() == set()


def test_a_change_the_running_job_did_not_see_queues_another_behind_it(org_with_users, cloud_brand, cloud_database_url):
    from nyra.cloud import jobs as cloud_jobs

    org_id = org_with_users[0]
    with cloud_db.connect(cloud_database_url) as conn:
        first = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=cloud_brand, kind="match")
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (first["id"],))
        try:
            cloud_jobs.enqueue(conn, org_id=org_id, brand_id=cloud_brand, kind="match")  # an ordinary request still conflicts
        except cloud_jobs.JobConflict:
            pass
        else:
            raise AssertionError("a running job of the same kind must conflict")
        behind = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=cloud_brand, kind="match", behind_running=True)
        assert behind["status"] == "queued" and behind["id"] != first["id"]
        try:
            cloud_jobs.enqueue(conn, org_id=org_id, brand_id=cloud_brand, kind="match", behind_running=True)
        except cloud_jobs.JobConflict:
            pass  # one waiting is enough: it will see the change
        else:
            raise AssertionError("a waiting job of the same kind must conflict")


def test_a_file_read_again_under_a_new_address_is_not_shown_as_put_back(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    from nyra import logos as detector

    org_id, base, admin_id, _, _ = org_with_users
    look = detector._text()[1]  # a logo to CLIP
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand, "https://t.test/fr/")

        def read(url):
            return conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash, embedding)
                   VALUES (%s, %s, %s, %s, 'logo', '0f0f0f0f0f0f0f0f', '0f0f0f0f0f0f0f0f', %s) RETURNING id""",
                (org_id, site, url, f"{org_id}/logo.jpg", look),
            ).fetchone()["id"]

        first = read("https://t.test/logo.png")
    listed = lambda: client.get(f"{base}/logos", headers=_headers(admin_id)).json()["items"]  # noqa: E731
    assert client.post(f"{base}/logos/keep", headers=_headers(admin_id), json={"site_image_ids": [str(first)], "kept": True}).status_code == 200
    assert [item["kept"] for item in listed()] == [True]
    with cloud_db.connect(cloud_database_url) as conn:
        read("https://t.test/logo-new-address.png")  # the same bytes, read after the person put the logo back
    assert [item["kept"] for item in listed()] == [False]  # the new address is set aside: the file is not shown as put back
    assert client.post(f"{base}/logos/keep", headers=_headers(admin_id), json={"site_image_ids": [str(first)], "kept": True}).status_code == 200
    assert [item["kept"] for item in listed()] == [True]  # putting it back again covers every address


def test_online_images_are_listed_by_tab_with_totals_pages_and_filters(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    from nyra import logos as detector

    org_id, base, admin_id, client_id, _ = org_with_users
    data = _upload_bytes().getvalue()
    ids = {}
    with cloud_db.connect(cloud_database_url) as conn:
        fr, uk = _site(conn, org_id, cloud_brand, "https://t.test/fr/"), _site(conn, org_id, cloud_brand, "https://t.test/uk/")
        # Far apart in the hashes, so that ignoring one never swallows another. Only the logo has an embedding (what a logo looks
        # like to CLIP), and no score yet: the page works it out when it is opened.
        for name, site, url, phash, look in (
            ("plain", fr, "https://t.test/media/plain.jpg", "0000000000000000", None),
            ("found", fr, "https://t.test/media/found.jpg", "ffffffffffffffff", None),
            ("logo", fr, "https://t.test/media/logo.png", "f0f0f0f0f0f0f0f0", detector._text()[1]),
            ("ignored", uk, "https://t.test/media/ignored.jpg", "0f0f0f0f0f0f0f0f", None),
        ):
            fake_storage_client.store[("site-images", f"{org_id}/{name}.jpg")] = data
            ids[name] = conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, content_hash, phash, dhash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, url, f"{org_id}/{name}.jpg", f"{org_id}/thumbs/{name}.jpg", name, phash, phash, look),
            ).fetchone()["id"]
        ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="r.jpg", storage_path=f"{org_id}/r.jpg",
                                              expiry_date=None, credit=None, notes=None)
        cloud_db.write_matches(conn, org_id, [(ref, ids["found"], "phash", 1.0, "haut")])
    assert client.post(f"{base}/exclusions", headers=_headers(admin_id), json={"site_image_id": str(ids["ignored"])}).status_code == 200

    def tab(view, **query):
        params = "&".join(f"{key}={value}" for key, value in {"view": view, **query}.items())
        return client.get(f"{base}/site-images?{params}", headers=_headers(client_id)).json()

    def listed(view, **query):
        return {item["id"] for item in tab(view, **query)["items"]}

    # Every tab with its total; the logo is still to qualify while the brand does not set logos aside.
    counts = tab("qualify")["counts"]
    assert counts == {"qualify": 2, "found": 1, "logos": 1, "ignored": 1, "all": 4}
    assert listed("qualify") == {str(ids["plain"]), str(ids["logo"])}
    assert listed("found") == {str(ids["found"])} and listed("logos") == {str(ids["logo"])} and listed("ignored") == {str(ids["ignored"])}
    assert len(listed("all")) == 4
    assert client.get(f"{base}/site-images?view=bogus", headers=_headers(client_id)).status_code == 400

    # Each entry says why it is where it is; a logo carries the detector's answer.
    statuses = {item["id"]: item["status"] for item in tab("all")["items"]}
    assert statuses == {str(ids["plain"]): "qualify", str(ids["found"]): "found", str(ids["logo"]): "logo", str(ids["ignored"]): "ignored"}
    logo = tab("logos")["items"][0]
    assert logo["logo"]["score"] >= detector.THRESHOLD and logo["logo"]["kept"] is False and next(i for i in tab("qualify")["items"] if i["id"] == str(ids["plain"]))["logo"] is None

    # Pages, the text search and the site version narrow the entries; the totals of the tabs do not move.
    first, second = tab("all", limit=3), tab("all", limit=3, offset=3)
    assert first["total"] == 4 and len(first["items"]) == 3 and len(second["items"]) == 1
    assert {i["id"] for i in first["items"]} | {i["id"] for i in second["items"]} == set(statuses)
    assert listed("all", q="PLAIN") == {str(ids["plain"])} and tab("all", q="PLAIN")["counts"]["all"] == 4
    assert listed("all", site=str(uk)) == {str(ids["ignored"])}
    assert sorted((entry["label"], entry["count"]) for entry in tab("all")["sites"]) == [("t.test", 1), ("t.test", 3)]

    # Setting the logos aside takes them out of the tab to qualify, not out of the logos tab.
    assert client.put(f"{base}/logos/setting", headers=_headers(admin_id), json={"enabled": True}).status_code == 200
    assert tab("qualify")["counts"]["qualify"] == 1 and listed("qualify") == {str(ids["plain"])} and listed("logos") == {str(ids["logo"])}


def test_putting_a_logo_back_covers_every_file_of_the_photo(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    org_id, base, admin_id, _, _ = org_with_users
    group = uuid.uuid4()
    ids = []
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand, "https://t.test/fr/")
        for name, side in (("big", 800), ("small", 200)):  # two files of one photo: a crop is another file
            fake_storage_client.store[("site-images", f"{org_id}/{name}.jpg")] = _upload_bytes().getvalue()
            ids.append(str(conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, content_hash, phash, dhash,
                                            width, height, variant_group, logo_score)
                   VALUES (%s, %s, %s, %s, %s, %s, '0f0f0f0f0f0f0f0f', '0f0f0f0f0f0f0f0f', %s, %s, %s, 0.99) RETURNING id""",
                (org_id, site, f"https://t.test/{name}.png", f"{org_id}/{name}.jpg", f"{org_id}/thumbs/{name}.jpg", name, side, side, group),
            ).fetchone()["id"]))
    logo = lambda: client.get(f"{base}/site-images?view=logos", headers=_headers(admin_id)).json()["items"][0]  # noqa: E731
    item = logo()
    assert len(item["variants"]) == 2 and item["logo"]["kept"] is False
    keep = client.post(f"{base}/logos/keep", headers=_headers(admin_id),
                       json={"site_image_ids": [variant["id"] for variant in item["variants"]], "kept": True})
    assert keep.status_code == 200
    assert logo()["logo"]["kept"] is True  # both files went back: the entry is shown as put back
    with cloud_db.connect(cloud_database_url) as conn:
        assert [row["logo_kept"] for row in conn.execute("SELECT logo_kept FROM site_images WHERE id = ANY(%s)", (ids,)).fetchall()] == [True, True]


def _audited(conn, org_id, action):
    return conn.execute("SELECT count(*) AS n FROM audit_log WHERE org_id = %s AND action = %s", (org_id, action)).fetchone()["n"]


def test_one_pass_at_a_time_per_organization(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    admin = _headers(admin_id)
    other = client.post(f"/api/orgs/{org_id}/brands", headers=admin, json={"name": "Autre"}).json()["brand"]
    other_base = f"/api/orgs/{org_id}/brands/{other['id']}"
    first = client.post(f"{base}/jobs/match", headers=admin)
    assert first.status_code == 200
    author = f"{admin_id}@x.com"
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE id = %s", (first.json()["job"]["id"],))
        name = conn.execute("SELECT name FROM brands WHERE id = %s", (cloud_brand,)).fetchone()["name"]
        before = _audited(conn, org_id, "job.match")
    # Another brand of the organization: refused by hand, whatever the kind; the answer says which brand, who, since when.
    for path in ("jobs/match", "reports"):
        refused = client.post(f"{other_base}/{path}", headers=admin, json={})
        assert refused.status_code == 409
        assert refused.json()["detail"] == f"Un passage est déjà en cours (marque {name}, lancé par {author}, à l'instant)."
    # Same brand: the message names the job, not the brand.
    same = client.post(f"{base}/jobs/match", headers=admin)
    assert same.status_code == 409 and same.json()["detail"].startswith("Une comparaison est déjà en cours ou en attente (lancé par ")
    with cloud_db.connect(cloud_database_url) as conn:
        assert _audited(conn, org_id, "job.match") == before and _audited(conn, org_id, "job.report") == 0  # a refusal traces nothing
    # An indexing is not refused: it waits behind.
    queued = client.post(f"{other_base}/jobs/index", headers=admin)
    assert queued.status_code == 200 and queued.json()["job"]["status"] == "queued"
    # Everyone sees it in the other brand: the administrator with the e-mail, the client without.
    held = client.get(f"{other_base}/jobs/current", headers=admin).json()["organization"]
    assert [(job["kind"], job["by"], job["brand_id"]) for job in held] == [("match", f"{admin_id}@x.com", str(cloud_brand))]
    as_client = client.get(f"{other_base}/jobs/current", headers=_headers(client_id)).json()["organization"]
    assert as_client[0]["by"] == "un membre de l'équipe" and f"{admin_id}" not in str(as_client)
    assert client.get(f"{other_base}/overview", headers=_headers(client_id)).json()["jobs"]["organization"][0]["brand_name"]
    # Cancelling is never refused, and only from the job's own brand.
    job_id = first.json()["job"]["id"]
    assert client.post(f"{other_base}/jobs/{job_id}/cancel", headers=admin).status_code == 404
    assert client.post(f"{base}/jobs/{job_id}/cancel", headers=admin).status_code == 200


def test_another_organization_is_not_held_up(client, org_with_users, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    assert client.post(f"{base}/jobs/match", headers=_headers(admin_id)).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:
        other = cloud_db.create_organization(conn, name="Z", slug=f"z-{uuid.uuid4().hex[:8]}")
        other_brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (other,)).fetchone()["id"]
        cloud_db.add_membership(conn, user_id=admin_id, org_id=other, role="admin")
    second = client.post(f"/api/orgs/{other}/brands/{other_brand}/jobs/match", headers=_headers(admin_id))
    assert second.status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:  # both run at once: the unique index is per organization
        conn.execute("UPDATE jobs SET status = 'running', started_at = now() WHERE org_id = ANY(%s) AND kind = 'match'", ([org_id, other],))
        assert conn.execute("SELECT count(*) AS n FROM jobs WHERE status = 'running' AND org_id = ANY(%s)", ([org_id, other],)).fetchone()["n"] == 2


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


def test_matches_filter_by_the_label_of_the_site(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(
            conn, org_id=org_id, brand_id=cloud_brand, filename="found.jpg", storage_path=f"{org_id}/found.jpg",
            expiry_date="2026-10-01", credit=None, notes=None, phash=same_hash, dhash=same_hash,
        )
        images = []
        for label, url in (("US", "https://target.test/us/"), ("FR", "https://target.test/fr/")):
            site = cloud_db.create_site(conn, org_id=org_id, brand_id=cloud_brand, url=url, label=label)
            images.append(conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, f"{url}found.jpg", f"{org_id}/{label}.jpg", same_hash, same_hash),
            ).fetchone()["id"])
        cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut") for image in images])

    everywhere = client.get(f"{base}/matches?within_days=3650", headers=_headers(admin_id)).json()
    assert everywhere["countries"] == ["FR", "US"]
    assert len(everywhere["confirmed"][0]["hits"]) == 2
    only_fr = client.get(f"{base}/matches?within_days=3650&country=FR", headers=_headers(admin_id)).json()
    assert [hit["site_url"] for hit in only_fr["confirmed"][0]["hits"]] == ["https://target.test/fr/found.jpg"]
    assert only_fr["countries"] == ["FR", "US"]  # the choice stays on offer whichever one is picked
    both = client.get(f"{base}/matches?within_days=3650&country=FR&country=US", headers=_headers(admin_id)).json()
    assert len(both["confirmed"][0]["hits"]) == 2
    nowhere = client.get(f"{base}/matches?within_days=3650&country=UK", headers=_headers(admin_id)).json()
    assert nowhere["confirmed"] == []


def test_locate_is_an_admin_job_and_occurrences_list_pages_with_signed_thumbnails(client, org_with_users, cloud_brand,
                                                                                  cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(
            conn, org_id=org_id, brand_id=cloud_brand, filename="a.jpg", storage_path=f"{org_id}/a.jpg",
            expiry_date=None, credit=None, notes=None, phash="ffff0000ffff0000", dhash="ffff0000ffff0000",
        )
        site = _site(conn, org_id, cloud_brand)
        crop = conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, phash, dhash)
               VALUES (%s, %s, 'https://target.test/crop.jpg', %s, %s, '0', '0') RETURNING id""",
            (org_id, site, f"{org_id}/crop.jpg", f"{org_id}/thumbs/crop.jpg"),
        ).fetchone()["id"]
        page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, 'https://target.test/p', 'done') RETURNING id",
                            (org_id, site)).fetchone()["id"]
        cloud_db.link_image_page(conn, crop, page)
        conn.execute("""INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage)
                        VALUES (%s, %s, %s, 'review', 12, 0.01, 0.4)""", (ref, crop, org_id))

    assert client.post(f"{base}/library/a.jpg/locate", headers=_headers(client_id)).status_code == 403
    assert client.post(f"{base}/library/nope.jpg/locate", headers=_headers(admin_id)).status_code == 404
    job = client.post(f"{base}/library/a.jpg/locate", headers=_headers(admin_id)).json()["job"]
    assert job["kind"] == "locate" and job["params"] == {"reference_id": str(ref)}
    assert client.post(f"{base}/library/a.jpg/locate", headers=_headers(admin_id)).status_code == 409

    found = client.get(f"{base}/library/a.jpg/occurrences", headers=_headers(client_id)).json()
    assert found["total_pages"] == 1 and found["images_found"] == 1 and found["pages_crawled"] == 1
    [shown] = found["pages"]
    assert shown["url"] == "https://target.test/p" and shown["image_count"] == 1
    assert shown["images"][0]["tier"] == "review"
    assert shown["images"][0]["thumb"].startswith(f"https://fake-storage.test/site-images/{org_id}/thumbs/")
    assert client.get(f"{base}/library/a.jpg/occurrences?limit=0", headers=_headers(client_id)).status_code == 422
    assert client.get(f"{base}/library/a.jpg/occurrences?tier=nope", headers=_headers(client_id)).status_code == 422
    only_review = client.get(f"{base}/library/a.jpg/occurrences?tier=review", headers=_headers(client_id)).json()
    assert only_review["total_pages"] == 1 and only_review["review_images"] == 1
    past_the_end = client.get(f"{base}/library/a.jpg/occurrences?offset=500", headers=_headers(client_id)).json()
    assert past_the_end["offset"] == 0 and len(past_the_end["pages"]) == 1  # a result that shrank is never answered empty

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.get(f"/api/orgs/{org_id}/brands/{other['id']}/library/a.jpg/occurrences",
                      headers=_headers(admin_id)).status_code == 404


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
    # The app shell answers any client-side address, the old French ones too (the browser then redirects them).
    assert client.get("/o/some-org/m/some-brand/bibliotheque").status_code == 200
    r = client.get("/o/some-org/b/some-brand/library")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/does-not-exist").status_code == 404
    # The robot's explanation page is public but kept out of search engines.
    assert client.get("/robot").headers["x-robots-tag"] == "noindex"
    assert "x-robots-tag" not in r.headers


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
    # The counter of the side column is cached, yet it follows the exclusion at once.
    assert client.get(f"{base}/overview", headers=_headers(client_id)).json()["dashboard"]["unreferenced_online"] == 1

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


def test_crops_of_one_photo_are_one_entry_listing_each_version_and_where_it_was_read(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    org_id, base, admin_id, client_id, _ = org_with_users
    data = _upload_bytes().getvalue()
    group = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        us, fr = _site(conn, org_id, cloud_brand, "https://t.test/us/"), _site(conn, org_id, cloud_brand, "https://t.test/fr/")
        for site, name, size, group_id in ((us, "small", (366, 372), group), (fr, "large", (435, 628), group),
                                           (fr, "alone", (200, 200), None)):
            path = f"{org_id}/{name}.jpg"
            fake_storage_client.store[("site-images", path)] = data
            image = conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash, width, height,
                                            variant_group)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, f"https://t.test/{name}.jpg", path, name, "ffff0000ffff0000", "ffff0000ffff0000", *size, group_id),
            ).fetchone()["id"]
            page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
                                (org_id, site, f"https://t.test/{name}-page")).fetchone()["id"]
            cloud_db.link_image_page(conn, image, page)

    items = client.get(f"{base}/site-images", headers=_headers(client_id)).json()["items"]
    assert len(items) == 2  # the two versions of the photo count once, next to the image alone
    photo = next(item for item in items if len(item["variants"]) == 2)
    assert (photo["width"], photo["height"]) == (435, 628)  # the card shows the largest
    large, small = photo["variants"]
    assert (large["width"], small["width"]) == (435, 366) and large["id"] == photo["id"]
    assert large["where"] == [{"site": "t.test", "page": "https://t.test/large-page"}] and small["where_count"] == 1
    assert photo["site_ids"] and len(photo["sites"]) == 2 and photo["page_count"] == 2
    assert client.get(f"{base}/overview", headers=_headers(client_id)).json()["dashboard"]["unreferenced_online"] == 2

    # The same bytes read at a new address, not seen by the worker yet (no group), join their file's entry;
    # two addresses on one page are one occurrence.
    with cloud_db.connect(cloud_database_url) as conn:
        image = conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash, width, height)
               SELECT org_id, site_id, 'https://t.test/large-copy.jpg', storage_path, content_hash, phash, dhash, width, height
               FROM site_images WHERE org_id = %s AND content_hash = 'large' RETURNING id""",
            (org_id,),
        ).fetchone()["id"]
        page = conn.execute("SELECT id FROM pages WHERE org_id = %s AND url = 'https://t.test/large-page'",
                            (org_id,)).fetchone()["id"]
        cloud_db.link_image_page(conn, image, page)
    items = client.get(f"{base}/site-images", headers=_headers(client_id)).json()["items"]
    assert len(items) == 2
    large = next(item for item in items if len(item["variants"]) == 2)["variants"][0]
    assert large["url_count"] == 2 and large["where_count"] == 1


def test_an_image_on_many_pages_lists_a_sample_and_the_exact_number(
    client, org_with_users, cloud_brand, cloud_database_url, fake_storage_client
):
    org_id, base, _, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand, "https://t.test/fr/")
        path = f"{org_id}/logo.jpg"
        fake_storage_client.store[("site-images", path)] = _upload_bytes().getvalue()
        image = conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash)
               VALUES (%s, %s, 'https://t.test/logo.png', %s, 'logo', 'ffff0000ffff0000', 'ffff0000ffff0000') RETURNING id""",
            (org_id, site, path),
        ).fetchone()["id"]
        for number in range(45):
            page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
                                (org_id, site, f"https://t.test/p{number:02d}")).fetchone()["id"]
            cloud_db.link_image_page(conn, image, page)
    item = client.get(f"{base}/site-images", headers=_headers(client_id)).json()["items"][0]
    assert item["page_count"] == 45 and len(item["pages"]) == cloud_db.SITE_IMAGE_PAGE_SAMPLE and item["pages"][0] == "https://t.test/p00"
    variant = item["variants"][0]
    assert variant["where_count"] == 45 and len(variant["where"]) == cloud_db.SITE_IMAGE_PAGE_SAMPLE


def test_distinct_pages_are_exact_when_every_row_is_whole_and_a_lower_bound_otherwise():
    from nyra.cloud.routes.site_images import _distinct_pages

    whole = [{"pages": ["a", "b"], "page_count": 2}, {"pages": ["b", "c"], "page_count": 2}]
    assert _distinct_pages(3, whole) == 3  # the union of what every row listed
    sampled = [{"pages": ["a"] * 20, "page_count": 45}, {"pages": ["b", "c"], "page_count": 2}]
    assert _distinct_pages(22, sampled) == 45  # the largest row's own number is the best known


@pytest.fixture()
def static_client(tmp_path, monkeypatch):
    """The app serving a fake built interface (no database needed for these routes)."""
    (tmp_path / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "dist" / "assets" / "app-abc123.js").write_text("console.log(1);\n" * 500, encoding="utf-8")
    monkeypatch.setattr("nyra.cloud.api.frontend_dir", lambda: tmp_path)
    settings = CloudSettings(
        database_url="postgresql://unused", supabase_url="https://x.supabase.co", service_role_key="key", jwt_secret=SECRET
    )
    return TestClient(create_app(settings))


def test_hashed_assets_are_cached_for_a_year_and_index_is_not(static_client):
    asset = static_client.get("/assets/app-abc123.js")
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert static_client.get("/").headers["Cache-Control"] == "no-cache"
    assert "immutable" not in static_client.get("/assets/missing.js").headers.get("Cache-Control", "")


def test_big_responses_are_gzipped_and_small_ones_are_not(static_client):
    big = static_client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert big.headers["Content-Encoding"] == "gzip"
    assert big.text == "console.log(1);\n" * 500  # the client decompresses transparently
    assert "Content-Encoding" not in static_client.get("/api/nope", headers={"Accept-Encoding": "gzip"}).headers
    assert "Content-Encoding" not in static_client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "identity"}).headers


def test_labels_are_off_by_default(client, org_with_users):
    _, base, admin_id, _, _ = org_with_users
    assert client.get(f"{base}/labels", headers=_headers(admin_id)).status_code in {404, 405}


def _look(seed: int, count: int):
    """`count` embeddings of one look: unit vectors close to each other, far from another seed's."""
    import numpy as np

    rng = np.random.default_rng(seed)
    centre = rng.normal(size=512)
    rows = centre + 0.05 * rng.normal(size=(count, 512))
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def test_images_get_a_type_and_a_content_from_a_few_examples(labels_on, client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    logos, packshots = _look(1, 6), _look(2, 6)
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand)
        ids = []
        for index, vector in enumerate([*logos, *packshots]):
            ids.append(str(conn.execute(
                """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, site, f"https://t.test/{index}.jpg", f"{org_id}/{index}.jpg", f"h{index}", f"{index:016x}", f"{index:016x}", vector),
            ).fetchone()["id"]))
    found = client.get(f"{base}/labels", headers=_headers(client_id)).json()
    types = {label["name"]: label["id"] for label in found["labels"] if label["kind"] == "type"}
    assert list(types) == ["logo", "packshot", "pictogramme", "autre"]

    def say(label_id, decision, **images):
        return client.post(f"{base}/labels/{label_id}/images", headers=_headers(admin_id), json={"decision": decision, **images})

    assert say(types["logo"], "yes", site_image_ids=ids[:3], reference_ids=[]).status_code == 200
    assert say(types["logo"], "yes", site_image_ids=ids[:1]).status_code == 200  # saying it again changes nothing
    assert client.post(f"{base}/labels/{types['logo']}/images", headers=_headers(client_id),
                       json={"decision": "yes", "site_image_ids": ids[:1]}).status_code == 403
    assert say(types["packshot"], "yes", site_image_ids=ids[6:9]).json()["written"] == 3

    found = client.get(f"{base}/labels/assignments?target=site", headers=_headers(client_id)).json()["assignments"]
    assert [(entry["name"], entry["source"]) for entry in found[ids[0]]] == [("logo", "user")]
    assert [(entry["name"], entry["source"]) for entry in found[ids[5]]] == [("logo", "algo")]
    assert [(entry["name"], entry["source"]) for entry in found[ids[11]]] == [("packshot", "algo")]

    # A type per image: saying packshot for a logo replaces it; a type does not apply to the library.
    say(types["packshot"], "yes", site_image_ids=ids[:1])
    found = client.get(f"{base}/labels/assignments?target=site", headers=_headers(client_id)).json()["assignments"]
    assert [entry["name"] for entry in found[ids[0]]] == ["packshot"]
    assert say(types["logo"], "yes", reference_ids=[str(uuid.uuid4())]).status_code == 400
    assert say(types["logo"], "clear", site_image_ids=ids[:1]).json()["written"] == 0  # nothing said for logo any more
    found_quality = client.get(f"{base}/labels", headers=_headers(client_id)).json()
    assert found_quality["quality"]["packshot"]["examples"] == 4 and found_quality["min_examples"] == 3

    # The sample leaves out what is typed and shows the rest once.
    sample = client.get(f"{base}/labels/sample?count=50", headers=_headers(client_id)).json()
    assert {item["id"] for item in sample["items"]} == set(ids) - set(ids[:3]) - set(ids[6:9]) - {ids[0]}

    # A content label is created by name, learned from examples in the library, and can be refused.
    with cloud_db.connect(cloud_database_url) as conn:
        refs = [str(cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename=f"r{n}.jpg",
                                                    storage_path=f"{org_id}/r{n}.jpg", expiry_date=None, credit=None,
                                                    notes=None, embedding=vector))
                for n, vector in enumerate(logos)]
    carafe = client.post(f"{base}/labels", headers=_headers(admin_id), json={"name": "  Carafe "}).json()
    assert carafe["name"] == "carafe" and client.post(f"{base}/labels", headers=_headers(admin_id), json={"name": "carafe"}).json()["id"] == carafe["id"]
    say(carafe["id"], "yes", reference_ids=refs[:3])
    say(carafe["id"], "no", reference_ids=refs[5:])
    found = client.get(f"{base}/labels/assignments?target=reference", headers=_headers(client_id)).json()["assignments"]
    assert [(e["name"], e["source"]) for e in found[refs[0]]] == [("carafe", "user")]
    assert [(e["name"], e["source"]) for e in found[refs[3]]] == [("carafe", "algo")]
    assert refs[5] not in found  # refused
    assert client.delete(f"{base}/labels/{types['logo']}", headers=_headers(admin_id)).status_code == 404
    assert client.delete(f"{base}/labels/{carafe['id']}", headers=_headers(admin_id)).status_code == 200


def test_collections_file_visuals_and_follow_the_rules(client, org_with_users, fake_storage_client):
    _, base, admin_id, client_id, _ = org_with_users
    admin, member = _headers(admin_id), _headers(client_id)

    def upload(**form):
        return client.post(f"{base}/library/upload", headers=admin, data=form, files=[("files", ("a.jpg", _upload_bytes(), "image/jpeg"))])

    season = client.post(f"{base}/collections", headers=admin, json={"name": "  Winter  26-27 "}).json()["collection"]
    assert season["name"] == "Winter 26-27" and season["parent_id"] is None
    assert client.post(f"{base}/collections", headers=member, json={"name": "x"}).status_code == 403
    assert client.post(f"{base}/collections", headers=admin, json={"name": "winter 26-27"}).status_code == 409
    assert client.post(f"{base}/collections", headers=admin, json={"name": " "}).status_code == 400

    child = client.post(f"{base}/collections", headers=admin, json={"name": "Packshots", "parent_id": season["id"]}).json()["collection"]
    deep = client.post(f"{base}/collections", headers=admin, json={"name": "Classic", "parent_id": child["id"]}).json()["collection"]
    assert client.post(f"{base}/collections", headers=admin, json={"name": "Too deep", "parent_id": deep["id"]}).status_code == 400
    assert client.post(f"{base}/collections", headers=admin, json={"name": "Lost", "parent_id": str(uuid.uuid4())}).status_code == 404

    assert upload(collection_id=child["id"]).status_code == 200
    assert upload(collection_id=str(uuid.uuid4())).status_code == 404
    library = client.get(f"{base}/library", headers=member).json()
    assert library["items"][0]["collection_id"] == child["id"]
    assert [c["name"] for c in library["collections"]] == ["Classic", "Packshots", "Winter 26-27"]

    assert client.post(f"{base}/library/collection", headers=admin, json={"filenames": ["a.jpg"], "collection_id": season["id"]}).json() == {"updated": 1}
    assert client.put(f"{base}/collections/{season['id']}", headers=admin, json={"name": "Winter 2026"}).status_code == 200
    # Deleting a collection removes its sub-collections but never the visuals.
    assert client.delete(f"{base}/collections/{season['id']}", headers=admin).status_code == 200
    library = client.get(f"{base}/library", headers=member).json()
    assert library["collections"] == [] and library["items"][0]["collection_id"] is None
    assert client.delete(f"{base}/collections/{season['id']}", headers=admin).status_code == 404


def test_collections_move_under_another_within_three_levels(client, org_with_users):
    _, base, admin_id, client_id, _ = org_with_users
    admin = _headers(admin_id)

    def add(name, parent=None):
        return client.post(f"{base}/collections", headers=admin, json={"name": name, "parent_id": parent}).json()["collection"]["id"]

    def move(collection, parent):
        return client.post(f"{base}/collections/{collection}/move", headers=admin, json={"parent_id": parent})

    hub, other, season = add("Lifestyle"), add("Products"), add("Winter 25-26")
    inner = add("Packshots", season)  # season + inner = a subtree two levels high
    assert move(season, hub).status_code == 200  # a top-level collection goes under a hub, its sub-collection follows
    parents = {c["id"]: c["parent_id"] for c in client.get(f"{base}/library", headers=admin).json()["collections"]}
    assert parents[season] == hub and parents[inner] == season
    assert client.post(f"{base}/collections/{season}/move", headers=_headers(client_id), json={"parent_id": other}).status_code == 403
    assert move(season, season).status_code == 400 and move(hub, inner).status_code == 400  # never into itself or below
    assert move(hub, other).status_code == 400  # hub, season and inner would be on four levels
    assert move(season, str(uuid.uuid4())).status_code == 404 and move(str(uuid.uuid4()), other).status_code == 404
    assert move(season, None).status_code == 200  # back to the top
    add("Winter 25-26", hub)
    assert move(season, hub).status_code == 409  # a sibling already has that name


def _names(client, base, headers, parent=None):
    """The names of the collections under one parent, in the order the library gives them."""
    return [c["name"] for c in client.get(f"{base}/library", headers=headers).json()["collections"] if c["parent_id"] == parent]


def test_collections_keep_the_order_chosen_and_new_ones_come_last(client, org_with_users):
    org_id, base, admin_id, client_id, _ = org_with_users
    admin, member = _headers(admin_id), _headers(client_id)

    def add(name, parent=None):
        return client.post(f"{base}/collections", headers=admin, json={"name": name, "parent_id": parent}).json()["collection"]["id"]

    def order(ids, parent=None, headers=admin):
        return client.put(f"{base}/collections/order", headers=headers, json={"parent_id": parent, "ids": ids})

    zeta, alpha, mid = add("Zeta"), add("Alpha"), add("Mid")
    assert _names(client, base, member) == ["Zeta", "Alpha", "Mid"]  # the order of creation, no longer the alphabet
    assert order([mid, zeta, alpha]).status_code == 200
    assert _names(client, base, member) == ["Mid", "Zeta", "Alpha"]  # for every member
    sub_b, sub_a = add("B", alpha), add("A", alpha)
    assert order([sub_a, sub_b], parent=alpha).status_code == 200
    assert _names(client, base, member, alpha) == ["A", "B"] and _names(client, base, member) == ["Mid", "Zeta", "Alpha"]  # a level at a time
    assert client.put(f"{base}/collections/{zeta}", headers=admin, json={"name": "Omega"}).status_code == 200
    assert _names(client, base, member) == ["Mid", "Omega", "Alpha"]  # a rename keeps its place
    assert client.post(f"{base}/collections/{mid}/move", headers=admin, json={"parent_id": alpha}).status_code == 200
    assert _names(client, base, member, alpha) == ["A", "B", "Mid"] and _names(client, base, member) == ["Omega", "Alpha"]  # a move lands last
    assert order([alpha, zeta]).status_code == 200 and _names(client, base, member) == ["Alpha", "Omega"]
    assert client.get(f"/api/orgs/{org_id}/audit", headers=admin).json()["events"][0]["action"] == "collection.reorder"


def test_collections_refuse_an_order_that_is_not_the_whole_level(client, org_with_users):
    org_id, base, admin_id, client_id, _ = org_with_users
    admin = _headers(admin_id)
    ids = [client.post(f"{base}/collections", headers=admin, json={"name": name}).json()["collection"]["id"] for name in ("A", "B", "C")]
    sub = client.post(f"{base}/collections", headers=admin, json={"name": "S", "parent_id": ids[0]}).json()["collection"]["id"]

    def order(sent, parent=None, headers=admin):
        return client.put(f"{base}/collections/order", headers=headers, json={"parent_id": parent, "ids": sent})

    assert order(ids[::-1], headers=_headers(client_id)).status_code == 403  # a client does not order
    for stale in (ids[:2], [*ids, sub], [ids[0], ids[0], ids[1], ids[2]], [*ids[:2], str(uuid.uuid4())]):
        assert order(stale).status_code == 409, stale  # one missing, one of another level, a repeated one, an unknown one
    assert order([sub], parent=ids[1]).status_code == 409  # a collection under another parent
    assert order([sub], parent=str(uuid.uuid4())).status_code == 404 and order([]).status_code == 422
    assert _names(client, base, admin) == ["A", "B", "C"]  # nothing was written
    other = client.post(f"/api/orgs/{org_id}/brands", headers=admin, json={"name": "Autre"}).json()["brand"]
    assert client.put(f"/api/orgs/{org_id}/brands/{other['id']}/collections/order", headers=admin,
                      json={"ids": ids}).status_code == 409  # another brand's collections are not this brand's level


def test_the_migration_ranks_collections_by_name_once_and_never_resets_a_chosen_order(client, org_with_users, cloud_database_url):
    _, base, admin_id, _, _ = org_with_users
    admin = _headers(admin_id)
    ids = {name: client.post(f"{base}/collections", headers=admin, json={"name": name}).json()["collection"]["id"] for name in ("Zeta", "Alpha", "Mid")}
    sql = (Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20261028000042_collection_position.sql").read_text(encoding="utf-8")
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("ALTER TABLE collections DROP COLUMN position")  # as it was before the migration (rolled back below)
        conn.execute(sql)
        ranked = {row["name"]: row["position"] for row in conn.execute("SELECT name, position FROM collections WHERE id = ANY(%s)", (list(ids.values()),)).fetchall()}
        assert ranked == {"Alpha": 0, "Mid": 1, "Zeta": 2}  # the alphabet of before
        conn.execute("UPDATE collections SET position = 7 WHERE id = %s", (ids["Zeta"],))
        conn.execute(sql)  # again: nothing is ranked a second time
        assert conn.execute("SELECT position FROM collections WHERE id = %s", (ids["Zeta"],)).fetchone()["position"] == 7
        conn.rollback()


def test_back_office_lists_are_for_platform_admins_who_also_look_after_any_organizations_people(
        client, org_with_users, cloud_database_url, monkeypatch):
    org_id, _, admin_id, client_id, outsider_id = org_with_users  # admin_id is a platform admin
    monkeypatch.setattr("nyra.cloud.routes.staff.find_or_invite_user", lambda *args: (str(outsider_id), True))
    for path in ("organizations", "users", "admins"):
        assert client.get(f"/api/staff/{path}").status_code == 401
        assert client.get(f"/api/staff/{path}", headers=_headers(client_id)).status_code == 403

    orgs = client.get("/api/staff/organizations", headers=_headers(admin_id)).json()["organizations"]
    assert str(org_id) in {o["id"] for o in orgs}
    users = {u["user_id"]: u for u in client.get("/api/staff/users", headers=_headers(admin_id)).json()["users"]}
    assert users[str(admin_id)]["staff"] is True and users[str(client_id)]["memberships"][0]["role"] == "client"

    # A new client: its first brand is billed to the platform admin who created it.
    made = client.post("/api/staff/organizations", headers=_headers(admin_id),
                       json={"name": "Hennessy", "admin_email": "boss@hennessy.com"})
    assert made.status_code == 200 and made.json()["slug"] == "hennessy" and made.json()["invited"] is True
    assert client.post("/api/staff/organizations", headers=_headers(admin_id),
                       json={"name": "Hennessy", "admin_email": "boss@hennessy.com"}).status_code == 409
    new = next(o for o in client.get("/api/staff/organizations", headers=_headers(admin_id)).json()["organizations"]
               if o["slug"] == "hennessy")
    assert new["members"] == 1 and new["brands"][0]["created_by"] == f"{admin_id}@x.com"

    # The people of an organization the platform admin is not a member of.
    members = f"/api/orgs/{new['id']}/members"
    assert client.get(members, headers=_headers(client_id)).status_code == 403
    assert len(client.get(members, headers=_headers(admin_id)).json()["members"]) == 1
    assert client.get(f"/api/orgs/{uuid.uuid4()}/members", headers=_headers(admin_id)).status_code == 404

    # Platform admins: add, list, remove; never oneself.
    assert client.post("/api/staff/admins", headers=_headers(admin_id), json={"email": "pas-un-mail"}).status_code == 400
    assert client.post("/api/staff/admins", headers=_headers(admin_id), json={"email": "o@x.com"}).status_code == 200
    admins = client.get("/api/staff/admins", headers=_headers(admin_id)).json()["admins"]
    assert {a["user_id"] for a in admins} == {str(admin_id), str(outsider_id)}
    assert client.delete(f"/api/staff/admins/{admin_id}", headers=_headers(admin_id)).status_code == 409
    assert client.delete(f"/api/staff/admins/{outsider_id}", headers=_headers(admin_id)).status_code == 200
    assert client.delete(f"/api/staff/admins/{outsider_id}", headers=_headers(admin_id)).status_code == 404


def test_a_logo_upload_becomes_a_small_png_and_unreadable_files_are_refused():
    from nyra.cloud.routes.brands import LOGO_SIZE, _logo_data_uri

    big = io.BytesIO()
    Image.new("RGBA", (900, 300), (200, 10, 10, 255)).save(big, format="PNG")
    uri = _logo_data_uri(big.getvalue())
    assert uri.startswith("data:image/png;base64,") and len(uri) < 20_000
    shrunk = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert max(shrunk.size) == LOGO_SIZE and shrunk.size[0] > shrunk.size[1]  # proportions kept
    with pytest.raises(HTTPException) as refused:
        _logo_data_uri(b"not an image")
    assert refused.value.status_code == 400


def test_admins_set_and_remove_the_logo_of_their_brand_and_it_comes_with_the_brands(client, org_with_users):
    org_id, base, admin_id, client_id, _ = org_with_users
    png = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 10, 200)).save(png, format="PNG")
    files = {"file": ("logo.png", png.getvalue(), "image/png")}
    assert client.put(f"{base}/logo", headers=_headers(client_id), files=files).status_code == 403
    done = client.put(f"{base}/logo", headers=_headers(admin_id), files=files)
    assert done.status_code == 200 and done.json()["logo"].startswith("data:image/png;base64,")
    assert client.put(f"{base}/logo", headers=_headers(admin_id), files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    listed = client.get("/api/orgs", headers=_headers(client_id)).json()["organizations"][0]["brands"]
    assert listed[0]["logo"] == done.json()["logo"]
    assert client.delete(f"{base}/logo", headers=_headers(client_id)).status_code == 403
    assert client.delete(f"{base}/logo", headers=_headers(admin_id)).status_code == 200
    assert client.get("/api/orgs", headers=_headers(client_id)).json()["organizations"][0]["brands"][0]["logo"] is None


def test_a_photo_marked_removed_says_what_the_last_read_found(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="v.jpg", storage_path=f"{org_id}/v.jpg",
                                              expiry_date="2026-10-01", credit=None, notes=None, phash=same_hash, dhash=same_hash)
        site = _site(conn, org_id, cloud_brand)
        image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, 'https://target.test/v.jpg', %s, %s, %s) RETURNING id",
                             (org_id, site, f"{org_id}/v.jpg", same_hash, same_hash)).fetchone()["id"]
        cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut")])
        page = conn.execute("INSERT INTO pages (org_id, site_id, url, status, http_status) VALUES (%s, %s, 'https://target.test/a', 'done', 200) RETURNING id", (org_id, site)).fetchone()["id"]
        cloud_db.link_image_page(conn, image, page)

    def removal():
        return client.get(f"{base}/matches?within_days=3650", headers=_headers(admin_id)).json()["confirmed"][0]["hits"][0]["removal"]

    def read(*, seen: bool, status: int = 200):
        """A read of the site that started after the decision: the page read again, the image seen on it or not."""
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM crawl_runs WHERE site_id = %s", (site,))
            conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at) VALUES (%s, %s, 'done', now() + interval '1 hour')", (org_id, site))
            conn.execute("UPDATE pages SET crawled_at = now() + interval '2 hours', http_status = %s WHERE id = %s", (status, page))
            conn.execute("UPDATE site_images SET last_seen = now() + interval '2 hours' WHERE id = %s AND %s", (image, seen))

    review = {"reference_id": str(ref), "site_image_ids": [str(image)], "decision": "traite"}
    assert removal() is None  # not marked removed
    assert client.post(f"{base}/reviews", headers=_headers(admin_id), json=review).status_code == 200
    assert removal() == "pending"  # no read since the mark
    read(seen=False)
    assert removal() == "gone"
    read(seen=False, status=500)
    assert removal() == "unread"  # the page failed to load: nothing is known about the image
    read(seen=True)
    assert removal() == "online"


def test_presence_lists_the_pages_of_a_visual_and_stays_inside_its_brand(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="v.jpg", storage_path=f"{org_id}/v.jpg",
                                              expiry_date=None, credit=None, notes=None, phash=same_hash, dhash=same_hash)
        site = _site(conn, org_id, cloud_brand)
        image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, 'https://target.test/v.jpg', %s, %s, %s) RETURNING id",
                             (org_id, site, f"{org_id}/v.jpg", same_hash, same_hash)).fetchone()["id"]
        cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut")])
        page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, 'https://target.test/blogs/a?x=1', 'done') RETURNING id", (org_id, site)).fetchone()["id"]
        cloud_db.link_image_page(conn, image, page)

    data = client.get(f"{base}/visuals/{ref}/presence", headers=_headers(client_id)).json()
    assert data["totals"] == {"still": 0, "gone": 0, "unknown": 1, "marked": 0, "pages": 1}  # no finished read of the site yet
    assert data["pages"] == [{"url": "https://target.test/blogs/a?x=1", "site_id": str(site), "section": "/blogs", "state": "unknown", "marked": False, "checked": False, "walled": False}]
    assert data["total"] == 1 and data["versions"][0]["sections"][0]["section"] == "/blogs"
    assert client.get(f"{base}/visuals/{ref}/presence?state=still", headers=_headers(client_id)).json()["pages"] == []
    assert client.get(f"{base}/visuals/{ref}/presence?state=nope", headers=_headers(client_id)).status_code == 422
    assert client.get(f"{base}/visuals/{uuid.uuid4()}/presence", headers=_headers(client_id)).status_code == 404

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.get(f"/api/orgs/{org_id}/brands/{other['id']}/visuals/{ref}/presence", headers=_headers(admin_id)).status_code == 404


def test_visuals_to_replace_lists_what_a_person_confirmed(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    ids = {}
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand)
        for name in ("confirmed.jpg", "undecided.jpg"):
            ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename=name, storage_path=f"{org_id}/{name}",
                                                  thumb_path=f"{org_id}/thumbs/{name}.jpg", expiry_date="2026-10-01", credit=None, notes=None,
                                                  phash=same_hash, dhash=same_hash)
            image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                 (org_id, site, f"https://target.test/{name}", f"{org_id}/{name}", same_hash, same_hash)).fetchone()["id"]
            cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut")])
            page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id", (org_id, site, f"https://target.test/blogs/{name}")).fetchone()["id"]
            cloud_db.link_image_page(conn, image, page)
            ids[name] = (ref, image)

    assert client.get(f"{base}/visuals", headers=_headers(client_id)).json()["visuals"] == []  # nothing confirmed yet
    ref, image = ids["confirmed.jpg"]
    review = {"reference_id": str(ref), "site_image_ids": [str(image)], "decision": "retenu"}
    assert client.post(f"{base}/reviews", headers=_headers(client_id), json=review).status_code == 200

    visuals = client.get(f"{base}/visuals", headers=_headers(client_id)).json()["visuals"]
    assert [item["filename"] for item in visuals] == ["confirmed.jpg"]
    item = visuals[0]
    assert (item["pages"], item["unknown"], item["still"], item["replaced"]) == (1, 1, 0, False)  # not read again yet: not replaced
    assert item["status"] == "expire" and item["expiry_date"] == "2026-10-01" and item["thumb"].startswith("https://fake-storage.test/refs/")

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.get(f"/api/orgs/{org_id}/brands/{other['id']}/visuals", headers=_headers(admin_id)).json()["visuals"] == []


def test_a_page_check_is_asked_for_known_pages_of_the_brand_only(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand)
        for path in ("a", "b"):
            conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done')", (org_id, site, f"https://target.test/{path}"))
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE brand_id = %s AND status IN ('queued', 'running')", (cloud_brand,))
    check = f"{base}/jobs/check"
    assert client.post(check, headers=_headers(client_id), json={"urls": ["https://target.test/a"]}).status_code == 403  # a reading is for administrators
    assert client.post(check, headers=_headers(admin_id), json={"urls": []}).status_code == 422
    assert client.post(check, headers=_headers(admin_id), json={"urls": [f"https://target.test/{n}" for n in range(51)]}).status_code == 422  # 50 at most
    assert client.post(check, headers=_headers(admin_id), json={"urls": ["https://target.test/a", "https://elsewhere.test/x"]}).status_code == 400
    done = client.post(check, headers=_headers(admin_id), json={"urls": ["https://target.test/a", "https://target.test/a"]})
    assert done.status_code == 200
    job = done.json()["job"]
    assert job["kind"] == "check" and job["params"] == {"pages": {str(site): ["https://target.test/a"]}}  # a repeated address is read once
    assert client.post(check, headers=_headers(admin_id), json={"urls": ["https://target.test/b"]}).status_code == 409  # one check at a time

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.post(f"/api/orgs/{org_id}/brands/{other['id']}/jobs/check", headers=_headers(admin_id), json={"urls": ["https://target.test/b"]}).status_code == 400


def test_a_page_check_accepts_an_address_that_is_a_page_of_two_sites_of_the_brand(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        first = _site(conn, org_id, cloud_brand)
        second = cloud_db.create_site(conn, org_id=org_id, brand_id=cloud_brand, url="https://twin.test/")
        for site in (first, second):
            conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, 'https://shared.test/p', 'done')", (org_id, site))
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE brand_id = %s AND status IN ('queued', 'running')", (cloud_brand,))
    done = client.post(f"{base}/jobs/check", headers=_headers(admin_id), json={"urls": ["https://shared.test/p"]})
    assert done.status_code == 200 and sorted(done.json()["job"]["params"]["pages"]) == sorted([str(first), str(second)])


def test_pages_are_marked_replaced_for_the_visual_that_shows_them(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, client_id, _ = org_with_users
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=cloud_brand, filename="v.jpg", storage_path=f"{org_id}/v.jpg",
                                              expiry_date=None, credit=None, notes=None, phash=same_hash, dhash=same_hash)
        site = _site(conn, org_id, cloud_brand)
        image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, 'https://target.test/v.jpg', %s, %s, %s) RETURNING id",
                             (org_id, site, f"{org_id}/v.jpg", same_hash, same_hash)).fetchone()["id"]
        cloud_db.write_matches(conn, org_id, [(ref, image, "phash", 1.0, "haut")])
        for path in ("a", "b"):
            page = conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id", (org_id, site, f"https://target.test/blogs/{path}")).fetchone()["id"]
            cloud_db.link_image_page(conn, image, page)
        conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, 'https://target.test/elsewhere', 'done')", (org_id, site))
    mark = f"{base}/visuals/{ref}/replaced"
    shown = ["https://target.test/blogs/a"]
    assert client.post(mark, headers=_headers(client_id), json={"urls": shown, "replaced": True}).json() == {"ok": True, "pages": 1}  # any member
    assert client.post(mark, headers=_headers(admin_id), json={"urls": ["https://target.test/elsewhere"], "replaced": True}).status_code == 400  # a page of the brand that does not show it
    assert client.post(mark, headers=_headers(admin_id), json={"urls": [], "replaced": True}).status_code == 422
    assert client.post(f"{base}/visuals/{uuid.uuid4()}/replaced", headers=_headers(admin_id), json={"urls": shown, "replaced": True}).status_code == 404

    data = client.get(f"{base}/visuals/{ref}/presence", headers=_headers(client_id)).json()
    assert data["totals"]["marked"] == 1 and {page["url"]: page["marked"] for page in data["pages"]} == {"https://target.test/blogs/a": True, "https://target.test/blogs/b": False}
    only = client.get(f"{base}/visuals/{ref}/presence?marked=false", headers=_headers(client_id)).json()
    assert [page["url"] for page in only["pages"]] == ["https://target.test/blogs/b"] and only["total"] == 1
    assert client.post(mark, headers=_headers(client_id), json={"urls": shown, "replaced": False}).status_code == 200
    assert client.get(f"{base}/visuals/{ref}/presence", headers=_headers(client_id)).json()["totals"]["marked"] == 0

    other = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Autre"}).json()["brand"]
    assert client.post(f"/api/orgs/{org_id}/brands/{other['id']}/visuals/{ref}/replaced", headers=_headers(admin_id), json={"urls": shown, "replaced": True}).status_code == 404


# --- quotas (AXE-532) -------------------------------------------------------------

def _limits(client, staff_id, org_id, **limits):
    return client.put(f"/api/staff/organizations/{org_id}/quotas", headers=_headers(staff_id), json={"limits": limits})


def _other_org(conn, admin_id):
    org = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:10]}")
    cloud_db.add_membership(conn, user_id=admin_id, org_id=org, role="admin")
    return org, f"/api/orgs/{org}"


def test_only_the_nyra_team_changes_an_organizations_limits_and_bad_values_are_refused(client, org_with_users):
    org_id, _, admin_id, client_id, _ = org_with_users
    assert _limits(client, client_id, org_id, brands=3).status_code == 403
    assert client.put(f"/api/orgs/{org_id}/settings", headers=_headers(admin_id), json={"overrides": {"quota": {"brands": 99}}}).json() == {"overrides": {}}
    assert _limits(client, admin_id, org_id, brands=3, storage_gb=1.5).json() == {"overrides": {"brands": 3, "storage_gb": 1.5}}
    assert _limits(client, admin_id, org_id, brands=-1).status_code == 400
    assert _limits(client, admin_id, org_id, nonsense=1).status_code == 400
    assert _limits(client, admin_id, uuid.uuid4(), brands=1).status_code == 404
    mine = next(o for o in client.get("/api/staff/usage", headers=_headers(admin_id)).json()["organizations"] if o["id"] == str(org_id))
    assert {i["key"]: i["limit"] for i in mine["items"]}["brands"] == 3 and mine["overrides"] == {"brands": 3, "storage_gb": 1.5}
    assert _limits(client, admin_id, org_id).json() == {"overrides": {}}  # empty: back to the defaults


def test_an_organization_at_its_limit_cannot_grow_and_the_others_are_not_affected(
        client, org_with_users, cloud_database_url, monkeypatch):
    org_id, base, admin_id, client_id, _ = org_with_users
    from nyra import netguard

    monkeypatch.setattr(netguard, "check_url", lambda url: None)
    with cloud_db.connect(cloud_database_url) as conn:
        other, other_base = _other_org(conn, admin_id)
        other_brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (other,)).fetchone()["id"]
    _limits(client, admin_id, org_id, brands=1, sites_per_brand=1, references=1, members=2)

    refused = client.post(f"/api/orgs/{org_id}/brands", headers=_headers(admin_id), json={"name": "Deux"})
    assert refused.status_code == 403 and refused.json()["detail"].startswith("Limite atteinte")
    assert client.post(f"{other_base}/brands", headers=_headers(admin_id), json={"name": "Deux"}).status_code == 200

    site = {"url": "https://a.test/"}
    assert client.post(f"{base}/sites", headers=_headers(admin_id), json=site).status_code == 200
    assert client.post(f"{base}/sites", headers=_headers(admin_id), json={"url": "https://b.test/"}).status_code == 403
    assert client.post(f"{other_base}/brands/{other_brand}/sites", headers=_headers(admin_id), json={"url": "https://c.test/"}).status_code == 200

    assert _upload(client, base, admin_id, "a.jpg").json()["saved"] == ["a.jpg"]
    assert _upload(client, base, admin_id, "a.jpg", replace=True).json()["replaced"] == ["a.jpg"]  # replacing adds nothing
    assert _upload(client, base, admin_id, "a.jpg").status_code == 403  # a copy under another name adds one
    assert _upload(client, base, admin_id, "b.jpg").status_code == 403
    assert _upload(client, f"{other_base}/brands/{other_brand}", admin_id, "b.jpg").json()["saved"] == ["b.jpg"]

    assert client.post(f"/api/orgs/{org_id}/members", headers=_headers(admin_id), json={"email": "x@y.co"}).status_code == 403
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reference_images WHERE org_id = %s", (org_id,)).fetchone()["n"] == 1


def test_a_read_is_refused_once_the_months_pages_are_used_and_the_page_of_usage_says_so(
        client, org_with_users, cloud_brand, cloud_database_url):
    from nyra.cloud import usage as cloud_usage

    org_id, base, admin_id, client_id, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        _site(conn, org_id, cloud_brand)
        other, other_base = _other_org(conn, admin_id)
    _limits(client, admin_id, org_id, pages_per_month=100)
    usage = f"/api/orgs/{org_id}/usage"
    assert client.get(usage, headers=_headers(client_id)).status_code == 403  # for administrators
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_usage.record(conn, org_id, pages=80)
    pages = next(i for i in client.get(usage, headers=_headers(admin_id)).json()["items"] if i["key"] == "pages")
    assert pages == {"key": "pages", "label": "Pages lues ce mois-ci", "used": 80, "limit": 100, "level": "warning"}
    assert client.post(f"{base}/jobs/crawl", headers=_headers(admin_id), json={}).status_code == 200  # 20 pages are left
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE org_id = %s", (org_id,))  # one crawl at a time
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_usage.record(conn, org_id, pages=20)
    refused = client.post(f"{base}/jobs/crawl", headers=_headers(admin_id), json={})
    assert refused.status_code == 403 and "pages lues ce mois-ci" in refused.json()["detail"]
    assert client.post(f"{base}/jobs/check", headers=_headers(admin_id), json={"urls": ["https://target.test/"]}).status_code == 403
    assert next(i for i in client.get(usage, headers=_headers(admin_id)).json()["items"] if i["key"] == "pages")["level"] == "reached"
    other_pages = next(i for i in client.get(f"{other_base}/usage", headers=_headers(admin_id)).json()["items"] if i["key"] == "pages")
    assert other_pages["used"] == 0 and other_pages["level"] == "ok"
    texts = " ".join([i["label"] for i in client.get(usage, headers=_headers(admin_id)).json()["items"]] + [refused.json()["detail"], cloud_usage.CAPPED]).lower()
    assert not any(word in texts for word in ("factur", "prix", "tarif", "€", "abonnement", "payer"))  # usage and limit, never money


def test_invitations_stop_at_the_days_limit_but_an_existing_account_sends_no_mail(
        client, org_with_users, cloud_database_url, monkeypatch):
    from types import SimpleNamespace

    org_id, _, admin_id, client_id, outsider_id = org_with_users
    base = f"/api/orgs/{org_id}/members"

    def invite(email, options):
        with cloud_db.connect(cloud_database_url) as conn:
            user = conn.execute("INSERT INTO auth.users (email) VALUES (%s) RETURNING id", (email,)).fetchone()["id"]
        return SimpleNamespace(user=SimpleNamespace(id=user))

    fake = SimpleNamespace(auth=SimpleNamespace(admin=SimpleNamespace(invite_user_by_email=invite)))
    monkeypatch.setattr("nyra.cloud.storage.get_client", lambda *args: fake)
    _limits(client, admin_id, org_id, emails_per_day=1)
    outsider, first, second = f"{outsider_id}@x.com", f"{uuid.uuid4().hex}@x.com", f"{uuid.uuid4().hex}@x.com"
    assert client.post(base, headers=_headers(admin_id), json={"email": outsider}).json()["invited"] is False  # has an account: no mail
    assert client.post(base, headers=_headers(admin_id), json={"email": first}).json()["invited"] is True
    late = client.post(base, headers=_headers(admin_id), json={"email": second})
    assert late.status_code == 429 and "Réessayez demain" in late.json()["detail"]
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT 1 FROM auth.users WHERE email = %s", (second,)).fetchone() is None  # nothing was sent


def test_adopting_site_images_is_refused_at_the_library_or_storage_limit(client, org_with_users, cloud_brand, cloud_database_url):
    org_id, base, admin_id, _, _ = org_with_users
    with cloud_db.connect(cloud_database_url) as conn:
        site = _site(conn, org_id, cloud_brand)
        image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash) VALUES (%s, %s, 'https://target.test/a.jpg', 'x', 'h') RETURNING id",
                             (org_id, site)).fetchone()["id"]
    body = {"site_image_ids": [str(image)]}
    for limit in ({"references": 0}, {"storage_gb": 0}):
        _limits(client, admin_id, org_id, **limit)
        refused = client.post(f"{base}/site-images/adopt", headers=_headers(admin_id), json=body)
        assert refused.status_code == 403 and refused.json()["detail"].startswith("Limite atteinte")
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reference_images WHERE org_id = %s", (org_id,)).fetchone()["n"] == 0


def test_staff_routes_for_quotas_refuse_an_organization_admin_who_is_not_staff(client, org_with_users, cloud_database_url):
    org_id, *_ = org_with_users
    plain = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (plain, f"{plain}@x.com"))
        cloud_db.add_membership(conn, user_id=plain, org_id=org_id, role="admin")
    assert _limits(client, plain, org_id, brands=99).status_code == 403
    assert client.get("/api/staff/usage", headers=_headers(plain)).status_code == 403
    assert client.get(f"/api/orgs/{org_id}/usage", headers=_headers(plain)).status_code == 200  # its own usage is its to see


def test_limits_lowered_under_the_usage_still_allow_replacing_and_an_invalid_row_falls_back_to_the_defaults(
        client, org_with_users, cloud_database_url, caplog):
    import logging

    org_id, base, admin_id, _, _ = org_with_users
    assert _upload(client, base, admin_id, "a.jpg").json()["saved"] == ["a.jpg"]
    with caplog.at_level(logging.INFO, logger="nyra.usage"):
        _limits(client, admin_id, org_id, references=0)
    assert any(f"changed by {admin_id}" in record.getMessage() for record in caplog.records)  # who changed the limits
    assert _upload(client, base, admin_id, "a.jpg", replace=True).json()["replaced"] == ["a.jpg"]  # nothing added: allowed
    assert _upload(client, base, admin_id, "b.jpg").status_code == 403
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE organizations SET quota_overrides = '{\"brands\": \"many\"}' WHERE id = %s", (org_id,))
    listed = client.get("/api/staff/usage", headers=_headers(admin_id))
    assert listed.status_code == 200  # one bad row does not stop the back office
    mine = next(o for o in listed.json()["organizations"] if o["id"] == str(org_id))
    assert mine["overrides"] == {} and listed.json()["truncated"] is False


def test_an_invitation_the_provider_refused_gives_its_place_back(client, org_with_users, cloud_database_url, monkeypatch):
    from types import SimpleNamespace

    org_id, _, admin_id, _, _ = org_with_users

    def refuse(email, options):
        raise RuntimeError("provider down")

    fake = SimpleNamespace(auth=SimpleNamespace(admin=SimpleNamespace(invite_user_by_email=refuse)))
    monkeypatch.setattr("nyra.cloud.storage.get_client", lambda *args: fake)
    _limits(client, admin_id, org_id, emails_per_day=1)
    for _ in range(2):  # the same place is available again: a 502 each time, never a 429
        failed = client.post(f"/api/orgs/{org_id}/members", headers=_headers(admin_id), json={"email": f"{uuid.uuid4().hex}@x.com"})
        assert failed.status_code == 502
