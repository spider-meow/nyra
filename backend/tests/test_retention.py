"""AXE-533: the working copy of an image not seen for two months is deleted, nothing else; and the size of the project is watched."""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from nyra import fetch
from nyra.cloud import alerts, retention
from nyra.cloud import db as cloud_db
from nyra.cloud.store import CloudCrawlStore

HASH = "a" * 16


def _image(conn, fake, org_id, site_id, name, days, work=None):
    """A site image last seen `days` ago, with its working copy and thumbnail in storage."""
    work, thumb = work or f"{org_id}/work/{name}.jpg", f"{org_id}/thumbs/{name}.jpg"
    for path in (work, thumb):
        fake.store[("site-images", path)] = b"jpeg"
    return conn.execute(
        """INSERT INTO site_images (org_id, site_id, url, storage_path, thumb_path, content_hash, phash, dhash, last_seen)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() - make_interval(days => %s)) RETURNING id""",
        (org_id, site_id, f"https://t.test/{name}.jpg", work, thumb, name, HASH, HASH, days),
    ).fetchone()["id"]


def _read(conn, org_id, site_id, days_ago):
    conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at, finished_at) VALUES (%s, %s, 'done', now() - make_interval(days => %s), now() - make_interval(days => %s))",
                 (org_id, site_id, days_ago, days_ago))


def test_only_the_working_copy_of_an_image_gone_for_two_months_is_deleted(cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    fake, ids = fake_storage_client, {}
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        quiet = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://quiet.test/")
        ref = cloud_db.upsert_reference_image(conn, org_id=cloud_org, brand_id=cloud_brand, filename="r.jpg", storage_path=f"{cloud_org}/r.jpg",
                                              expiry_date=None, credit=None, notes=None, phash=HASH, dhash=HASH)
        _read(conn, cloud_org, site, 5)  # read last week
        _read(conn, cloud_org, quiet, 100)  # not read for over two months: says nothing about what is on it
        for name, days in (("gone", 90), ("matched", 90), ("located", 90), ("recent", 10), ("twin_old", 90), ("twin_new", 10)):
            shared = f"{cloud_org}/work/twin.jpg" if name.startswith("twin") else None
            ids[name] = _image(conn, fake, cloud_org, site, name, days, work=shared)
        ids["quiet"] = _image(conn, fake, cloud_org, quiet, "quiet", 90)
        cloud_db.write_matches(conn, cloud_org, [(ref, ids["matched"], "phash", 1.0, "haut")])
        conn.execute("INSERT INTO reference_locations (reference_id, site_image_id, org_id, tier, inliers, ref_coverage, site_coverage) VALUES (%s, %s, %s, 'same', 10, 0.5, 0.5)",
                     (ref, ids["located"], cloud_org))

    done = retention.purge_images(cloud_database_url, fake)
    assert done == {"batches": 1, "files": 1}  # "gone"'s working copy; the twins share one file that the recent one still needs

    with cloud_db.connect(cloud_database_url) as conn:
        rows = {row["content_hash"]: row for row in conn.execute("SELECT content_hash, storage_path, thumb_path, phash, embedding FROM site_images WHERE org_id = %s", (cloud_org,)).fetchall()}
        audit = conn.execute("SELECT detail FROM audit_log WHERE action = 'images.purge' AND org_id = %s", (cloud_org,)).fetchall()
    assert {name for name, row in rows.items() if row["storage_path"] is None} == {"gone", "twin_old"}
    assert all(row["thumb_path"] and row["phash"] for row in rows.values())  # the row, its hashes and its thumbnail stay
    assert ("site-images", f"{cloud_org}/work/gone.jpg") not in fake.store and ("site-images", f"{cloud_org}/thumbs/gone.jpg") in fake.store
    for kept in ("matched", "located", "recent", "quiet"):
        assert ("site-images", f"{cloud_org}/work/{kept}.jpg") in fake.store
    assert ("site-images", f"{cloud_org}/work/twin.jpg") in fake.store
    assert [row["detail"] for row in audit] == [{"images": 2, "files": 1, "months": 2}]
    assert retention.purge_images(cloud_database_url, fake) == {"batches": 0, "files": 0}  # nothing left to do

    # The image is back on the site: it is read again like a new one, and the file returns.
    store = CloudCrawlStore(org_id=cloud_org, site_id=site, database_url=cloud_database_url, storage_client=fake)
    assert store.known_images(["https://t.test/gone.jpg", "https://t.test/recent.jpg"]) == {"https://t.test/recent.jpg": ids["recent"]}
    assert store.image_by_content_hash("gone") is None


def test_a_run_is_bounded_and_a_failing_delete_leaves_the_rows_committed(cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch, caplog):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        _read(conn, cloud_org, site, 1)
        for index in range(5):
            _image(conn, fake_storage_client, cloud_org, site, f"old{index}", 90)
    monkeypatch.setattr(retention, "BATCH", 2)
    monkeypatch.setattr(retention, "MAX_BATCHES", 2)
    assert retention.purge_images(cloud_database_url, fake_storage_client)["batches"] == 2  # 4 of 5; tomorrow's run takes the last
    assert retention.purge_images(cloud_database_url, fake_storage_client)["batches"] == 1

    with cloud_db.connect(cloud_database_url) as conn:
        _image(conn, fake_storage_client, cloud_org, site, "later", 90)

    class Broken:
        class storage:  # noqa: N801 - the shape of the client
            @staticmethod
            def from_(bucket):
                raise RuntimeError("storage unavailable")

    with caplog.at_level("WARNING", logger="nyra.retention"):
        assert retention.purge_images(cloud_database_url, Broken)["files"] == 0
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT storage_path FROM site_images WHERE content_hash = 'later'").fetchone()["storage_path"] is None
    assert "orphans left behind" in caplog.text


def test_an_image_read_again_gets_its_working_copy_back(cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    from test_cloud_db import _jpeg

    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        _read(conn, cloud_org, site, 1)
    store = CloudCrawlStore(org_id=cloud_org, site_id=site, database_url=cloud_database_url, storage_client=fake_storage_client)
    page = store.upsert_page("https://t.test/p")
    data = _jpeg(5)
    processed = fetch.process_image(data, "image/jpeg", min_side_px=100, max_pixels=10_000_000)
    embedding = np.ones(512, dtype=np.float32) / np.sqrt(512)
    image = store.save_image(url="https://t.test/a.jpg", page_id=page, data=data, content_type="image/jpeg", processed=processed, embedding=embedding)
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE site_images SET last_seen = now() - interval '90 days' WHERE id = %s", (image,))
    assert retention.purge_images(cloud_database_url, fake_storage_client)["files"] == 1
    assert ("site-images", f"{cloud_org}/work/{processed.content_hash}.jpg") not in fake_storage_client.store

    assert store.known_images(["https://t.test/a.jpg"]) == {}  # the crawler downloads it again ...
    assert store.image_by_content_hash(processed.content_hash) is None
    assert store.save_image(url="https://t.test/a.jpg", page_id=page, data=data, content_type="image/jpeg", processed=processed, embedding=embedding) == image
    assert ("site-images", f"{cloud_org}/work/{processed.content_hash}.jpg") in fake_storage_client.store  # ... and the file is back
    assert store.known_images(["https://t.test/a.jpg"]) == {"https://t.test/a.jpg": image}


# --- the alerts on the plan of the project -------------------------------------------------------------------

LIMITS = alerts.Thresholds(disk=0.8, worker_memory=0.85, queue_seconds=300, silent_seconds=300, plan_storage=100 * 1000**3, plan_database=8 * 1000**3)
WORKER = {"instance": "w", "role": "worker", "age_seconds": 5.0, "reading": {"memory": 1, "memory_limit": 10, "host": {"memory_total": 10}}}


def plan_alerts(storage=None, database=None):
    return [(a["code"], a["severity"]) for a in alerts.evaluate([WORKER], 0.0, 0.1, LIMITS, {"storage": storage, "database": database})]


def test_storage_and_database_alerts_start_at_seventy_and_ninety_percent_of_the_plan():
    assert plan_alerts(storage=69 * 1000**3, database=5 * 1000**3) == []
    assert plan_alerts(storage=70 * 1000**3) == [("storage_plan", "warning")]
    assert plan_alerts(storage=95 * 1000**3, database=7.5 * 1000**3) == [("storage_plan", "critical"), ("database_plan", "critical")]
    assert plan_alerts(database=6 * 1000**3) == [("database_plan", "warning")]
    assert alerts.evaluate([WORKER], 0.0, 0.1, LIMITS) == []  # sizes unknown: nothing raised
    message = alerts.evaluate([WORKER], 0.0, 0.1, LIMITS, {"storage": 72 * 1000**3, "database": 0})[0]["message"]
    assert "72 %" in message and "72.0 Go sur 100 Go" in message


def test_a_full_plan_never_stops_the_uptime_probe_and_the_thresholds_can_be_lowered(monkeypatch):
    found = alerts.evaluate([WORKER], 0.0, 0.1, LIMITS, {"storage": 99 * 1000**3, "database": 0})
    assert found[0]["severity"] == "critical" and alerts.probe_problems(found) == []  # the probe says "down", not "paid storage"
    monkeypatch.setenv("NYRA_PLAN_STORAGE_GB", "1")
    monkeypatch.setenv("NYRA_ALERT_SUPABASE_WARN_PERCENT", "10")
    lowered = alerts.thresholds()
    assert lowered.plan_storage == 1000**3 and lowered.usage_warn == pytest.approx(0.1) and lowered.usage_critical == pytest.approx(0.9)
    assert [a["code"] for a in alerts.evaluate([WORKER], 0.0, 0.1, lowered, {"storage": 200 * 1000**2, "database": 0})] == ["storage_plan"]  # lowered artificially: it fires


def test_the_size_of_the_project_is_read_from_the_database_and_kept_ten_minutes(cloud_database_url, monkeypatch):
    monkeypatch.setitem(alerts._usage, "at", float("-inf"))
    bucket = f"b-{uuid.uuid4().hex[:6]}"
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO storage.buckets (id, name) VALUES (%s, %s)", (bucket, bucket))
        conn.execute("INSERT INTO storage.objects (bucket_id, name, metadata) VALUES (%s, 'a', '{\"size\": 1000}'), (%s, 'b', '{\"size\": 234}')", (bucket, bucket))
        first = alerts.supabase_usage(conn)
        before = conn.execute("SELECT COALESCE(sum((metadata->>'size')::bigint), 0) AS n FROM storage.objects").fetchone()["n"]
        conn.execute("INSERT INTO storage.objects (bucket_id, name, metadata) VALUES (%s, 'c', '{\"size\": 99}')", (bucket,))
        assert alerts.supabase_usage(conn) == first  # asked again within ten minutes: the same answer, no new sum
        conn.execute("DELETE FROM storage.objects WHERE bucket_id = %s", (bucket,))
        conn.execute("DELETE FROM storage.buckets WHERE id = %s", (bucket,))
    assert first["storage"] == before and first["storage"] >= 1234 and first["database"] > 0
    monkeypatch.setitem(alerts._usage, "at", float("-inf"))
