"""backend/nyra/cloud/{db,jobs,store}.py against a real Postgres.

Skips unless TEST_DATABASE_URL points at a disposable Postgres with the
`vector` extension available (see conftest.py).
"""

from __future__ import annotations

import io
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("psycopg")

from nyra import fetch
from nyra.cloud import db as cloud_db
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud.store import CloudCrawlStore, CloudMatchStore
from nyra.config import load_config
from nyra.match import run_matching

HASH_A = "ffff0000ffff0000"
HASH_B = "0000ffff0000ffff"


def test_normalize_database_url_encodes_reserved_characters_in_password():
    raw = "postgresql://postgres.abc:p@ss word@aws-0-eu.pooler.supabase.com:5432/postgres"
    assert cloud_db.normalize_database_url(raw) == (
        "postgresql://postgres.abc:p%40ss%20word@aws-0-eu.pooler.supabase.com:5432/postgres"
    )


def test_slugify_folds_accents_and_separators():
    assert cloud_db.slugify("Rémy Martin") == "remy-martin"
    assert cloud_db.slugify("  remy--martin  ") == "remy-martin"
    with pytest.raises(ValueError):
        cloud_db.slugify("---")


def _ref(conn, org_id, name, phash=HASH_A, expiry=None):
    return cloud_db.upsert_reference_image(
        conn, org_id=org_id, filename=name, storage_path=f"{org_id}/{name}", expiry_date=expiry,
        credit=None, notes=None, phash=phash, dhash=phash,
    )


def _site_image(conn, org_id, site_id, url, phash=HASH_A):
    return conn.execute(
        """INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (org_id, site_id, url, f"{org_id}/{uuid.uuid4().hex}.jpg", phash, phash),
    ).fetchone()["id"]


def test_reference_rehash_resets_compared_at_and_delete_returns_paths(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        ref_id = _ref(conn, cloud_org, "a.jpg")
        cloud_db.stamp_compared(conn, [ref_id], [])
        _ref(conn, cloud_org, "a.jpg")  # same hashes: stays compared
        assert cloud_db.get_reference_by_filename(conn, cloud_org, "a.jpg")["compared_at"] is not None
        _ref(conn, cloud_org, "a.jpg", phash=HASH_B)
        assert cloud_db.get_reference_by_filename(conn, cloud_org, "a.jpg")["compared_at"] is None

        listed = cloud_db.list_references(conn, cloud_org)
        assert [row["filename"] for row in listed] == ["a.jpg"] and "embedding" not in listed[0]
        deleted = cloud_db.delete_references(conn, cloud_org, ["a.jpg", "nope.jpg"])
        assert [row["storage_path"] for row in deleted] == [f"{cloud_org}/a.jpg"]


def test_reviews_only_attach_to_this_organizations_matches(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        other_org = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        site = cloud_db.upsert_site(conn, org_id=other_org, url="https://other.test/")
        foreign_ref = _ref(conn, other_org, "x.jpg")
        foreign_img = _site_image(conn, other_org, site, "https://other.test/x.jpg")
        cloud_db.write_matches(conn, other_org, [(foreign_ref, foreign_img, "phash", 1.0, "haut")])

        touched = cloud_db.set_reviews(conn, org_id=cloud_org, reference_id=foreign_ref,
                                       site_image_ids=[foreign_img], decision="ecarte", reviewed_by=None)
        assert touched == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM reviews WHERE reference_id = %s", (foreign_ref,)).fetchone()["c"] == 0


def test_match_rows_aggregates_pages_in_one_query(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://t.test/")
        ref = _ref(conn, cloud_org, "a.jpg", expiry="2026-10-01")
        img = _site_image(conn, cloud_org, site, "https://t.test/a.jpg")
        for path in ("/b", "/a"):
            page = conn.execute(
                "INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
                (cloud_org, site, f"https://t.test{path}"),
            ).fetchone()["id"]
            cloud_db.link_image_page(conn, img, page)
        cloud_db.write_matches(conn, cloud_org, [(ref, img, "phash", 1.0, "haut")])
        rows = cloud_db.match_rows(conn, cloud_org)
    assert len(rows) == 1
    assert rows[0]["pages"] == ["https://t.test/a", "https://t.test/b"]
    assert rows[0]["expiry_date"] == "2026-10-01"


def test_overrides_round_trip(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_db.get_overrides(conn, cloud_org) == {}
        cloud_db.set_overrides(conn, cloud_org, {"match": {"phash_threshold": 6}}, None)
        assert cloud_db.get_overrides(conn, cloud_org) == {"match": {"phash_threshold": 6}}


def test_memberships_and_ping(cloud_database_url, cloud_org):
    user_id = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user_id, f"{user_id}@x.com"))
        cloud_db.add_membership(conn, user_id=user_id, org_id=cloud_org, role="client")
        assert cloud_db.get_membership(conn, user_id=user_id, org_id=cloud_org)["role"] == "client"
        orgs = cloud_db.list_memberships_for_user(conn, user_id)
    assert orgs[0]["org_slug"].startswith("test-")
    assert cloud_db.ping(cloud_database_url)


# --- job queue ---------------------------------------------------------------------

def test_enqueue_refuses_a_second_crawl_and_coalesces_index(cloud_database_url, cloud_org):
    with cloud_db.connect(cloud_database_url) as conn:
        first = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="crawl", params={"site": "https://a.test"})
    with pytest.raises(cloud_jobs.JobConflict):
        with cloud_db.connect(cloud_database_url) as conn:
            cloud_jobs.enqueue(conn, org_id=cloud_org, kind="crawl")
    with cloud_db.connect(cloud_database_url) as conn:
        index_a = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="index")
        index_b = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="index")
        assert index_a["id"] == index_b["id"]
        current = cloud_jobs.current(conn, cloud_org)
    assert [job["id"] for job in current["active"]] == [first["id"], index_a["id"]]


def test_claim_runs_one_job_per_organization_at_a_time(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        org_a = cloud_db.create_organization(conn, name="A", slug=f"a-{uuid.uuid4().hex[:8]}")
        org_b = cloud_db.create_organization(conn, name="B", slug=f"b-{uuid.uuid4().hex[:8]}")
        a1 = cloud_jobs.enqueue(conn, org_id=org_a, kind="match")
        a2 = cloud_jobs.enqueue(conn, org_id=org_a, kind="report")
        b1 = cloud_jobs.enqueue(conn, org_id=org_b, kind="match")
    with cloud_db.connect(cloud_database_url) as conn:
        first = cloud_jobs.claim(conn)
    with cloud_db.connect(cloud_database_url) as conn:
        second = cloud_jobs.claim(conn)
    with cloud_db.connect(cloud_database_url) as conn:
        third = cloud_jobs.claim(conn)
    assert first["id"] == a1["id"]
    assert second["id"] == b1["id"]  # a2 waits: org A already has a running job
    assert third is None
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.finish(conn, first["id"], status="done", message="ok")
        assert cloud_jobs.claim(conn)["id"] == a2["id"]


def test_cancel_queued_is_immediate_and_running_is_flagged(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        org = cloud_db.create_organization(conn, name="C", slug=f"c-{uuid.uuid4().hex[:8]}")
        running = cloud_jobs.enqueue(conn, org_id=org, kind="match")
        queued = cloud_jobs.enqueue(conn, org_id=org, kind="report")
    with cloud_db.connect(cloud_database_url) as conn:
        assert cloud_jobs.claim(conn)["id"] == running["id"]
        assert cloud_jobs.request_cancel(conn, org, uuid.UUID(queued["id"]))["status"] == "cancelled"
        assert cloud_jobs.request_cancel(conn, org, uuid.UUID(running["id"]))["status"] == "running"
        assert cloud_jobs.heartbeat(conn, running["id"], message="x") is True


def test_reap_stale_fails_jobs_whose_worker_disappeared(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        org = cloud_db.create_organization(conn, name="D", slug=f"d-{uuid.uuid4().hex[:8]}")
        job = cloud_jobs.enqueue(conn, org_id=org, kind="match")
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.claim(conn)
        conn.execute("UPDATE jobs SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (job["id"],))
        assert cloud_jobs.reap_stale(conn) == 1
        assert cloud_jobs.get(conn, org, uuid.UUID(job["id"]))["status"] == "error"


# --- stores ---------------------------------------------------------------------------

def _jpeg(seed: int) -> bytes:
    img = Image.new("RGB", (300, 300), (seed, 40, 90))
    ImageDraw.Draw(img).ellipse([40 + seed % 30, 40, 250, 250], fill=(220, seed, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_crawl_store_saves_images_and_reuses_duplicate_bytes(cloud_database_url, cloud_org, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://t.test/")
    store = CloudCrawlStore(org_id=cloud_org, site_id=site, database_url=cloud_database_url,
                            storage_client=fake_storage_client)
    page = store.upsert_page("https://t.test/p")
    data = _jpeg(5)
    processed = fetch.process_image(data, "image/jpeg", min_side_px=100, max_pixels=10_000_000)
    first = store.save_image(url="https://t.test/a.jpg", page_id=page, data=data, content_type="image/jpeg",
                             processed=processed, embedding=np.ones(512, dtype=np.float32) / np.sqrt(512))
    assert ("site-images", f"{cloud_org}/{processed.content_hash}.jpg") in fake_storage_client.store
    assert ("site-images", f"{cloud_org}/thumbs/{processed.content_hash}.jpg") in fake_storage_client.store

    existing = store.image_by_content_hash(processed.content_hash)
    second = store.save_duplicate(url="https://t.test/a.jpg?w=600", page_id=page, existing=existing)
    assert second != first
    store.mark_page(page, 200)
    assert store.known_images(["https://t.test/a.jpg", "https://t.test/other.jpg"]) == {"https://t.test/a.jpg": first}
    assert store.crawled_urls() == {"https://t.test/p"}


def test_match_store_runs_incremental_matching(cloud_database_url, cloud_org):
    config = load_config()
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://t.test/")
        _ref(conn, cloud_org, "a.jpg")
        _site_image(conn, cloud_org, site, "https://t.test/x.jpg")
    store = CloudMatchStore(org_id=cloud_org, database_url=cloud_database_url)
    assert run_matching(store, config, use_clip=True) == 1
    assert run_matching(store, config, use_clip=True) == 1
    with cloud_db.connect(cloud_database_url) as conn:
        _site_image(conn, cloud_org, site, "https://t.test/y.jpg")
        _ref(conn, cloud_org, "b.jpg", phash=HASH_B)
    assert run_matching(store, config, use_clip=True) == 2
