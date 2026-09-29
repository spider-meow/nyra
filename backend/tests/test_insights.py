"""The insights pages: what the crawler measures, the `insights` views, the
statistics built from them, and who may read them (organization admins for
their own numbers, `platform_staff` for everyone's).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import uuid

import pytest
from PIL import Image, ImageDraw

from nyra import fetch
from nyra.config import load_config
from nyra.crawl import CrawlStats, _Crawl


def _png(size: int, seed: int) -> bytes:
    img = Image.new("RGB", (size, size), color=(seed % 256, 40, 90))
    ImageDraw.Draw(img).ellipse([10, 10, size - 10, size - 10], fill=(200, seed % 256, 10))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _MemoryStore:
    """The CrawlStore protocol in memory: enough for _collect_images."""

    def __init__(self, known: dict[str, str]):
        self.known = known
        self.by_hash: dict[str, dict] = {}
        self.saved: list[str] = []
        self.duplicates: list[str] = []

    def known_images(self, urls):
        return {url: self.known[url] for url in urls if url in self.known}

    def link(self, image_id, page_id):
        pass

    def image_by_content_hash(self, digest):
        return self.by_hash.get(digest)

    def save_duplicate(self, *, url, page_id, existing):
        self.duplicates.append(url)

    def save_image(self, *, url, page_id, data, content_type, processed, embedding):
        self.saved.append(url)
        self.by_hash[processed.content_hash] = {"content_hash": processed.content_hash}


def test_collect_images_measures_downloads_rejections_duplicates_and_formats(monkeypatch):
    big_a, big_b, tiny = _png(400, 1), _png(300, 2), _png(50, 3)
    bodies = {
        "https://s.test/a.png": big_a,
        "https://s.test/a-copy.png": big_a,   # same bytes, other URL
        "https://s.test/b.png": big_b,
        "https://s.test/tiny.png": tiny,      # under min_image_side_px
        "https://s.test/404.png": None,       # download fails
    }

    async def fake_download(url, client, *, timeout, max_bytes):
        body = bodies[url]
        return None if body is None else (body, "image/png")

    monkeypatch.setattr(fetch, "adownload", fake_download)
    store = _MemoryStore(known={"https://s.test/known.png": "img-1"})
    crawl = _Crawl("https://s.test", store, load_config(), max_pages=1, embedder=lambda images: [None] * len(images),
                   resume=False, progress=None, should_stop=None)

    asyncio.run(crawl._collect_images(None, sorted([*bodies, "https://s.test/known.png"]), page_id="p1"))
    stats = crawl.stats

    assert stats.images_found == 6
    assert stats.images_known == 1
    assert stats.downloads == 5 and stats.downloads_failed == 1
    assert stats.bytes_downloaded == 2 * len(big_a) + len(big_b) + len(tiny)
    assert stats.images_rejected == 1
    assert stats.images_duplicate == 1
    assert stats.images_new == 2
    assert store.saved == ["https://s.test/a-copy.png", "https://s.test/b.png"]
    assert stats.bytes_new == len(big_a) + len(big_b)
    assert stats.pixels_new == 400 * 400 + 300 * 300
    assert stats.formats_new == {"png": 2}
    assert stats.embedded == 2
    assert stats.download_seconds > 0 and stats.store_seconds >= 0


def test_metrics_are_json_ready_and_rounded():
    stats = CrawlStats(render_seconds=1.23456789, http_statuses={"200": 3}, bytes_new=10)
    metrics = stats.metrics()
    assert metrics["render_seconds"] == 1.235
    assert metrics["http_statuses"] == {"200": 3}
    assert "pages_visited" not in metrics  # crawl_runs has its own columns for the counters
    json.dumps(metrics)


# --- Postgres (TEST_DATABASE_URL) ----------------------------------------------------------


def _seed(conn, org_id, *, crawl_seconds=120.0):
    from nyra.cloud import db as cloud_db

    site_id = cloud_db.upsert_site(conn, org_id=org_id, url="https://brand.test/")
    page = conn.execute(
        "INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, 'https://brand.test/p', 'done') RETURNING id",
        (org_id, site_id),
    ).fetchone()["id"]
    images = []
    for url, digest, size, ext in (
        ("https://brand.test/a.jpg", "h1", 100_000, "jpg"),
        ("https://brand.test/a-2.jpg", "h1", 100_000, "jpg"),   # same bytes: counted once
        ("https://brand.test/b.webp", "h2", 300_000, "webp"),
    ):
        images.append(conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, width, height, phash, dhash,
                                        byte_size)
               VALUES (%s, %s, %s, %s, %s, 1000, 500, 'ffff', 'ffff', %s) RETURNING id""",
            (org_id, site_id, url, f"{org_id}/{digest}.{ext}", digest, size),
        ).fetchone()["id"])
        conn.execute("INSERT INTO image_pages (image_id, page_id) VALUES (%s, %s)", (images[-1], page))
    ref = cloud_db.upsert_reference_image(conn, org_id=org_id, filename="ref.jpg", storage_path=f"{org_id}/ref.jpg",
                                          expiry_date="2000-01-01", credit=None, notes=None, byte_size=5_000_000)
    conn.execute(
        """INSERT INTO matches (org_id, reference_id, site_image_id, level, score, confidence)
           VALUES (%s, %s, %s, 'phash', 2, 'haut'), (%s, %s, %s, 'clip', 0.8, 'a_verifier')""",
        (org_id, ref, images[0], org_id, ref, images[2]),
    )
    conn.execute(
        """INSERT INTO reviews (reference_id, site_image_id, org_id, decision)
           VALUES (%s, %s, %s, 'retenu'), (%s, %s, %s, 'ecarte')""",
        (ref, images[0], org_id, ref, images[2], org_id),
    )
    metrics = CrawlStats(duration_seconds=crawl_seconds, embedded=40, embed_seconds=2.0, bytes_new=400_000,
                         render_seconds=60.0, http_statuses={"200": 9, "404": 1}, formats_new={"jpg": 1, "webp": 1})
    conn.execute(
        """INSERT INTO crawl_runs (org_id, site_id, status, finished_at, pages_visited, images_found, images_stored,
                                   images_new, metrics)
           VALUES (%s, %s, 'done', now(), 10, 60, 3, 2, %s)""",
        (org_id, site_id, json.dumps(metrics.metrics())),
    )
    conn.execute(
        """INSERT INTO jobs (org_id, kind, status, created_at, started_at, finished_at, result)
           VALUES (%s, 'crawl', 'done', now() - interval '10 minutes', now() - interval '9 minutes', now(), %s),
                  (%s, 'match', 'error', now(), now(), now(), NULL)""",
        (org_id, json.dumps({"match_metrics": {"pairs": 1000, "seconds": 0.5, "hits": 2, "full": True}}), org_id),
    )


def test_org_insights_numbers(cloud_database_url, cloud_org):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    with cloud_db.connect(cloud_database_url) as conn:
        _seed(conn, cloud_org)
        data = insights.org_insights(conn, cloud_org)

    site = data["site"]
    assert site["image_urls"] == 3 and site["distinct_files"] == 2
    assert site["total_bytes"] == 400_000 and site["avg_bytes"] == 200_000
    assert site["avg_megapixels"] == pytest.approx(0.5)
    assert site["pages_read"] == 1 and site["image_page_links"] == 3
    assert {row["format"]: row["files"] for row in data["formats"]} == {"jpg": 1, "webp": 1}

    assert data["library"]["references_total"] == 1 and data["library"]["expired"] == 1
    assert data["library"]["total_bytes"] == 5_000_000

    summary = data["crawls"]["summary"]
    assert summary["finished"] == 1
    assert summary["avg_duration_seconds"] == 120
    assert summary["avg_pages_per_minute"] == pytest.approx(5.0)
    assert summary["avg_images_scanned_per_second"] == pytest.approx(0.5)
    assert summary["clip_images_per_second"] == pytest.approx(20.0)
    assert summary["avg_new_image_bytes"] == pytest.approx(200_000)
    assert summary["avg_images_per_page"] == pytest.approx(6.0)
    assert summary["http_statuses"] == {"200": 9, "404": 1}
    assert summary["phases_seconds"]["render_seconds"] == 60.0
    assert data["crawls"]["last"]["seconds_per_page"] == pytest.approx(6.0)

    jobs = {row["kind"]: row for row in data["jobs"]}
    assert jobs["crawl"]["done"] == 1 and jobs["crawl"]["avg_run_seconds"] == pytest.approx(540, abs=1)
    assert jobs["crawl"]["avg_wait_seconds"] == pytest.approx(60, abs=1)
    assert jobs["match"]["failed"] == 1
    assert data["compare"]["pairs_per_second"] == pytest.approx(2000)

    matching = data["matching"]
    assert matching["total"]["matches"] == 2 and matching["total"]["reviewed"] == 2
    assert matching["by_confidence"]["a_verifier"]["false_positive_rate"] == 1.0
    assert matching["by_confidence"]["haut"]["false_positive_rate"] == 0.0
    assert matching["by_level"]["phash"]["to_remove"] == 1
    json.dumps(data)


def test_platform_insights_lists_every_organization(cloud_database_url):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    with cloud_db.connect(cloud_database_url) as conn:
        a = cloud_db.create_organization(conn, name="Marque A", slug=f"a-{uuid.uuid4().hex[:8]}")
        b = cloud_db.create_organization(conn, name="Marque B", slug=f"b-{uuid.uuid4().hex[:8]}")
        _seed(conn, a, crawl_seconds=100.0)
        data = insights.platform_insights(conn)

    orgs = {row["org_id"]: row for row in data["organizations"]}
    assert orgs[str(a)]["storage_bytes"] == 5_400_000
    assert orgs[str(a)]["last_crawl_seconds"] == 100
    assert orgs[str(a)]["false_positive_rate"] == 0.5
    assert orgs[str(a)]["failed_jobs_30d"] == 1
    assert orgs[str(b)]["crawls_90d"] == 0 and orgs[str(b)]["last_crawl_at"] is None
    assert data["totals"]["organizations"] >= 2
    assert any(row["org_name"] == "Marque A" and row["kind"] == "match" for row in data["failures"])
    assert "queued" in data["queue"] and "running" in data["queue"]
    json.dumps(data)


# --- API -------------------------------------------------------------------------------------

def test_insights_routes_are_gated(cloud_database_url, cloud_org, fake_storage_client):
    pytest.importorskip("fastapi")
    import jwt as pyjwt
    from fastapi.testclient import TestClient

    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights
    from nyra.cloud.api import CloudSettings, create_app

    secret = "test-secret-at-least-32-bytes-long-enough!!"
    settings = CloudSettings(database_url=cloud_database_url, supabase_url="https://x.supabase.co",
                             service_role_key="key", jwt_secret=secret)
    settings.storage_client = lambda: fake_storage_client
    client = TestClient(create_app(settings))

    def headers(user_id):
        token = pyjwt.encode({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600},
                             secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    admin, member, staff = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, 'a@x'), (%s, 'c@x'), (%s, 's@x')",
                     (admin, member, staff))
        cloud_db.add_membership(conn, user_id=admin, org_id=cloud_org, role="admin")
        cloud_db.add_membership(conn, user_id=member, org_id=cloud_org, role="client")
        insights.set_staff(conn, staff, True)

    assert client.get(f"/api/orgs/{cloud_org}/insights", headers=headers(admin)).status_code == 200
    assert client.get(f"/api/orgs/{cloud_org}/insights", headers=headers(member)).status_code == 403
    assert client.get(f"/api/orgs/{cloud_org}/insights", headers=headers(staff)).status_code == 403

    assert client.get("/api/staff/insights").status_code == 401
    assert client.get("/api/staff/insights", headers=headers(admin)).status_code == 403
    body = client.get("/api/staff/insights", headers=headers(staff)).json()
    assert "organizations" in body and "queue" in body

    assert client.get("/api/me", headers=headers(staff)).json()["staff"] is True
    assert client.get("/api/me", headers=headers(admin)).json()["staff"] is False

    with cloud_db.connect(cloud_database_url) as conn:
        insights.set_staff(conn, staff, False)
    assert client.get("/api/staff/insights", headers=headers(staff)).status_code == 403
