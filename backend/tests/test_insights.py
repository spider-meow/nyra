"""The insights pages: what the crawler measures, the `insights` views, the
statistics built from them, and who may read them (organization admins for
their own numbers, `platform_staff` for everyone's).
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import uuid
from datetime import datetime

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


def _seed(conn, org_id, brand_id, *, crawl_seconds=120.0, host="brand.test"):
    from nyra.cloud import db as cloud_db

    site_id = cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id, url=f"https://{host}/")
    page = conn.execute(
        "INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
        (org_id, site_id, f"https://{host}/p"),
    ).fetchone()["id"]
    images = []
    for name, digest, size, fmt, path in (
        ("a.jpg", f"{host}-h1", 100_000, "jpg", "work/h1.jpg"),
        ("a-2.jpg", f"{host}-h1", 100_000, "jpg", "work/h1.jpg"),   # same bytes: counted once
        ("b.webp", f"{host}-h2", 300_000, "webp", "work/h2.jpg"),
        ("old.png", f"{host}-h3", None, None, "h3.png"),             # stored before formats and sizes were kept
    ):
        images.append(conn.execute(
            """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, width, height, phash, dhash,
                                        byte_size, format, stored_bytes)
               VALUES (%s, %s, %s, %s, %s, 1000, 500, 'ffff', 'ffff', %s, %s, %s) RETURNING id""",
            (org_id, site_id, f"https://{host}/{name}", f"{org_id}/{path}", digest, size, fmt,
             None if size is None else 20_000),
        ).fetchone()["id"])
        conn.execute("INSERT INTO image_pages (image_id, page_id) VALUES (%s, %s)", (images[-1], page))
    ref = cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=brand_id, filename="ref.jpg",
                                          storage_path=f"{org_id}/{brand_id}/ref.jpg", expiry_date="2000-01-01",
                                          credit=None, notes=None, byte_size=5_000_000)
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
        """INSERT INTO jobs (org_id, brand_id, kind, status, created_at, started_at, finished_at, result)
           VALUES (%s, %s, 'crawl', 'done', now() - interval '10 minutes', now() - interval '9 minutes', now(), %s),
                  (%s, %s, 'match', 'error', now(), now(), now(), NULL)""",
        (org_id, brand_id, json.dumps({"match_metrics": {"pairs": 1000, "seconds": 0.5, "hits": 2, "full": True}}),
         org_id, brand_id),
    )


def test_brand_insights_numbers_stay_within_the_brand(cloud_database_url, cloud_org, cloud_brand):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    with cloud_db.connect(cloud_database_url) as conn:
        _seed(conn, cloud_org, cloud_brand)
        # Another brand of the same organization: none of its numbers may leak in.
        other = cloud_db.create_brand(conn, org_id=cloud_org, name="Autre", slug=f"autre-{uuid.uuid4().hex[:6]}")
        _seed(conn, cloud_org, other, crawl_seconds=999.0, host="other.test")
        data = insights.brand_insights(conn, cloud_brand)

    site = data["site"]
    assert site["image_urls"] == 4 and site["distinct_files"] == 3
    assert site["files_with_size"] == 2
    assert site["total_bytes"] == 400_000 and site["avg_bytes"] == 200_000
    assert site["stored_bytes"] == 40_000
    assert site["avg_megapixels"] == pytest.approx(0.5)
    assert site["pages_read"] == 1 and site["image_page_links"] == 4 and site["sites"] == 1
    # The original's format, not the stored working copy's; an old row falls back to its extension.
    assert {row["format"]: row["files"] for row in data["formats"]} == {"jpg": 1, "webp": 1, "png": 1}

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
    assert jobs["crawl"]["total"] == 1 and jobs["crawl"]["avg_run_seconds"] == pytest.approx(540, abs=1)
    assert jobs["crawl"]["avg_wait_seconds"] == pytest.approx(60, abs=1)
    assert jobs["match"]["failed"] == 1
    assert data["compare"]["pairs_per_second"] == pytest.approx(2000)

    matching = data["matching"]
    assert matching["total"]["matches"] == 2 and matching["total"]["reviewed"] == 2
    assert matching["by_confidence"]["a_verifier"]["false_positive_rate"] == 1.0
    assert matching["by_confidence"]["haut"]["false_positive_rate"] == 0.0
    assert matching["by_level"]["phash"]["to_remove"] == 1
    json.dumps(data)


def test_platform_insights_lists_every_brand(empty_platform):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    conn = empty_platform
    a = cloud_db.create_organization(conn, name="Maison A", slug=f"a-{uuid.uuid4().hex[:8]}")
    brand_a = conn.execute("SELECT id FROM brands WHERE org_id = %s", (a,)).fetchone()["id"]
    second = cloud_db.create_brand(conn, org_id=a, name="Cuvée", slug=f"cuvee-{uuid.uuid4().hex[:6]}")
    _seed(conn, a, brand_a, crawl_seconds=100.0)
    data = insights.platform_insights(conn)

    brands = {row["brand_id"]: row for row in data["brands"]}
    # Stored: working copies + thumbnails (40 KB), plus the old row's nothing, plus the library's 5 MB.
    assert brands[str(brand_a)]["storage_bytes"] == 5_040_000
    assert brands[str(brand_a)]["org_name"] == "Maison A"
    assert brands[str(brand_a)]["last_crawl_seconds"] == 100
    assert brands[str(brand_a)]["false_positive_rate"] == 0.5
    assert brands[str(brand_a)]["failed_jobs_30d"] == 1
    assert brands[str(second)]["crawls_90d"] == 0 and brands[str(second)]["last_crawl_at"] is None
    assert data["totals"]["brands"] >= 2 and data["totals"]["organizations"] >= 1
    assert any(row["brand_name"] == "Maison A" and row["kind"] == "match" for row in data["failures"])
    assert "queued" in data["queue"] and "running" in data["queue"]
    json.dumps(data)


def test_platform_insights_shape_history_running_jobs_and_totals(empty_platform):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    conn = empty_platform
    name = f"Maison {uuid.uuid4().hex[:8]}"
    org = cloud_db.create_organization(conn, name=name, slug=name.lower().replace(" ", "-"))
    brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]
    _seed(conn, org, brand, crawl_seconds=100.0)
    conn.execute("""INSERT INTO jobs (org_id, brand_id, kind, status, started_at, heartbeat_at, message)
                    VALUES (%s, %s, 'index', 'running', now(), now(), 'en cours')""", (org, brand))
    data = insights.platform_insights(conn)

    assert list(data) == ["totals", "brands", "total_brands", "crawls", "jobs", "compare", "matching", "queue",
                          "running", "failures"]
    assert data["total_brands"] == data["totals"]["brands"] == len(data["brands"]) == 1
    assert list(data["totals"]) == ["organizations", "brands", "members", "references", "site_files", "pages_read",
                                    "matches", "storage_bytes"]
    brands = data["brands"]
    totals = data["totals"]
    assert totals["references"] == sum(row["references_total"] or 0 for row in brands)
    assert totals["site_files"] == sum(row["distinct_files"] or 0 for row in brands)
    assert totals["pages_read"] == sum(row["pages_read"] or 0 for row in brands)
    assert totals["matches"] == sum(row["matches"] for row in brands)
    assert totals["storage_bytes"] == sum(row["storage_bytes"] for row in brands)
    assert data["totals"]["brands"] == len(data["brands"])
    assert data["totals"]["organizations"] == len({row["org_id"] for row in data["brands"]})

    mine = next(row for row in data["brands"] if row["brand_id"] == str(brand))
    assert {"storage_bytes", "crawls_90d", "last_crawl_at", "last_crawl_status", "last_crawl_seconds",
            "avg_crawl_seconds", "avg_pages_per_minute", "clip_images_per_second", "false_positive_rate", "jobs_30d",
            "failed_jobs_30d", "matches", "members", "org_slug", "references_total"} <= set(mine)
    assert mine["crawls_90d"] == 1 and mine["last_crawl_status"] == "done" and mine["jobs_30d"] == 3
    assert mine["matches"] == 2 and mine["avg_pages_per_minute"] == pytest.approx(6.0)
    assert [(row["org_name"], row["name"]) for row in brands] == sorted((row["org_name"], row["name"]) for row in brands)

    history = [row for row in data["crawls"]["history"] if row["org_name"] == name]
    assert len(history) == 1 and history[0]["pages_visited"] == 10 and history[0]["status"] == "done"
    assert set(history[0]) == {"org_name", "brand_name", "started_at", "status", "duration_seconds", "pages_visited",
                               "images_found", "images_new", "pages_per_minute", "images_scanned_per_second",
                               "clip_images_per_second", "avg_new_image_bytes"}
    assert data["crawls"]["summary"]["runs"] >= 1 and "phases_seconds" in data["crawls"]["summary"]
    assert {row["kind"] for row in data["jobs"]} >= {"crawl", "match"}
    assert data["compare"] is not None and data["matching"]["total"]["matches"] >= 2
    assert data["queue"]["running"] >= 1 and data["queue"]["jobs_24h"] >= 3
    assert set(data["queue"]) == {"queued", "running", "oldest_queued_seconds", "last_heartbeat_seconds", "jobs_24h",
                                  "failed_24h", "last_finished_at"}
    running = [row for row in data["running"] if row["org_name"] == name]
    assert [(row["kind"], row["message"]) for row in running] == [("index", "en cours")]
    assert len(data["failures"]) <= 15
    json.dumps(data)


# --- the platform page on an empty, deterministic platform -----------------------------------
#
# These tests run inside one transaction that is rolled back: `now()` is frozen (so ages are exact), the
# platform holds only what the test creates, and nothing leaks into the other tests.

@pytest.fixture()
def empty_platform(cloud_database_url):
    import psycopg
    from psycopg.rows import dict_row

    from nyra.cloud import db as cloud_db

    with psycopg.connect(cloud_db.normalize_database_url(cloud_database_url), row_factory=dict_row) as conn:
        conn.execute("DELETE FROM organizations")  # cascades to brands, memberships, jobs, runs…
        yield conn
        conn.rollback()


def _shift(conn, brand_id, hours):
    """Move a brand's runs and jobs back in time, so no two brands tie on a date."""
    conn.execute("""UPDATE crawl_runs SET started_at = started_at - %s * interval '1 hour',
                      finished_at = finished_at - %s * interval '1 hour'
                    WHERE site_id IN (SELECT id FROM sites WHERE brand_id = %s)""", (hours, hours, brand_id))
    conn.execute("""UPDATE jobs SET created_at = created_at - %s * interval '1 hour',
                      started_at = started_at - %s * interval '1 hour', finished_at = finished_at - %s * interval '1 hour'
                    WHERE brand_id = %s""", (hours, hours, hours, brand_id))


def _run(conn, org, brand, *, status="done", days=1, pages=10, found=60, new=2, metrics=None, errors=0):
    site = conn.execute("SELECT id FROM sites WHERE brand_id = %s ORDER BY url LIMIT 1", (brand,)).fetchone()["id"]
    conn.execute(
        """INSERT INTO crawl_runs (org_id, site_id, status, started_at, finished_at, pages_visited, images_found,
                                   images_new, errors, metrics)
           VALUES (%s, %s, %s, now() - %s * interval '1 day', now() - %s * interval '1 day' + interval '90 seconds',
                   %s, %s, %s, %s, %s)""",
        (org, site, status, days, days, pages, found, new, json.dumps(["e"] * errors), json.dumps(metrics or {})))


def _job(conn, org, brand, kind, status, *, hours, run_minutes=None, error=None, heartbeat=False):
    conn.execute(
        """INSERT INTO jobs (org_id, brand_id, kind, status, error, message, created_at, started_at, finished_at,
                             heartbeat_at)
           VALUES (%s, %s, %s, %s, %s, 'msg', now() - %s * interval '1 hour',
                   CASE WHEN %s::text IS NULL THEN NULL ELSE now() - %s * interval '1 hour' + interval '1 minute' END,
                   CASE WHEN %s::text IS NULL THEN NULL ELSE now() - %s * interval '1 hour'
                        + %s * interval '1 minute' END,
                   CASE WHEN %s THEN now() - interval '30 seconds' END)""",
        (org, brand, kind, status, error, hours, run_minutes, hours, run_minutes, hours, run_minutes or 0, heartbeat))


def _seed_world(conn):
    """Four organizations, six brands: crawls of every status and age, jobs, matches, reviews, members."""
    from nyra.cloud import db as cloud_db

    ids = {}
    for name in ("Alpha", "Beta", "Delta", "Gamma"):
        ids[name] = cloud_db.create_organization(conn, name=name, slug=name.lower())
    brands = {name: conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]
              for name, org in ids.items()}
    brands["Alpha Cuvee"] = cloud_db.create_brand(conn, org_id=ids["Alpha"], name="Alpha Cuvee", slug="cuvee")
    brands["Delta Bis"] = cloud_db.create_brand(conn, org_id=ids["Delta"], name="Delta Bis", slug="bis")
    for hours, (name, secs) in enumerate((("Alpha", 100.0), ("Beta", 60.0), ("Delta", 300.0)), start=1):
        _seed(conn, ids[name], brands[name], crawl_seconds=secs, host=f"{name.lower()}.test")
        _shift(conn, brands[name], hours)
    _seed(conn, ids["Alpha"], brands["Alpha Cuvee"], crawl_seconds=30.0, host="cuvee.test")
    _shift(conn, brands["Alpha Cuvee"], 4)

    alpha, beta, delta = ids["Alpha"], ids["Beta"], ids["Delta"]
    busy = {"duration_seconds": 200.0, "embedded": 10, "embed_seconds": 5.0, "bytes_new": 1000, "render_seconds": 50.0,
            "download_seconds": 8.0, "http_statuses": {"200": 5, "301": 2, "404": 1}, "formats_new": {"png": 2}}
    _run(conn, alpha, brands["Alpha"], days=3, pages=20, found=100, new=4, metrics=busy)
    _run(conn, alpha, brands["Alpha"], days=5, status="error", pages=3, found=0, new=0, errors=2)
    _run(conn, alpha, brands["Alpha"], days=10, pages=8, found=40, new=0)            # no measurements: old row
    _run(conn, alpha, brands["Alpha"], days=100, pages=99, found=99, new=9, metrics=busy)  # outside the 90 days
    _run(conn, beta, brands["Beta"], days=0, status="running", pages=1, found=1, new=1)
    _run(conn, beta, brands["Beta"], days=4, status="cancelled", pages=2, found=2, new=0, metrics={"duration_seconds": 5})
    _run(conn, delta, brands["Delta"], days=6, pages=30, found=90, new=3,
         metrics={**busy, "duration_seconds": 0, "http_statuses": {"200": 7, "404": 2}, "formats_new": {"webp": 1, "png": 1}})
    _job(conn, alpha, brands["Alpha"], "index", "done", hours=30, run_minutes=3)
    _job(conn, alpha, brands["Alpha"], "report", "cancelled", hours=2, run_minutes=1)
    _job(conn, alpha, brands["Alpha"], "crawl", "queued", hours=15)
    _job(conn, alpha, brands["Alpha"], "crawl", "error", hours=24 * 40, run_minutes=2, error="old failure")
    _job(conn, beta, brands["Beta"], "index", "running", hours=1, run_minutes=1, heartbeat=True)
    _job(conn, delta, brands["Delta Bis"], "match", "error", hours=6, run_minutes=4, error="boom")
    _job(conn, delta, brands["Delta Bis"], "crawl", "done", hours=7, run_minutes=9)

    users = [uuid.uuid4() for _ in range(3)]
    for user in users:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, f"{user}@x"))
    for user, org in ((users[0], alpha), (users[0], beta), (users[1], alpha), (users[2], delta)):
        cloud_db.add_membership(conn, user_id=user, org_id=org, role="admin")
    return {**{f"org:{name}": org for name, org in ids.items()}, **{f"brand:{name}": b for name, b in brands.items()}}


_ISO = re.compile(r"^\d{4}-\d\d-\d\dT")


def _scrub(value, aliases, now):
    """Ids become names and dates become 'seconds before now', so a snapshot is the same on every run."""
    if isinstance(value, dict):
        return {key: "<id>" if key == "id" else _scrub(item, aliases, now) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, aliases, now) for item in value]
    if isinstance(value, str) and _ISO.match(value):
        return f"T-{round((now - datetime.fromisoformat(value)).total_seconds())}s"
    if isinstance(value, str) and value in aliases:
        return aliases[value]
    return value


def _same(actual, expected, path="data"):
    """Equal structures; floats within a few ulps (an average may add its terms in another order)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), path
        for key in expected:
            _same(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for index, item in enumerate(expected):
            _same(actual[index], item, f"{path}[{index}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), path
    else:
        assert actual == expected, path


def _platform_snapshot(conn):
    from nyra.cloud import insights

    ids = _seed_world(conn)
    aliases = {str(value): key for key, value in ids.items()}
    now = conn.execute("SELECT now() AS n").fetchone()["n"]
    return _scrub(insights.platform_insights(conn), aliases, now)


# Captured from the code before the back office was made to scale (every brand, every 90-day run read in Python).
_PLATFORM_SNAPSHOT = r"""
{
 "totals": {"organizations": 4, "brands": 6, "members": 3, "references": 4, "site_files": 12, "pages_read": 4, "matches": 8, "storage_bytes": 20160000.0},
 "brands": [
  {"brand_id": "brand:Alpha", "name": "Alpha", "slug": "alpha", "org_id": "org:Alpha", "org_name": "Alpha", "org_slug": "alpha", "distinct_files": 3, "site_bytes": 400000.0, "avg_image_bytes": 200000.0, "site_stored_bytes": 40000.0, "pages_read": 1, "sites": 1, "references_total": 1, "expired": 1, "expiring_90_days": 0, "library_bytes": 5000000.0, "matches": 2, "members": 2, "storage_bytes": 5040000.0, "crawls_90d": 4, "last_crawl_at": "T-3600s", "last_crawl_status": "done", "last_crawl_seconds": 100.0, "avg_crawl_seconds": 130.0, "avg_pages_per_minute": 5.777777777777779, "clip_images_per_second": 7.142857142857143, "false_positive_rate": 0.5, "jobs_30d": 5, "failed_jobs_30d": 1},
  {"brand_id": "brand:Alpha Cuvee", "name": "Alpha Cuvee", "slug": "cuvee", "org_id": "org:Alpha", "org_name": "Alpha", "org_slug": "alpha", "distinct_files": 3, "site_bytes": 400000.0, "avg_image_bytes": 200000.0, "site_stored_bytes": 40000.0, "pages_read": 1, "sites": 1, "references_total": 1, "expired": 1, "expiring_90_days": 0, "library_bytes": 5000000.0, "matches": 2, "members": 2, "storage_bytes": 5040000.0, "crawls_90d": 1, "last_crawl_at": "T-14400s", "last_crawl_status": "done", "last_crawl_seconds": 30.0, "avg_crawl_seconds": 30.0, "avg_pages_per_minute": 20.0, "clip_images_per_second": 20.0, "false_positive_rate": 0.5, "jobs_30d": 2, "failed_jobs_30d": 1},
  {"brand_id": "brand:Beta", "name": "Beta", "slug": "beta", "org_id": "org:Beta", "org_name": "Beta", "org_slug": "beta", "distinct_files": 3, "site_bytes": 400000.0, "avg_image_bytes": 200000.0, "site_stored_bytes": 40000.0, "pages_read": 1, "sites": 1, "references_total": 1, "expired": 1, "expiring_90_days": 0, "library_bytes": 5000000.0, "matches": 2, "members": 1, "storage_bytes": 5040000.0, "crawls_90d": 3, "last_crawl_at": "T-0s", "last_crawl_status": "running", "last_crawl_seconds": 90.0, "avg_crawl_seconds": 60.0, "avg_pages_per_minute": 10.0, "clip_images_per_second": 20.0, "false_positive_rate": 0.5, "jobs_30d": 3, "failed_jobs_30d": 1},
  {"brand_id": "brand:Delta", "name": "Delta", "slug": "delta", "org_id": "org:Delta", "org_name": "Delta", "org_slug": "delta", "distinct_files": 3, "site_bytes": 400000.0, "avg_image_bytes": 200000.0, "site_stored_bytes": 40000.0, "pages_read": 1, "sites": 1, "references_total": 1, "expired": 1, "expiring_90_days": 0, "library_bytes": 5000000.0, "matches": 2, "members": 1, "storage_bytes": 5040000.0, "crawls_90d": 2, "last_crawl_at": "T-10800s", "last_crawl_status": "done", "last_crawl_seconds": 300.0, "avg_crawl_seconds": 195.0, "avg_pages_per_minute": 11.0, "clip_images_per_second": 7.142857142857143, "false_positive_rate": 0.5, "jobs_30d": 2, "failed_jobs_30d": 1},
  {"brand_id": "brand:Delta Bis", "name": "Delta Bis", "slug": "bis", "org_id": "org:Delta", "org_name": "Delta", "org_slug": "delta", "distinct_files": 0, "site_bytes": null, "avg_image_bytes": null, "site_stored_bytes": null, "pages_read": 0, "sites": 0, "references_total": 0, "expired": 0, "expiring_90_days": 0, "library_bytes": null, "matches": 0, "members": 1, "storage_bytes": 0, "crawls_90d": 0, "last_crawl_at": null, "last_crawl_status": null, "last_crawl_seconds": null, "avg_crawl_seconds": null, "avg_pages_per_minute": null, "clip_images_per_second": null, "false_positive_rate": null, "jobs_30d": 2, "failed_jobs_30d": 1},
  {"brand_id": "brand:Gamma", "name": "Gamma", "slug": "gamma", "org_id": "org:Gamma", "org_name": "Gamma", "org_slug": "gamma", "distinct_files": 0, "site_bytes": null, "avg_image_bytes": null, "site_stored_bytes": null, "pages_read": 0, "sites": 0, "references_total": 0, "expired": 0, "expiring_90_days": 0, "library_bytes": null, "matches": 0, "members": 0, "storage_bytes": 0, "crawls_90d": 0, "last_crawl_at": null, "last_crawl_status": null, "last_crawl_seconds": null, "avg_crawl_seconds": null, "avg_pages_per_minute": null, "clip_images_per_second": null, "false_positive_rate": null, "jobs_30d": 0, "failed_jobs_30d": 0}
 ],
 "crawls": {
  "summary": {"runs": 10, "finished": 7, "failed": 1, "avg_duration_seconds": 124.28571428571429, "max_duration_seconds": 300.0, "avg_pages": 14.0, "avg_pages_per_minute": 9.904761904761903, "avg_images_scanned_per_second": 0.8206349206349206, "avg_seconds_per_page": 4.694444444444445, "avg_images_per_page": 5.285714285714286, "clip_images_per_second": 10.0, "avg_new_image_bytes": 106800.0, "images_found": 470, "images_new": 15, "bytes_downloaded": 0, "bytes_new": 1602000, "phases_seconds": {"render_seconds": 340.0, "download_seconds": 16.0, "process_seconds": 0, "embed_seconds": 18.0, "store_seconds": 0}, "http_statuses": {"200": 48, "404": 7, "301": 2}, "formats_new": {"webp": 5, "jpg": 4, "png": 3}},
  "history": [
   {"org_name": "Alpha", "brand_name": "Alpha", "started_at": "T-864000s", "status": "done", "duration_seconds": 90.0, "pages_visited": 8, "images_found": 40, "images_new": 0, "pages_per_minute": 5.333333333333334, "images_scanned_per_second": 0.4444444444444444, "clip_images_per_second": null, "avg_new_image_bytes": null},
   {"org_name": "Delta", "brand_name": "Delta", "started_at": "T-518400s", "status": "done", "duration_seconds": 90.0, "pages_visited": 30, "images_found": 90, "images_new": 3, "pages_per_minute": 20.0, "images_scanned_per_second": 1.0, "clip_images_per_second": 2.0, "avg_new_image_bytes": 333.3333333333333},
   {"org_name": "Alpha", "brand_name": "Alpha", "started_at": "T-432000s", "status": "error", "duration_seconds": 90.0, "pages_visited": 3, "images_found": 0, "images_new": 0, "pages_per_minute": 2.0, "images_scanned_per_second": 0.0, "clip_images_per_second": null, "avg_new_image_bytes": null},
   {"org_name": "Beta", "brand_name": "Beta", "started_at": "T-345600s", "status": "cancelled", "duration_seconds": 5.0, "pages_visited": 2, "images_found": 2, "images_new": 0, "pages_per_minute": 24.0, "images_scanned_per_second": 0.4, "clip_images_per_second": null, "avg_new_image_bytes": null},
   {"org_name": "Alpha", "brand_name": "Alpha", "started_at": "T-259200s", "status": "done", "duration_seconds": 200.0, "pages_visited": 20, "images_found": 100, "images_new": 4, "pages_per_minute": 6.0, "images_scanned_per_second": 0.5, "clip_images_per_second": 2.0, "avg_new_image_bytes": 250.0},
   {"org_name": "Alpha", "brand_name": "Alpha Cuvee", "started_at": "T-14400s", "status": "done", "duration_seconds": 30.0, "pages_visited": 10, "images_found": 60, "images_new": 2, "pages_per_minute": 20.0, "images_scanned_per_second": 2.0, "clip_images_per_second": 20.0, "avg_new_image_bytes": 200000.0},
   {"org_name": "Delta", "brand_name": "Delta", "started_at": "T-10800s", "status": "done", "duration_seconds": 300.0, "pages_visited": 10, "images_found": 60, "images_new": 2, "pages_per_minute": 2.0, "images_scanned_per_second": 0.2, "clip_images_per_second": 20.0, "avg_new_image_bytes": 200000.0},
   {"org_name": "Beta", "brand_name": "Beta", "started_at": "T-7200s", "status": "done", "duration_seconds": 60.0, "pages_visited": 10, "images_found": 60, "images_new": 2, "pages_per_minute": 10.0, "images_scanned_per_second": 1.0, "clip_images_per_second": 20.0, "avg_new_image_bytes": 200000.0},
   {"org_name": "Alpha", "brand_name": "Alpha", "started_at": "T-3600s", "status": "done", "duration_seconds": 100.0, "pages_visited": 10, "images_found": 60, "images_new": 2, "pages_per_minute": 6.0, "images_scanned_per_second": 0.6, "clip_images_per_second": 20.0, "avg_new_image_bytes": 200000.0},
   {"org_name": "Beta", "brand_name": "Beta", "started_at": "T-0s", "status": "running", "duration_seconds": 90.0, "pages_visited": 1, "images_found": 1, "images_new": 1, "pages_per_minute": 0.6666666666666667, "images_scanned_per_second": 0.011111111111111112, "clip_images_per_second": null, "avg_new_image_bytes": null}
  ]
 },
 "jobs": [
  {"kind": "crawl", "total": 6, "done": 5, "failed": 0, "cancelled": 0, "avg_run_seconds": 528.0, "p95_run_seconds": 540.0, "avg_wait_seconds": 60.0},
  {"kind": "index", "total": 2, "done": 1, "failed": 0, "cancelled": 0, "avg_run_seconds": 120.0, "p95_run_seconds": 120.0, "avg_wait_seconds": 60.0},
  {"kind": "match", "total": 5, "done": 0, "failed": 5, "cancelled": 0, "avg_run_seconds": null, "p95_run_seconds": null, "avg_wait_seconds": 12.0},
  {"kind": "report", "total": 1, "done": 0, "failed": 0, "cancelled": 1, "avg_run_seconds": null, "p95_run_seconds": null, "avg_wait_seconds": 60.0}
 ],
 "compare": {"full": true, "hits": 2, "pairs": 1000, "seconds": 0.5, "finished_at": "T-3600s", "pairs_per_second": 2000.0},
 "matching": {"total": {"matches": 8, "reviewed": 8, "to_remove": 4, "removed": 0, "false_positives": 4, "false_positive_rate": 0.5, "review_progress": 1.0}, "by_confidence": {"haut": {"matches": 4, "reviewed": 4, "to_remove": 4, "removed": 0, "false_positives": 0, "false_positive_rate": 0.0}, "a_verifier": {"matches": 4, "reviewed": 4, "to_remove": 0, "removed": 0, "false_positives": 4, "false_positive_rate": 1.0}}, "by_level": {"phash": {"matches": 4, "reviewed": 4, "to_remove": 4, "removed": 0, "false_positives": 0, "false_positive_rate": 0.0}, "clip": {"matches": 4, "reviewed": 4, "to_remove": 0, "removed": 0, "false_positives": 4, "false_positive_rate": 1.0}}},
 "queue": {"queued": 1, "running": 1, "oldest_queued_seconds": 54000.0, "last_heartbeat_seconds": 30.0, "jobs_24h": 13, "failed_24h": 5, "last_finished_at": "T-3540s"},
 "running": [
  {"id": "<id>", "org_name": "Beta", "brand_name": "Beta", "kind": "index", "message": "msg", "progress": {}, "started_at": "T-3540s", "heartbeat_age_seconds": 30.0}
 ],
 "failures": [
  {"id": "<id>", "org_name": "Alpha", "brand_name": "Alpha", "kind": "match", "error": null, "finished_at": "T-3600s"},
  {"id": "<id>", "org_name": "Beta", "brand_name": "Beta", "kind": "match", "error": null, "finished_at": "T-7200s"},
  {"id": "<id>", "org_name": "Delta", "brand_name": "Delta", "kind": "match", "error": null, "finished_at": "T-10800s"},
  {"id": "<id>", "org_name": "Alpha", "brand_name": "Alpha Cuvee", "kind": "match", "error": null, "finished_at": "T-14400s"},
  {"id": "<id>", "org_name": "Delta", "brand_name": "Delta Bis", "kind": "match", "error": "boom", "finished_at": "T-21360s"},
  {"id": "<id>", "org_name": "Alpha", "brand_name": "Alpha", "kind": "crawl", "error": "old failure", "finished_at": "T-3455880s"}
 ]
}
"""


def test_platform_insights_snapshot_is_unchanged(empty_platform):
    snapshot = _platform_snapshot(empty_platform)
    expected = json.loads(_PLATFORM_SNAPSHOT)
    # `total_brands` is new; every older figure must match the snapshot taken before.
    assert snapshot.pop("total_brands") == 6
    _same(snapshot, expected)


def test_platform_summary_is_what_python_computes_from_the_runs(empty_platform):
    from nyra.cloud import insights

    _seed_world(empty_platform)
    runs = _all_runs(empty_platform)
    assert len(runs) == 10  # the run 100 days old is not in the 90-day window
    _same(insights.platform_insights(empty_platform)["crawls"]["summary"], insights._crawl_summary(runs))


def _all_runs(conn):
    from nyra.cloud import insights

    return insights._all(conn, f"""SELECT {insights._RUN_COLUMNS} FROM insights.crawl_runs
                                   WHERE started_at > now() - interval '90 days'""")


def test_platform_brands_are_capped_but_totals_count_every_brand(empty_platform, monkeypatch):
    from nyra.cloud import insights

    _seed_world(empty_platform)
    everything = insights.platform_insights(empty_platform)
    for limit, expected in ((1, {"Beta"}), (5, {"Alpha", "Alpha Cuvee", "Beta", "Delta", "Delta Bis"})):
        monkeypatch.setattr(insights, "PLATFORM_BRANDS_LIMIT", limit)
        data = insights.platform_insights(empty_platform)
        # The most recently active brands (last crawl or job); Gamma never did anything.
        assert {row["name"] for row in data["brands"]} == expected
        assert data["brands"] == [row for row in everything["brands"] if row["name"] in expected]
        assert data["total_brands"] == data["totals"]["brands"] == 6
        assert data["totals"] == everything["totals"]
        assert [row["name"] for row in data["brands"]] == sorted(row["name"] for row in data["brands"])
    assert len(everything["brands"]) == 6


def test_platform_history_keeps_the_latest_runs_oldest_first(empty_platform):
    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    conn = empty_platform
    org = cloud_db.create_organization(conn, name="Solo", slug="solo")
    brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]
    cloud_db.create_site(conn, org_id=org, brand_id=brand, url="https://solo.test/")
    for days in range(1, 66):
        _run(conn, org, brand, days=days, pages=days)
    data = insights.platform_insights(conn)
    # 65 runs, 60 shown: days 60 (oldest) to 1 (latest), and the summary still counts all 65.
    assert [row["pages_visited"] for row in data["crawls"]["history"]] == list(range(60, 0, -1))
    assert data["crawls"]["summary"]["runs"] == 65


def test_platform_insights_on_an_empty_platform(empty_platform):
    from nyra.cloud import insights

    data = insights.platform_insights(empty_platform)
    assert data["brands"] == [] and data["total_brands"] == 0
    assert set(data["totals"].values()) == {0}
    summary = data["crawls"]["summary"]
    assert summary["runs"] == 0 and summary["avg_duration_seconds"] is None and summary["http_statuses"] == {}
    assert data["crawls"]["history"] == [] and data["jobs"] == [] and data["compare"] is None
    json.dumps(data)


# --- API -------------------------------------------------------------------------------------

def test_insights_routes_are_gated(cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
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

    path = f"/api/orgs/{cloud_org}/brands/{cloud_brand}/insights"
    assert client.get(path, headers=headers(admin)).status_code == 200
    assert client.get(path, headers=headers(member)).status_code == 403
    assert client.get(path, headers=headers(staff)).status_code == 403
    assert client.get(f"/api/orgs/{cloud_org}/brands/{uuid.uuid4()}/insights", headers=headers(admin)).status_code == 404

    assert client.get("/api/staff/insights").status_code == 401
    assert client.get("/api/staff/insights", headers=headers(admin)).status_code == 403
    body = client.get("/api/staff/insights", headers=headers(staff)).json()
    assert "brands" in body and "queue" in body

    assert client.get("/api/me", headers=headers(staff)).json()["staff"] is True
    assert client.get("/api/me", headers=headers(admin)).json()["staff"] is False

    with cloud_db.connect(cloud_database_url) as conn:
        insights.set_staff(conn, staff, False)
    assert client.get("/api/staff/insights", headers=headers(staff)).status_code == 403
