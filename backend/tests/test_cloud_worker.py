"""backend/nyra/cloud/worker.py: jobs claimed from Postgres and run end to end,
against a real Postgres (TEST_DATABASE_URL) and the in-memory fake Storage.

CLIP is replaced by a deterministic fake embedding so no model is
downloaded; a real crawl needs a browser and a live site and isn't run
here (the crawler's parsing is covered in test_crawl.py).
"""

from __future__ import annotations

import errno
import io
import os
import threading
import time
import uuid
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("psycopg")

import psycopg

from nyra.cloud import db as cloud_db
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud import labels as cloud_labels
from nyra.cloud import logos as cloud_logos
from nyra.cloud import presence as cloud_presence
from nyra.cloud import usage as cloud_usage
from nyra.cloud import worker as worker_module
from nyra.config import Config, MatchConfig
from nyra.match import compute_hashes, signature


def _image_bytes(seed: int) -> bytes:
    img = Image.new("RGB", (320, 320), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 150 + seed, 300], fill=(200, 30 + seed, 30))
    draw.ellipse([170, 60, 300, 200], fill=(20, 40, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _fake_embeddings(images, _config):
    out = []
    for img in images:
        vector = np.asarray(img.convert("L").resize((16, 32)), dtype=np.float32).ravel()
        out.append(vector / (np.linalg.norm(vector) or 1.0))
    return out


@pytest.fixture()
def worker(cloud_database_url, fake_storage_client, monkeypatch):
    monkeypatch.setattr(worker_module, "compute_clip_embeddings", _fake_embeddings)
    monkeypatch.setattr(worker_module, "warm_clip", lambda _config: None)
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
    return worker_module.Worker(database_url=cloud_database_url, storage_client_factory=lambda: fake_storage_client)


def _job(cloud_database_url, brand_id, job_id):
    with cloud_db.connect(cloud_database_url) as conn:
        return cloud_jobs.get(conn, brand_id, uuid.UUID(job_id))


def _add_reference(conn, storage, org_id, brand_id, name, data, expiry="2020-01-01"):
    img = Image.open(io.BytesIO(data))
    phash, dhash = compute_hashes(img)
    path = f"{org_id}/{brand_id}/{name}"
    storage.store[("refs", path)] = data
    return cloud_db.upsert_reference_image(conn, org_id=org_id, brand_id=brand_id, filename=name, storage_path=path,
                                           expiry_date=expiry, credit=None, notes=None, phash=phash, dhash=dhash)


def _add_site_image(conn, storage, org_id, site_id, url, data):
    img = Image.open(io.BytesIO(data))
    phash, dhash = compute_hashes(img)
    path = f"{org_id}/{uuid.uuid4().hex}.jpg"
    storage.store[("site-images", path)] = data
    image_id = conn.execute(
        """INSERT INTO site_images (org_id, site_id, url, storage_path, content_hash, phash, dhash)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (org_id, site_id, url, path, uuid.uuid4().hex, phash, dhash),
    ).fetchone()["id"]
    page_id = conn.execute(
        "INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done') RETURNING id",
        (org_id, site_id, url + "/page"),
    ).fetchone()["id"]
    cloud_db.link_image_page(conn, image_id, page_id)
    return image_id


def test_index_job_backfills_references_and_site_images_then_compares(worker, cloud_database_url, cloud_org, cloud_brand,
                                                                     fake_storage_client):
    data = _image_bytes(0)
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "hero.jpg", data)
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/hero.jpg", data)
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="index")

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done", finished
    assert finished["result"]["indexed"] == 2
    assert finished["result"]["matches"] == 1
    # Timings and what the comparison did, for the statistics pages.
    assert finished["result"]["metrics"]["embedded"] == 2
    assert finished["result"]["match_metrics"]["pairs"] >= 1
    assert finished["result"]["match_metrics"]["hits"] == 1 and "seconds" in finished["result"]["match_metrics"]

    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.get_reference_by_filename(conn, cloud_brand, "hero.jpg")
        site_row = conn.execute("SELECT * FROM site_images WHERE org_id = %s", (cloud_org,)).fetchone()
    assert ref["embedding"] is not None and ref["phash_flip"] and ref["thumb_path"]
    assert ref["compared_at"] is not None
    assert site_row["embedding"] is not None and site_row["thumb_path"]
    assert ("refs", ref["thumb_path"]) in fake_storage_client.store
    assert ref["thumb_path"] == f"{cloud_org}/{cloud_brand}/thumbs/hero.jpg.jpg"


def _run_index(worker, url, org_id, brand_id):
    with cloud_db.connect(url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=brand_id, kind="index")
    assert worker.run_once()
    return _job(url, brand_id, job["id"])


def _jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_an_image_that_could_not_be_read_is_not_marked_checked_and_is_grouped_on_the_next_run(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch):
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from test_verify import scene

    from nyra.cloud import variants as variants_module

    photo = scene(40)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        for name, img in (("photo", photo), ("crop", photo.crop((90, 60, 800, 600)).resize((500, 380)))):
            _add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{name}.jpg", _jpeg_bytes(img))

    def state():
        with cloud_db.connect(cloud_database_url) as conn:
            return conn.execute("SELECT count(variant_group) AS grouped, count(variants_checked_at) AS checked "
                                "FROM site_images WHERE org_id = %s", (cloud_org,)).fetchone()

    with monkeypatch.context() as broken:
        broken.setattr(variants_module, "fetch", SimpleNamespace(decode=lambda data, max_pixels: None))
        _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert dict(state()) == {"grouped": 0, "checked": 0}  # nothing was looked at, so nothing is stamped
    _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert dict(state()) == {"grouped": 2, "checked": 2}


def test_a_comparison_groups_the_crops_of_a_photo_and_keeps_the_group_when_another_crop_arrives(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    pytest.importorskip("cv2")
    from test_verify import scene

    photo = scene(30)
    crop = photo.crop((90, 60, 800, 600)).resize((500, 380))
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        ids = [_add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{name}.jpg", _jpeg_bytes(img))
               for name, img in (("photo", photo), ("crop", crop), ("other", scene(31)))]

    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["status"] == "done", finished
    assert finished["result"]["match_metrics"]["variant_links"] == 1

    def groups():
        with cloud_db.connect(cloud_database_url) as conn:
            rows = conn.execute("SELECT id, variant_group, variants_checked_at FROM site_images WHERE org_id = %s",
                                (cloud_org,)).fetchall()
        assert all(row["variants_checked_at"] is not None for row in rows)
        return {row["id"]: row["variant_group"] for row in rows}

    first = groups()
    assert first[ids[0]] is not None and first[ids[0]] == first[ids[1]] and first[ids[2]] is None

    # A third crop of the same photo, read later, joins the same group; nothing already checked changes.
    smaller = photo.crop((0, 0, 700, 500)).resize((420, 300))
    with cloud_db.connect(cloud_database_url) as conn:
        late = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/late.jpg", _jpeg_bytes(smaller))
    _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    second = groups()
    assert second[late] == first[ids[0]] and {key: second[key] for key in ids} == first


def _reference_row(url, brand_id, name):
    with cloud_db.connect(url) as conn:
        return cloud_db.get_reference_by_filename(conn, brand_id, name)


def test_index_job_of_a_library_without_site_images_skips_the_comparison(worker, cloud_database_url, cloud_org,
                                                                        cloud_brand, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "hero.jpg", _image_bytes(0))
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["status"] == "done" and finished["message"] == "1 image(s) indexée(s)."
    assert "matches" not in finished["result"] and "match_metrics" not in finished["result"]
    assert set(finished["result"]["metrics"]) == {"embedded", "embed_seconds", "load_seconds", "model_load_seconds",
                                                  "duration_seconds"}
    assert finished["result"]["metrics"]["embedded"] == 1
    ref = _reference_row(cloud_database_url, cloud_brand, "hero.jpg")
    assert ref["embedding"] is not None and ref["phash_flip"] and ref["dhash_flip"]
    assert ref["work_path"] == f"{cloud_org}/{cloud_brand}/work/hero.jpg.jpg"
    assert ("refs", ref["work_path"]) in worker.storage_client_factory().store
    # A second pass finds nothing to do: no model, no upload, nothing indexed.
    again = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert again["message"] == "0 image(s) indexée(s)." and again["result"]["indexed"] == 0
    assert again["result"]["metrics"]["embedded"] == 0 and again["result"]["metrics"]["model_load_seconds"] == 0


def test_index_job_only_makes_what_is_missing_and_prefers_the_working_copy(worker, cloud_database_url, cloud_org,
                                                                          cloud_brand, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "hero.jpg", _image_bytes(0))
    _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    ref = _reference_row(cloud_database_url, cloud_brand, "hero.jpg")
    # Thumbnail lost, and the original with it; the embedding and the working copy are still there.
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE reference_images SET thumb_path = NULL, compared_at = now() WHERE id = %s", (ref["id"],))
    fake_storage_client.store.pop(("refs", ref["storage_path"]))
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    # The working copy is read instead of the (now missing) original, only the thumbnail is made,
    # and the comparison stamp survives because neither embedding nor flip hashes changed.
    assert finished["result"]["indexed"] == 1 and finished["result"]["metrics"]["embedded"] == 0
    after = _reference_row(cloud_database_url, cloud_brand, "hero.jpg")
    assert after["thumb_path"] and after["work_path"] == ref["work_path"] and after["compared_at"] is not None


def test_index_job_skips_an_unreadable_image_and_says_so(worker, cloud_database_url, cloud_org, cloud_brand,
                                                         fake_storage_client, caplog):
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "lost.jpg", _image_bytes(0))
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "kept.jpg", _image_bytes(50))
    lost = _reference_row(cloud_database_url, cloud_brand, "lost.jpg")
    fake_storage_client.store.pop(("refs", lost["storage_path"]))
    with caplog.at_level("WARNING", logger="nyra.worker"):
        finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["status"] == "done" and finished["result"]["indexed"] == 1
    assert f"reading refs/{lost['storage_path']} failed; image skipped" in caplog.text
    assert _reference_row(cloud_database_url, cloud_brand, "lost.jpg")["embedding"] is None
    assert _reference_row(cloud_database_url, cloud_brand, "kept.jpg")["embedding"] is not None


def _batches_of_one(worker, monkeypatch):
    real = worker.config_for

    def config_for(org_id):
        config = real(org_id)
        return replace(config, match=replace(config.match, embedding_batch_size=1))

    monkeypatch.setattr(worker, "config_for", config_for)


def test_index_job_reports_its_progress_in_order(worker, cloud_database_url, cloud_org, cloud_brand,
                                                 fake_storage_client, monkeypatch):
    _batches_of_one(worker, monkeypatch)
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "a.jpg", _image_bytes(0))
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "b.jpg", _image_bytes(60))
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/a.jpg", _image_bytes(0))
    seen = []
    real_report = worker_module.JobContext.report

    def spy(self, message, progress=None, *, force=False):
        if progress and progress.get("phase") == "index":
            seen.append((message, progress, force))
        real_report(self, message, progress, force=force)

    monkeypatch.setattr(worker_module.JobContext, "report", spy)
    _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    index = {"phase": "index", "total": 3}
    assert seen == [
        ("Chargement du modèle d'analyse (3 image(s) à indexer ; plus long la toute première fois)…",
         {**index, "done": 0}, True),
        ("Lecture des images · 0/3", {**index, "done": 0}, False),
        ("Analyse des images · 1/3", {**index, "done": 1}, False),
        ("Lecture des images · 1/3", {**index, "done": 1}, False),
        ("Analyse des images · 2/3", {**index, "done": 2}, False),
        ("Lecture des images · 2/3", {**index, "done": 2}, False),
        ("Analyse des images · 3/3", {**index, "done": 3}, False),
    ]


def test_a_cancelled_index_job_stops_between_batches_and_skips_the_comparison(worker, cloud_database_url, cloud_org,
                                                                             cloud_brand, fake_storage_client,
                                                                             monkeypatch):
    _batches_of_one(worker, monkeypatch)
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "a.jpg", _image_bytes(0))
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "b.jpg", _image_bytes(60))
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/a.jpg", _image_bytes(0))
    checks = []

    def stop_at_the_second_batch(self):
        checks.append(1)
        self.cancelled = len(checks) >= 2
        return self.cancelled

    monkeypatch.setattr(worker_module.JobContext, "should_stop", stop_at_the_second_batch)
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    # One reference done; the second reference and the site image are left for later.
    assert finished["status"] == "cancelled" and finished["message"] == "Indexation arrêtée."
    assert finished["result"]["indexed"] == 1 and "matches" not in finished["result"]
    assert len(checks) == 3  # refs loop: batch 1, batch 2 (stops); sites loop: its first check (stops)


def _site_images_sharing_a_hash(conn, storage, org_id, brand_id, count):
    """`count` site images with the same bytes (so the same content_hash) and one different image."""
    site = cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id, url="https://t.test/")
    ids = [_add_site_image(conn, storage, org_id, site, f"https://t.test/{i}.jpg", _image_bytes(0)) for i in range(count)]
    first = conn.execute("SELECT content_hash, storage_path FROM site_images WHERE id = %s", (ids[0],)).fetchone()
    digest = first["content_hash"]
    # As the crawler stores a duplicate: same bytes, same hash, same stored file.
    conn.execute("UPDATE site_images SET content_hash = %s, storage_path = %s WHERE id = ANY(%s)",
                 (digest, first["storage_path"], ids))
    _add_site_image(conn, storage, org_id, site, "https://t.test/other.jpg", _image_bytes(60))
    return ids, digest


def test_index_job_embeds_site_images_sharing_a_hash_once_and_fills_all_their_rows(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch):
    embedded = []
    monkeypatch.setattr(worker_module, "compute_clip_embeddings",
                        lambda images, config: embedded.append(len(images)) or _fake_embeddings(images, config))
    with cloud_db.connect(cloud_database_url) as conn:
        _, digest = _site_images_sharing_a_hash(conn, fake_storage_client, cloud_org, cloud_brand, 3)
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert sum(embedded) == 2  # one image per distinct hash, not four
    assert finished["result"]["indexed"] == 4 and finished["result"]["metrics"]["embedded"] == 2
    assert finished["message"] == "4 image(s) indexée(s), comparaison à jour."
    with cloud_db.connect(cloud_database_url) as conn:
        rows = conn.execute("SELECT content_hash, embedding, thumb_path FROM site_images WHERE org_id = %s",
                            (cloud_org,)).fetchall()
    assert len(rows) == 4 and all(row["embedding"] is not None for row in rows)
    assert [row["thumb_path"] for row in rows if row["content_hash"] == digest] == [f"{cloud_org}/thumbs/{digest}.jpg"] * 3


def test_index_job_embeds_a_hash_when_only_one_of_its_rows_lacks_an_embedding(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        ids, _ = _site_images_sharing_a_hash(conn, fake_storage_client, cloud_org, cloud_brand, 2)
        conn.execute("UPDATE site_images SET embedding = %s WHERE id = %s", (np.ones(512, dtype=np.float32), ids[0]))
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["result"]["indexed"] == 3 and finished["result"]["metrics"]["embedded"] == 2
    with cloud_db.connect(cloud_database_url) as conn:
        missing = conn.execute("SELECT COUNT(*) AS c FROM site_images WHERE org_id = %s AND embedding IS NULL",
                               (cloud_org,)).fetchone()["c"]
    assert missing == 0


def test_index_job_still_indexes_a_twin_whose_file_differs_from_an_unreadable_one(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    """Same hash but two files (legacy rows): losing one file must not keep the other from being indexed."""
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        a = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/a.jpg", _image_bytes(0))
        b = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/b.jpg", _image_bytes(0))
        digest = conn.execute("SELECT content_hash FROM site_images WHERE id = %s", (a,)).fetchone()["content_hash"]
        conn.execute("UPDATE site_images SET content_hash = %s WHERE id = ANY(%s)", (digest, [a, b]))
        lost = conn.execute("SELECT storage_path FROM site_images WHERE id = %s", (a,)).fetchone()["storage_path"]
    del fake_storage_client.store[("site-images", lost)]
    _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    with cloud_db.connect(cloud_database_url) as conn:
        done = conn.execute("SELECT id FROM site_images WHERE embedding IS NOT NULL").fetchall()
    assert {row["id"] for row in done} >= {b}


def _chunks_of(worker, monkeypatch, rows):
    """Candidates read `rows` at a time (one row per batch, so a chunk is `rows` batches)."""
    _batches_of_one(worker, monkeypatch)
    monkeypatch.setattr(worker_module, "INDEX_CHUNK_BATCHES", rows)


@pytest.mark.parametrize(("rows", "fetches"), [(1, 8), (3, 4), (100, 2)], ids=["one-row", "boundary-at-count", "one-chunk"])
def test_index_job_reads_its_candidates_chunk_by_chunk(worker, cloud_database_url, cloud_org, cloud_brand,
                                                       fake_storage_client, monkeypatch, rows, fetches):
    """3 references + 3 site images. A chunk the size of the row count costs one more (empty) query; a short one ends."""
    _chunks_of(worker, monkeypatch, rows)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        for i in range(3):
            _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, f"r{i}.jpg", _image_bytes(i * 40))
            _add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{i}.jpg", _image_bytes(i * 40))
    queries, messages = [], []
    real_execute, real_report = psycopg.Cursor.execute, worker_module.JobContext.report
    monkeypatch.setattr(psycopg.Cursor, "execute",
                        lambda self, query, *args, **kwargs: queries.append(query) or real_execute(self, query, *args, **kwargs))
    monkeypatch.setattr(worker_module.JobContext, "report",
                        lambda self, message, *args, **kwargs: messages.append(message) or real_report(self, message, *args, **kwargs))
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["message"] == "6 image(s) indexée(s), comparaison à jour."
    assert finished["result"]["indexed"] == 6 and finished["result"]["metrics"]["embedded"] == 6
    assert sum("id > %s ORDER BY" in query for query in queries if isinstance(query, str)) == fetches
    assert [m for m in messages if m.startswith("Analyse")][-1] == "Analyse des images · 6/6"
    with cloud_db.connect(cloud_database_url) as conn:
        missing = conn.execute("""SELECT (SELECT COUNT(*) FROM reference_images WHERE org_id = %(o)s AND embedding IS NULL)
                                       + (SELECT COUNT(*) FROM site_images WHERE org_id = %(o)s AND embedding IS NULL) AS c""",
                               {"o": cloud_org}).fetchone()["c"]
    assert missing == 0


def test_index_job_ends_when_images_can_never_be_indexed_and_chunks_are_small(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch):
    """Unreadable images stay pending for ever: the chunks must move past them (keyset on id), not re-read them."""
    _chunks_of(worker, monkeypatch, 2)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        for i in range(6):
            _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, f"r{i}.jpg", _image_bytes(i * 10))
        site_ids = [_add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{i}.jpg",
                                    _image_bytes(i * 10)) for i in range(5)]
        for name in ("r1.jpg", "r4.jpg"):
            fake_storage_client.store.pop(("refs", f"{cloud_org}/{cloud_brand}/{name}"))
        lost_site = conn.execute("SELECT storage_path FROM site_images WHERE id = %s", (site_ids[2],)).fetchone()
        fake_storage_client.store.pop(("site-images", lost_site["storage_path"]))
    checks = []

    def guard(self):  # a job that loops would run for ever: fail instead
        checks.append(1)
        assert len(checks) < 100, "the index job does not end"
        return False

    monkeypatch.setattr(worker_module.JobContext, "should_stop", guard)
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["status"] == "done", finished
    assert finished["result"]["indexed"] == 4 + 4 and finished["result"]["metrics"]["embedded"] == 8
    with cloud_db.connect(cloud_database_url) as conn:
        left = {row["filename"] for row in conn.execute(
            "SELECT filename FROM reference_images WHERE org_id = %s AND embedding IS NULL", (cloud_org,)).fetchall()}
        left_sites = conn.execute("SELECT id FROM site_images WHERE org_id = %s AND embedding IS NULL",
                                  (cloud_org,)).fetchall()
    assert left == {"r1.jpg", "r4.jpg"} and [row["id"] for row in left_sites] == [site_ids[2]]


@pytest.mark.parametrize("rows", [1, 2, 100])
def test_index_job_indexes_twins_whatever_the_chunk_boundary(worker, cloud_database_url, cloud_org, cloud_brand,
                                                             fake_storage_client, monkeypatch, rows):
    """Twins (same hash and file) in different chunks: the head's UPDATE fills the later ones, so they are not
    fetched again and the file is embedded once. The reported count only includes the twins that shared the
    head's chunk; the rows themselves are all indexed."""
    _chunks_of(worker, monkeypatch, rows)
    with cloud_db.connect(cloud_database_url) as conn:
        _, digest = _site_images_sharing_a_hash(conn, fake_storage_client, cloud_org, cloud_brand, 3)
    finished = _run_index(worker, cloud_database_url, cloud_org, cloud_brand)
    assert finished["status"] == "done" and finished["result"]["metrics"]["embedded"] == 2
    indexed = finished["result"]["indexed"]
    assert indexed >= 2 and (rows < 100 or indexed == 4)
    with cloud_db.connect(cloud_database_url) as conn:
        found = conn.execute("SELECT content_hash, embedding, thumb_path FROM site_images WHERE org_id = %s",
                             (cloud_org,)).fetchall()
    assert len(found) == 4 and all(row["embedding"] is not None and row["thumb_path"] for row in found)
    assert {row["thumb_path"] for row in found if row["content_hash"] == digest} == {f"{cloud_org}/thumbs/{digest}.jpg"}


@pytest.mark.parametrize("rows", [1, 2, 100])
def test_index_job_embeds_the_twin_lacking_an_embedding_whatever_the_chunk_boundary(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch, rows):
    """A twin that already has its embedding is no reason to skip the one that has none, in any chunk order."""
    _chunks_of(worker, monkeypatch, rows)
    with cloud_db.connect(cloud_database_url) as conn:
        ids, _ = _site_images_sharing_a_hash(conn, fake_storage_client, cloud_org, cloud_brand, 2)
        conn.execute("UPDATE site_images SET embedding = %s WHERE id = %s", (np.ones(512, dtype=np.float32), ids[0]))
    assert _run_index(worker, cloud_database_url, cloud_org, cloud_brand)["status"] == "done"
    with cloud_db.connect(cloud_database_url) as conn:
        missing = conn.execute("SELECT COUNT(*) AS c FROM site_images WHERE org_id = %s AND (embedding IS NULL "
                               "OR thumb_path IS NULL)", (cloud_org,)).fetchone()["c"]
    assert missing == 0


def test_report_job_stores_files_without_false_positives(worker, cloud_database_url, cloud_org, cloud_brand,
                                                         fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        kept = _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "kept.jpg", _image_bytes(1))
        dismissed = _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "dismissed.jpg", _image_bytes(40))
        img_a = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/a.jpg", _image_bytes(1))
        img_b = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/b.jpg", _image_bytes(40))
        cloud_db.write_matches(conn, cloud_org, [(kept, img_a, "phash", 1.0, "haut"), (dismissed, img_b, "phash", 1.0, "haut")])
        cloud_db.set_reviews(conn, brand_id=cloud_brand, reference_id=dismissed, site_image_ids=[img_b],
                             decision="ecarte", reviewed_by=None)
        cloud_db.set_match_signature(conn, cloud_org, cloud_brand, signature(MatchConfig(), True, verified=True))
        other = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://u.test/")
        for site_id, status, started in ((site, "done", "2026-01-01"), (site, "cancelled", "2026-02-05"),
                                         (other, "done", "2026-01-20"), (other, "error", "2026-02-06")):
            conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at) VALUES (%s, %s, %s, %s)",
                         (cloud_org, site_id, status, started))
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="report", params={"within_days": 90})

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done", finished
    with cloud_db.connect(cloud_database_url) as conn:
        report = cloud_db.get_report(conn, cloud_brand, uuid.UUID(finished["result"]["report_id"]))
    html = fake_storage_client.store[("reports", report["storage_path_html"])].decode("utf-8")
    csv_text = fake_storage_client.store[("reports", report["storage_path_csv"])].decode("utf-8")
    for text in (html, csv_text):  # the settings the matches were produced with travel with the report
        assert "ViT-B-32/laion2b_s34b_b79k" in text and "0.92 / 0.85 / 0.75" in text
        # Finished reads only (a cancelled or failed one is no read), each site at its own last day: a range, not the newest.
        assert "du 01/01/2026 au 20/01/2026" in text and "05/02/2026" not in text and "06/02/2026" not in text
    assert "kept.jpg" in html and "dismissed.jpg" not in html
    assert "data:image/jpeg;base64," in html  # thumbnails inlined, the file stands alone
    assert report["stats"]["expired_online"] == 1


def test_crawl_of_a_private_address_fails_cleanly(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    monkeypatch.delenv("NYRA_ALLOW_PRIVATE_HOSTS", raising=False)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="http://127.0.0.1:8000/")
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="crawl",
                                 params={"site_ids": [str(site)]})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error"
    assert finished["message"].startswith("Adresse refusée")
    with cloud_db.connect(cloud_database_url) as conn:
        run = cloud_db.list_crawl_runs(conn, cloud_brand)[0]
    assert run["status"] == "error"


def test_crawl_queued_before_brands_existed_files_its_address_under_the_brand(worker, cloud_database_url, cloud_org,
                                                                              cloud_brand, monkeypatch):
    monkeypatch.delenv("NYRA_ALLOW_PRIVATE_HOSTS", raising=False)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="crawl",
                                 params={"site": "http://127.0.0.1:8000/"})
    assert worker.run_once()
    assert _job(cloud_database_url, cloud_brand, job["id"])["status"] == "error"
    with cloud_db.connect(cloud_database_url) as conn:
        assert [row["url"] for row in cloud_db.list_sites(conn, cloud_brand)] == ["http://127.0.0.1:8000/"]


def test_crawl_of_a_brand_without_addresses_fails_with_a_message(worker, cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="crawl", params={"site_ids": []})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error"
    assert finished["message"] == "Aucune adresse à lire pour cette marque."


def test_report_title_skips_a_brand_named_like_its_organization():
    assert worker_module.report_title({"name": "Rémy Martin"}, {"name": "Rémy Martin"}) == "Rémy Martin"
    assert worker_module.report_title({"name": "Rémy Martin"}, {"name": "Louis XIII"}) == "Rémy Martin · Louis XIII"


def test_unexpected_failure_is_recorded_and_the_worker_survives(worker, cloud_database_url, cloud_org, cloud_brand,
                                                               monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(worker_module, "run_matching", boom)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error"
    assert finished["message"] == "La tâche a échoué."
    assert "disk on fire" in finished["error"]
    assert worker.run_once() is False


def test_a_missing_chromium_is_explained_instead_of_a_traceback():
    missing = Exception("BrowserType.launch: Executable doesn't exist at C:/x/chrome-headless-shell.exe")
    assert "python -m playwright install chromium" in worker_module.browser_problem(missing)
    assert worker_module.browser_problem(RuntimeError("disk on fire")) is None


def test_locate_job_finds_copies_crops_and_their_group_mates_and_replaces_its_previous_result(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    pytest.importorskip("cv2")
    from test_verify import jpeg, scene

    photo = scene(40)
    tight = photo.crop((300, 200, 560, 400))
    group = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        ref = _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "hero.jpg", _jpeg_bytes(photo))
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        ids = {name: _add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{name}.jpg", _jpeg_bytes(img))
               for name, img in (("copy", jpeg(photo.resize((600, 433)))), ("crop", jpeg(tight.resize((520, 400)))),
                                 ("mate", scene(41)), ("other", scene(42)), ("gone", scene(43)))}
        conn.execute("UPDATE site_images SET variant_group = %s WHERE id = ANY(%s)", (group, [ids["crop"], ids["mate"]]))
        path = conn.execute("SELECT storage_path FROM site_images WHERE id = %s", (ids["gone"],)).fetchone()["storage_path"]
        del fake_storage_client.store[("site-images", path)]  # a missing object: counted, never fatal
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="locate", params={"reference_id": str(ref)})

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done", finished
    assert finished["result"] == {"searched": 5, "unreadable": 1, "found": 3, "found_direct": 2}

    def saved():
        with cloud_db.connect(cloud_database_url) as conn:
            rows = conn.execute("SELECT site_image_id, tier, inliers FROM reference_locations WHERE reference_id = %s", (ref,)).fetchall()
        return {row["site_image_id"]: (row["tier"], row["inliers"]) for row in rows}

    found = saved()
    assert set(found) == {ids["copy"], ids["crop"], ids["mate"]}
    assert found[ids["copy"]][0] == "same" and found[ids["crop"]][0] == "review"
    assert found[ids["mate"]] == ("review", 0)  # same photo as the crop, no figures of its own
    assert _reference_row(cloud_database_url, cloud_brand, "hero.jpg")["located_at"] is not None

    # A second search replaces the first: an image that stopped being a hit disappears.
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE site_images SET variant_group = NULL WHERE id = ANY(%s)", (list(ids.values()),))
        conn.execute("DELETE FROM site_images WHERE id = %s", (ids["copy"],))
        cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="locate", params={"reference_id": str(ref)})
    assert worker.run_once()
    assert set(saved()) == {ids["crop"]}


def test_locate_job_says_why_it_cannot_search(worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="locate", params={"reference_id": str(uuid.uuid4())})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error" and finished["error"] == "Référence introuvable."


def test_locate_job_leaves_out_excluded_images_and_refuses_to_save_for_a_replaced_reference(
        worker, cloud_database_url, cloud_org, cloud_brand, fake_storage_client, monkeypatch):
    pytest.importorskip("cv2")
    from test_verify import jpeg, scene

    from nyra.cloud import locate as locate_module

    photo = scene(50)
    with cloud_db.connect(cloud_database_url) as conn:
        ref = _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "hero.jpg", _jpeg_bytes(photo))
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        kept = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/kept.jpg", _jpeg_bytes(jpeg(photo.crop((300, 200, 560, 400)).resize((520, 400)))))
        aside = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/aside.jpg",
                                _jpeg_bytes(jpeg(photo.resize((600, 433)))))  # exclusions reach every copy, not every crop
        cloud_db.add_exclusion(conn, cloud_org, cloud_brand, aside, reason=None, created_by=None)
        cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="locate", params={"reference_id": str(ref)})
    assert worker.run_once()
    with cloud_db.connect(cloud_database_url) as conn:
        found = {row["site_image_id"] for row in conn.execute("SELECT site_image_id FROM reference_locations WHERE reference_id = %s", (ref,)).fetchall()}
    assert found == {kept}  # an image set aside as a recurring false positive is never searched

    # The reference is replaced while the search runs: nothing is saved for the old picture, and the message says why.
    real_locate = locate_module.verify.locate

    def replaced_meanwhile(*args, **kwargs):
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("UPDATE reference_images SET phash = %s WHERE id = %s", ("0" * 16, ref))
        return real_locate(*args, **kwargs)

    monkeypatch.setattr(locate_module.verify, "locate", replaced_meanwhile)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="locate", params={"reference_id": str(ref)})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error" and "remplacée" in finished["error"]
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM reference_locations WHERE reference_id = %s", (ref,)).fetchone()["n"] == 1  # the first result stands


def test_supervise_restarts_a_worker_that_exits_and_stops_when_asked():
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    from nyra.cloud.worker import supervise

    stop = threading.Event()
    flag = Path(tempfile.mkdtemp()) / "starts"
    code = f"open({str(flag)!r}, 'a').write('x'); import sys; sys.exit(1)"
    thread = threading.Thread(target=supervise, args=([sys.executable, "-c", code], stop, 0.1))
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline and (not flag.exists() or len(flag.read_text()) < 2):
        time.sleep(0.2)
    stop.set()
    thread.join(timeout=40)
    assert not thread.is_alive()
    assert len(flag.read_text()) >= 2, "started again after exiting"


def test_a_check_job_reads_only_the_pages_given_and_records_no_crawl_run(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        for path in ("/a", "/b"):
            conn.execute("INSERT INTO pages (org_id, site_id, url, status) VALUES (%s, %s, %s, 'done')", (cloud_org, site, f"https://t.test{path}"))
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="check", params={"pages": {str(site): ["https://t.test/a"]}})
        runs_before = conn.execute("SELECT count(*) AS n FROM crawl_runs WHERE site_id = %s", (site,)).fetchone()["n"]

    asked = []

    def fake_crawl_pages(site_url, urls, store, config, **_kwargs):
        asked.append((site_url, list(urls)))
        page = store.upsert_page(urls[0])  # a real read opens the page, stamps what it shows, then marks it read
        store.mark_page(page, 200)
        return worker_module.CrawlStats(pages_visited=1)

    monkeypatch.setattr(worker_module, "crawl_pages", fake_crawl_pages)
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done", finished
    assert asked == [("https://t.test/", ["https://t.test/a"])]  # /b was not asked for
    assert finished["result"] == {"pages_read": 1, "pages_asked": 1, "pages_failed": 0, "pages_redirected": 0, "blocked_by_robots": 0, "errors": []}
    assert finished["message"] == "1 page(s) relue(s) sur 1."
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM crawl_runs WHERE site_id = %s", (site,)).fetchone()["n"] == runs_before
        page = conn.execute("SELECT links_tracked, read_started_at FROM pages WHERE site_id = %s AND url = 'https://t.test/a'", (site,)).fetchone()
        untouched = conn.execute("SELECT read_started_at FROM pages WHERE site_id = %s AND url = 'https://t.test/b'", (site,)).fetchone()
    assert page["links_tracked"] is True and page["read_started_at"] is not None  # the read began before its links were stamped
    assert untouched["read_started_at"] is None


def test_a_check_job_with_no_known_site_fails_with_a_message(worker, cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="check", params={"pages": {str(uuid.uuid4()): ["https://t.test/a"]}})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "error" and "Aucune page" in finished["message"]


def test_a_check_says_what_kept_pages_from_being_read():
    stats = worker_module.CrawlStats(pages_visited=4, pages_failed=1, pages_redirected=1, blocked_by_robots=2)
    assert worker_module._check_message(False, 6, stats) == "2 page(s) relue(s) sur 6 ; 1 en échec, 1 redirigée(s) ailleurs, 2 bloquée(s) par robots.txt."
    assert worker_module._check_message(False, 3, worker_module.CrawlStats(pages_visited=3)) == "3 page(s) relue(s) sur 3."
    assert "arrêtée" in worker_module._check_message(True, 3, worker_module.CrawlStats())


def _human_decisions(url, org_id) -> list:
    """Everything a person decides in this brand, as stored: the decisions on matches, the exclusions, the logo settings."""
    with cloud_db.connect(url) as conn:
        return [
            [dict(row) for row in conn.execute(query, (org_id,)).fetchall()]
            for query in ("SELECT * FROM reviews WHERE org_id = %s ORDER BY reference_id, site_image_id",
                          "SELECT * FROM excluded_hashes WHERE org_id = %s ORDER BY id",
                          "SELECT * FROM image_labels WHERE org_id = %s ORDER BY label_id, site_image_id",
                          "SELECT * FROM page_replacements WHERE org_id = %s ORDER BY reference_id, page_id",
                          "SELECT id, set_aside_logos FROM brands WHERE org_id = %s ORDER BY id",
                          "SELECT id FROM site_images WHERE org_id = %s AND logo_kept ORDER BY id")
        ]


def test_no_automatic_job_takes_a_decision_for_a_person(worker, cloud_database_url, cloud_org, cloud_brand,
                                                       fake_storage_client, monkeypatch):
    """The algorithm proposes, a person decides: a full run of the jobs (index + comparison + grouping of crops,
    full recomputation, locate, check) leaves every decision a person took exactly as it was (decisions on matches,
    exclusions, content labels, pages marked replaced, the logo setting and the logos put back; the logo *scores* are the
    job's own work and are not compared), and puts none on the matches it finds. The crawl needs Chromium and is covered by the static check in test_units."""
    pytest.importorskip("cv2")
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        refs = {n: _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, f"r{n}.jpg", _image_bytes(n)) for n in (0, 40)}
        images = {n: _add_site_image(conn, fake_storage_client, cloud_org, site, f"https://t.test/{n}.jpg", _image_bytes(n))
                  for n in (0, 40, 120)}
    assert _run_index(worker, cloud_database_url, cloud_org, cloud_brand)["status"] == "done"

    with cloud_db.connect(cloud_database_url) as conn:  # what people do from the interface
        cloud_db.set_reviews(conn, brand_id=cloud_brand, reference_id=refs[0], site_image_ids=[images[0]], decision="ecarte", reviewed_by=None)
        cloud_db.set_reviews(conn, brand_id=cloud_brand, reference_id=refs[40], site_image_ids=[images[40]], decision="retenu", reviewed_by=None)
        cloud_db.add_exclusion(conn, cloud_org, cloud_brand, images[120], reason="logo", created_by=None)
        cloud_logos.set_enabled(conn, cloud_org, cloud_brand, True)
        cloud_logos.keep(conn, cloud_brand, [images[40]], True)
        label = cloud_labels.add_content_label(conn, cloud_org, cloud_brand, "carafe")
        cloud_labels.set_decision(conn, label, cloud_brand, "yes", site_image_ids=[images[0]], reference_ids=[], user_id=None)
        page = conn.execute("SELECT page_id FROM image_pages WHERE image_id = %s", (images[0],)).fetchone()["page_id"]
        cloud_presence.set_replaced(conn, cloud_org, refs[0], [page], replaced=True, user_id=None)
    before = _human_decisions(cloud_database_url, cloud_org)
    assert [len(table) for table in before] == [2, 2, 1, 1, 1, 1]  # the decisions are really there to be kept

    with cloud_db.connect(cloud_database_url) as conn:  # new material, and a signature that forces a full recomputation
        _add_reference(conn, fake_storage_client, cloud_org, cloud_brand, "r80.jpg", _image_bytes(80))
        _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/80.jpg", _image_bytes(80))
        cloud_db.set_match_signature(conn, cloud_org, cloud_brand, "stale")
    monkeypatch.setattr(worker_module, "crawl_pages", lambda *args, **kwargs: worker_module.CrawlStats())
    with cloud_db.connect(cloud_database_url) as conn:
        for kind, params in (("index", {}), ("match", {}), ("locate", {"reference_id": str(refs[0])}),
                             ("check", {"pages": {str(site): ["https://t.test/80.jpg/page"]}})):
            cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind=kind, params=params)
    for _ in range(4):
        assert worker.run_once()
    with cloud_db.connect(cloud_database_url) as conn:
        statuses = [row["status"] for row in conn.execute("SELECT status FROM jobs WHERE brand_id = %s", (cloud_brand,)).fetchall()]
        new = conn.execute("""SELECT count(*) AS n FROM matches m LEFT JOIN reviews v USING (reference_id, site_image_id)
                              WHERE m.org_id = %s AND v.decision IS NULL""", (cloud_org,)).fetchone()["n"]
    assert statuses.count("done") == 5, statuses
    assert new >= 1  # the new pair was proposed, undecided
    assert _human_decisions(cloud_database_url, cloud_org) == before
def _read_pages_of_a_fake_site(monkeypatch, available: int = 40):
    """A site with `available` pages: a read opens as many as it is allowed, as the browser would."""
    def fake_crawl_site(url, store, config, *, max_pages, progress=None, **_kwargs):
        pages = min(max_pages, available)
        stats = worker_module.CrawlStats(pages_visited=pages, pages_queued=available - pages, images_new=pages // 2, bytes_downloaded=pages * 1000)
        if progress:
            progress(stats)
        return stats

    monkeypatch.setattr(worker_module, "crawl_site", fake_crawl_site)


def _crawl_all(worker, url, org_id, brand_id):
    with cloud_db.connect(url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=org_id, brand_id=brand_id, kind="crawl", params={"then_match": False, "fresh": True})
    assert worker.run_once()
    return _job(url, brand_id, job["id"])


def _pages_counted(url, org_id):
    with cloud_db.connect(url) as conn:
        return cloud_usage.pages_used(conn, Config().quota, org_id)[0]


def test_a_read_stops_at_the_months_page_limit_and_the_counter_is_the_pages_really_read(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    _read_pages_of_a_fake_site(monkeypatch)
    with cloud_db.connect(cloud_database_url) as conn:
        other = cloud_db.create_organization(conn, name="Other", slug=f"other-{uuid.uuid4().hex[:10]}")
        other_brand = conn.execute("SELECT id FROM brands WHERE org_id = %s", (other,)).fetchone()["id"]
        for org, brand in ((cloud_org, cloud_brand), (other, other_brand)):
            for n in (1, 2):
                cloud_db.create_site(conn, org_id=org, brand_id=brand, url=f"https://t{n}.test/")
        cloud_usage.set_overrides(conn, cloud_org, {"pages_per_month": 100})

    first = _crawl_all(worker, cloud_database_url, cloud_org, cloud_brand)  # 40 + 40 pages
    assert first["status"] == "done" and first["result"]["pages_visited"] == 80 and not first["result"]["quota_reached"]
    assert "Usage du mois : 80 pages lues sur 100" in first["message"]  # the warning from 80 %
    with cloud_db.connect(cloud_database_url) as conn:
        runs = conn.execute("SELECT pages_visited FROM crawl_runs WHERE org_id = %s", (cloud_org,)).fetchall()
        counters = {r["kind"]: r["amount"] for r in conn.execute("SELECT kind, amount FROM usage_counters WHERE org_id = %s", (cloud_org,)).fetchall()}
    assert _pages_counted(cloud_database_url, cloud_org) == sum(r["pages_visited"] for r in runs) == 80
    assert counters == {"pages_read": 80, "images_new": 40, "bytes_downloaded": 80_000}

    second = _crawl_all(worker, cloud_database_url, cloud_org, cloud_brand)  # 20 pages are left: read them, then stop
    assert second["result"]["pages_visited"] == 20 and second["result"]["quota_reached"] is True
    assert "Limite d'usage atteinte" in second["message"]
    assert _pages_counted(cloud_database_url, cloud_org) == 100

    third = _crawl_all(worker, cloud_database_url, cloud_org, cloud_brand)  # nothing is left: nothing is read
    assert third["result"]["pages_visited"] == 0 and third["result"]["quota_reached"] is True
    assert _pages_counted(cloud_database_url, cloud_org) == 100

    other_read = _crawl_all(worker, cloud_database_url, other, other_brand)  # another organization is not affected
    assert other_read["result"]["pages_visited"] == 80 and not other_read["result"]["quota_reached"]
    assert _pages_counted(cloud_database_url, other) == 80 and _pages_counted(cloud_database_url, cloud_org) == 100


def test_a_page_check_reads_only_the_pages_that_fit_in_the_limit_and_counts_them(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        cloud_usage.set_overrides(conn, cloud_org, {"pages_per_month": 3})
        cloud_usage.record(conn, cloud_org, pages=1)
        urls = [f"https://t.test/{n}" for n in range(5)]
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="check", params={"pages": {str(site): urls}})
    asked = []

    def fake_crawl_pages(site_url, wanted, store, config, **_kwargs):
        asked.append(list(wanted))
        return worker_module.CrawlStats(pages_visited=len(wanted))

    monkeypatch.setattr(worker_module, "crawl_pages", fake_crawl_pages)
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert asked == [urls[:2]] and "Limite d'usage atteinte" in finished["message"]
    assert _pages_counted(cloud_database_url, cloud_org) == 3


def test_a_site_that_ends_exactly_at_the_limit_is_not_reported_as_cut_and_a_full_storage_stops_the_read(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    _read_pages_of_a_fake_site(monkeypatch)
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t1.test/")
        cloud_usage.set_overrides(conn, cloud_org, {"pages_per_month": 40})
    exact = _crawl_all(worker, cloud_database_url, cloud_org, cloud_brand)  # 40 pages exist, 40 are allowed
    assert exact["result"]["pages_visited"] == 40 and exact["result"]["quota_reached"] is False
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_usage.set_overrides(conn, cloud_org, {"pages_per_month": 1000, "storage_gb": 0})
    full = _crawl_all(worker, cloud_database_url, cloud_org, cloud_brand)  # storage full: no page is read
    assert full["result"]["pages_visited"] == 0 and full["result"]["quota_reached"] is True
# --- a read that looks incomplete never says an image is gone ---------------------------------------------

def _simulated_site(pages):
    """A `crawl_site` standing for a site: `{path: (HTTP status, [image paths shown])}`; it opens, stamps and marks pages as the real one."""
    def crawl(_site_url, store, _config, **_kwargs):
        stats = worker_module.CrawlStats()
        for path, (status, images) in pages.items():
            page = store.upsert_page(f"https://t.test{path}")
            if status == 200:
                for image in store.known_images([f"https://t.test{name}" for name in images]).values():
                    store.link(image, page)
                stats.pages_ok += 1
            store.mark_page(page, status)
            stats.pages_visited += 1
            stats.http_statuses[str(status)] = stats.http_statuses.get(str(status), 0) + 1
        return stats
    return crawl


def _visual_site(conn, org, brand):
    """A site with 10 images on two pages (/a: 0-4, /b: 5-9); one visual, shown by image 0 and image 5 and confirmed."""
    site = cloud_db.create_site(conn, org_id=org, brand_id=brand, url="https://t.test/")
    ref = cloud_db.upsert_reference_image(conn, org_id=org, brand_id=brand, filename="v.jpg", storage_path=f"{org}/v.jpg",
                                          expiry_date="2026-12-01", credit=None, notes=None, phash="ffff0000ffff0000", dhash="ffff0000ffff0000")
    ids = [conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, %s, 'x', 'ffff0000ffff0000', 'ffff0000ffff0000') RETURNING id",
                        (org, site, f"https://t.test/i{n}")).fetchone()["id"] for n in range(10)]
    cloud_db.write_matches(conn, org, [(ref, ids[0], "phash", 1.0, "haut"), (ref, ids[5], "phash", 1.0, "haut")])
    cloud_db.set_reviews(conn, brand_id=brand, reference_id=ref, site_image_ids=[ids[0], ids[5]], decision="retenu", reviewed_by=None)
    return site


def _read(worker, monkeypatch, org, brand, site, pages):

    monkeypatch.setattr(worker_module, "crawl_site", _simulated_site(pages))
    with cloud_db.connect(worker.database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=org, brand_id=brand, kind="crawl", params={"site_ids": [str(site)], "then_match": False})
    assert worker.run_once()
    return _outcome(worker.database_url, brand, _job(worker.database_url, brand, job["id"]))


def _outcome(url, brand, job=None):
    from nyra.cloud import presence

    with cloud_db.connect(url) as conn:
        run = cloud_db.list_crawl_runs(conn, brand)[0]
        visual = presence.visuals(presence.by_visual(conn, brand))[0]
        states = {row["url"][-2:]: row["state"] for row in presence.pages(conn, brand, visual["row"]["reference_id"])}
    return run, visual, states, job


NORMAL = {"/a": (200, ["/i0", "/i1", "/i2", "/i3", "/i4"]), "/b": (200, ["/i5", "/i6", "/i7", "/i8", "/i9"])}
# What the same read would have said without the guard: the visual is on neither page, so both are `gone`.
WITHOUT_I0_I5 = {"/a": (200, ["/i1", "/i2", "/i3", "/i4"]), "/b": (200, ["/i6", "/i7", "/i8", "/i9"])}


@pytest.mark.parametrize("scenario, pages, suspect", [
    ("empty pages answering 200", {"/a": (200, []), "/b": (200, [])}, True),
    ("blocked with 403", {"/a": (403, []), "/b": (200, [])}, True),
    ("a partial read", {"/a": (200, ["/i1", "/i2", "/i3", "/i4"])}, True),
    ("a normal read of a site that lost the visual", WITHOUT_I0_I5, False),
])
def test_a_suspect_read_marks_nothing_removed_and_a_normal_one_raises_no_alert(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch, scenario, pages, suspect):
    with cloud_db.connect(cloud_database_url) as conn:
        site = _visual_site(conn, cloud_org, cloud_brand)
    first, _, states, _ = _read(worker, monkeypatch, cloud_org, cloud_brand, site, NORMAL)
    assert (first["suspect"], states) == (False, {"/a": "still", "/b": "still"})  # the first read has nothing to be compared with
    second, _, _, _ = _read(worker, monkeypatch, cloud_org, cloud_brand, site, NORMAL)
    assert (second["suspect"], second["images_known_seen"]) == (False, 10)  # a normal read raises no alert
    run, visual, states, job = _read(worker, monkeypatch, cloud_org, cloud_brand, site, pages)

    assert run["suspect"] is suspect, scenario
    assert ("semble incomplète" in job["message"]) is suspect
    if suspect:
        assert run["suspect_reason"] and "gone" not in states.values() and not visual["replaced"]  # pages stay unknown, no visual is replaced
        with cloud_db.connect(cloud_database_url) as conn:  # the barrier is what holds: without it a page would be gone
            conn.execute("UPDATE crawl_runs SET suspect = false WHERE id = %s", (run["id"],))
        assert "gone" in _outcome(cloud_database_url, cloud_brand)[2].values(), scenario
    else:
        assert states == {"/a": "gone", "/b": "gone"} and visual["replaced"]  # a trusted read still says the visual left


def test_a_read_that_did_not_end_properly_cannot_make_a_page_gone_but_a_check_after_a_clean_read_can(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    with cloud_db.connect(cloud_database_url) as conn:
        site = _visual_site(conn, cloud_org, cloud_brand)
    _read(worker, monkeypatch, cloud_org, cloud_brand, site, NORMAL)
    _read(worker, monkeypatch, cloud_org, cloud_brand, site, NORMAL)
    # A check of /a alone after the clean read: the visual's image 0 is no longer there, no crawl run is involved.
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE pages SET crawled_at = now(), read_started_at = now(), links_tracked = true WHERE site_id = %s AND url LIKE '%%/a'", (site,))
    assert _outcome(cloud_database_url, cloud_brand)[2]["/a"] == "gone"  # as before this guard
    for status in ("cancelled", "error", "running"):
        with cloud_db.connect(cloud_database_url) as conn:
            conn.execute("DELETE FROM crawl_runs WHERE site_id = %s AND status <> 'done'", (site,))
            clean = "(SELECT max(started_at) FROM crawl_runs WHERE site_id = %s AND status = 'done')"
            ends = "NULL" if status == "running" else clean + " + interval '3 seconds'"
            conn.execute(f"INSERT INTO crawl_runs (org_id, site_id, status, started_at, finished_at) VALUES (%s, %s, %s, {clean} + interval '1 second', {ends})",
                         (cloud_org, site, status, site, *([] if status == "running" else [site])))
            conn.execute(f"UPDATE pages SET crawled_at = {clean} + interval '2 seconds' WHERE site_id = %s AND url LIKE '%%/a'", (site, site))  # read by that run
        states = _outcome(cloud_database_url, cloud_brand)[2]
        assert states["/a"] == "unknown", status  # a run that was never judged read it
        assert not _outcome(cloud_database_url, cloud_brand)[1]["replaced"]


def test_a_site_that_renews_many_images_is_not_suspect_but_a_real_fall_is(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://t.test/")
        for n in range(100):  # 40 images known for a while, 60 renewed by the last read
            conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, first_seen, last_seen) VALUES (%s, %s, %s, 'x', now() - make_interval(days => %s), now())",
                         (cloud_org, site, f"https://t.test/i{n}", 5 if n < 40 else 0))
        for _ in range(2):  # earlier reads: 100 seen, 40 of them known
            conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at, pages_ok, images_seen, images_known_seen) VALUES (%s, %s, 'done', now() - interval '2 days', 5, 100, 40)", (cloud_org, site))
        conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at, pages_ok, images_seen, images_known_seen) VALUES (%s, %s, 'done', now() - interval '1 day', 0, 0, 0)", (cloud_org, site))  # an empty resume: ignored
        stats = worker_module.CrawlStats(pages_visited=5, pages_ok=5, http_statuses={"200": 5})
        run = cloud_db.start_crawl_run(conn, org_id=cloud_org, site_id=site, triggered_by=None)
        conn.execute("UPDATE crawl_runs SET started_at = now() - interval '1 hour' WHERE id = %s", (run,))
        assert cloud_db.record_read_health(conn, run, stats) is None  # 40 known images seen again, as in the earlier reads
        conn.execute("UPDATE site_images SET last_seen = now() - interval '2 hours' WHERE url ~ 'i([0-9]|[12][0-9])$'")  # 30 known images no longer seen
        assert "10 images déjà connues" in cloud_db.record_read_health(conn, run, stats)


# --- guard rails: duration per kind, watchdog, leftovers, resources ---------------------------------------------

def test_every_job_kind_has_its_own_duration_limit_that_an_override_can_change(monkeypatch):
    assert set(worker_module.JOB_LIMITS) == cloud_jobs.KINDS
    limits = {kind: worker_module.job_limit_seconds(kind) for kind in cloud_jobs.KINDS}
    assert limits["crawl"] == max(limits.values()) and limits["index"] > limits["match"] > limits["check"]
    message = worker_module.overrun_message("check")
    assert "dépassé sa durée maximale de 30 min" in message and "conservé" in message and "relancez-la" in message
    monkeypatch.setenv("NYRA_JOB_LIMIT_CHECK_MIN", "45")
    assert worker_module.job_limit_seconds("check") == 45 * 60 and "45 min" in worker_module.overrun_message("check")
    for bad in ("0", "-3", "abc", "100000", "1.5"):  # out of range or not a whole number: the default stays
        monkeypatch.setenv("NYRA_JOB_LIMIT_CHECK_MIN", bad)
        assert worker_module.job_limit_seconds("check") == limits["check"]


class _Exit(SystemExit):
    """Raised by the simulated `os._exit`, to leave the thread's loop."""


def _claimed_check(cloud_database_url, org_id, brand_id):
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.enqueue(conn, org_id=org_id, brand_id=brand_id, kind="check", params={})
        return cloud_jobs.claim(conn)


def _exit_recorder(monkeypatch):
    codes: list[int] = []

    def fake_exit(code):
        codes.append(code)
        raise _Exit

    monkeypatch.setattr(worker_module.os, "_exit", fake_exit)
    return codes


def test_a_job_over_its_limit_is_marked_failed_with_a_clear_message_its_runs_are_closed_and_the_worker_exits(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    codes = _exit_recorder(monkeypatch)
    job = _claimed_check(cloud_database_url, cloud_org, cloud_brand)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://over.example")
        run = cloud_db.start_crawl_run(conn, org_id=cloud_org, site_id=site, triggered_by=None, job_id=uuid.UUID(job["id"]))
    ctx = worker_module.JobContext(cloud_database_url, job)
    ctx.close()  # no thread: the test calls what the thread would
    with pytest.raises(_Exit):
        ctx._time_out()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert codes == [70] and finished["status"] == "error" and finished["message"] == worker_module.overrun_message("check")
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT status FROM crawl_runs WHERE id = %s", (run,)).fetchone()["status"] == "error"


def test_the_heartbeat_thread_times_a_job_out_only_past_its_limit(worker, cloud_database_url, cloud_org, cloud_brand,
                                                                  monkeypatch):
    codes: list[int] = []
    monkeypatch.setattr(worker_module.os, "_exit", lambda code: (codes.append(code), ctx.close()))  # a real exit ends the thread
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    job = _claimed_check(cloud_database_url, cloud_org, cloud_brand)
    ctx = worker_module.JobContext(cloud_database_url, job)
    time.sleep(0.3)
    assert codes == [] and _job(cloud_database_url, cloud_brand, job["id"])["status"] == "running"
    ctx._started -= ctx.max_seconds + 1  # the clock says the job has run past its limit
    ctx._beat.join(5)
    assert codes == [70] and _job(cloud_database_url, cloud_brand, job["id"])["status"] == "error"


def test_a_job_that_just_ended_or_was_claimed_again_is_not_failed_and_the_worker_stays(
        worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    codes = _exit_recorder(monkeypatch)
    job = _claimed_check(cloud_database_url, cloud_org, cloud_brand)
    ctx = worker_module.JobContext(cloud_database_url, job)
    ctx.close()
    with cloud_db.connect(cloud_database_url) as conn:  # the main thread has just finished it
        cloud_jobs.finish(conn, job["id"], status="done", message="ok", result={})
    ctx._time_out()
    assert codes == [] and _job(cloud_database_url, cloud_brand, job["id"])["status"] == "done"
    with cloud_db.connect(cloud_database_url) as conn:  # requeued and taken again by another worker: another run
        conn.execute("UPDATE jobs SET status = 'running', started_at = now() + interval '1 minute' WHERE id = %s", (job["id"],))
    ctx._time_out()
    assert codes == [] and _job(cloud_database_url, cloud_brand, job["id"])["status"] == "running"


def test_a_time_out_exits_even_when_the_database_does_not_answer(worker, cloud_database_url, cloud_org, cloud_brand,
                                                                 monkeypatch):
    exited, release = threading.Event(), threading.Event()
    monkeypatch.setattr(worker_module.os, "_exit", lambda code: exited.set())
    monkeypatch.setattr(worker_module, "TIME_OUT_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(cloud_jobs, "fail_running", lambda *_a, **_k: release.wait(5))
    job = _claimed_check(cloud_database_url, cloud_org, cloud_brand)
    ctx = worker_module.JobContext(cloud_database_url, job)
    ctx.close()
    thread = threading.Thread(target=ctx._time_out, daemon=True)
    thread.start()
    assert exited.wait(3)  # the timer left the process while the write was still blocked
    release.set()
    thread.join(5)  # it ends with its own exit: it must be over before `monkeypatch` gives the real `os._exit` back (AXE-611)
    assert not thread.is_alive()


def test_the_main_thread_stops_the_heartbeat_before_it_writes_the_outcome(worker, cloud_database_url, cloud_org,
                                                                          cloud_brand, monkeypatch):
    order: list[str] = []
    real_close = worker_module.JobContext.close
    monkeypatch.setattr(worker_module.JobContext, "close", lambda self: (order.append("close"), real_close(self)))
    real_finish = cloud_jobs.finish
    monkeypatch.setattr(cloud_jobs, "finish", lambda *a, **k: (order.append("finish"), real_finish(*a, **k)))
    monkeypatch.setattr(worker_module, "run_matching", lambda *a, **k: None)
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    worker.run_once()
    assert order.index("close") < order.index("finish")


def test_the_watchdog_exits_when_the_alive_file_is_stale_and_ignores_a_missing_one(tmp_path, monkeypatch):
    alive = tmp_path / "alive"
    monkeypatch.setattr(worker_module, "ALIVE_FILE", alive)
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.01)
    codes = _exit_recorder(monkeypatch)
    watcher = worker_module.Worker(database_url="", storage_client_factory=lambda: None)
    # missing file: the loop keeps going (stop it after a few turns), nothing exits
    threading.Timer(0.1, watcher.stop).start()
    watcher._watchdog()
    assert codes == []
    alive.touch()
    os.utime(alive, (time.time() - 2 * worker_module.WATCHDOG_SECONDS,) * 2)
    watcher._stop.clear()
    with pytest.raises(_Exit):
        watcher._watchdog()
    assert codes == [70]
    alive.touch()  # fresh: the loop does not exit
    threading.Timer(0.1, watcher.stop).start()
    watcher._stop.clear()
    watcher._watchdog()
    assert codes == [70]


def test_chromium_leftovers_are_removed_once_quiet_and_a_recent_deep_write_spares_the_folder(tmp_path):
    old, busy, fresh, other = (tmp_path / "playwright_chromiumdev_profile-old", tmp_path / "playwright_chromiumdev_profile-busy",
                               tmp_path / "playwright-artifacts-new", tmp_path / "keep-me")
    for folder in (old, busy, fresh, other):
        (folder / "Default" / "Cache").mkdir(parents=True)
        (folder / "Default" / "Cache" / "data").write_bytes(b"x")
    long_ago = time.time() - 2 * worker_module.TEMP_LEFTOVER_AGE_SECONDS
    for folder in (old, busy, other):  # every directory and file is old, except busy's deepest file
        for path in (folder, *folder.rglob("*")):
            os.utime(path, (long_ago, long_ago))
    (busy / "Default" / "Cache" / "data").write_bytes(b"y")  # a browser is writing deep inside an old-looking folder
    assert worker_module.clean_temp(time.time(), tmp_path) == 1
    assert not old.exists() and busy.exists() and fresh.exists() and other.exists()


def test_running_out_of_memory_or_disk_says_so_and_what_to_do(worker, cloud_database_url, cloud_org, cloud_brand,
                                                              monkeypatch):
    assert "mémoire" in worker_module.resource_problem(MemoryError())
    assert "disque" in worker_module.resource_problem(OSError(errno.ENOSPC, "No space left on device"))
    assert worker_module.resource_problem(RuntimeError("x")) is None

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(worker_module, "run_matching", full)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    assert worker.run_once()
    assert "disque" in _job(cloud_database_url, cloud_brand, job["id"])["message"]


def test_a_retry_is_lighter_and_the_first_attempt_is_untouched():
    """AXE-645: one page at a time and embedding batches halved at each retry (16, 8, 4)."""
    config = Config()
    assert worker_module.lighter(config, 0) is config
    first, second = worker_module.lighter(config, 1), worker_module.lighter(config, 2)
    assert (first.crawl.concurrency, first.match.embedding_batch_size) == (1, config.match.embedding_batch_size // 2)
    assert second.match.embedding_batch_size == config.match.embedding_batch_size // 4
    assert worker_module.lighter(replace(config, match=replace(config.match, embedding_batch_size=1)), 3).match.embedding_batch_size == 1


def test_a_worker_that_is_stopping_puts_its_job_back_without_using_a_retry(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    """AXE-642: SIGTERM stops the job where it is (every step reads `ctx.cancelled`) and requeues it at once, `retries` unchanged."""
    def stopped_while_running(self, ctx, org_id, brand_id, params):
        self.stop()  # what the SIGTERM handler does
        assert ctx.cancelled and ctx.should_stop()
        return "Comparaison terminée.", {}

    monkeypatch.setattr(worker_module.Worker, "_match_job", stopped_while_running)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match", params={"retries": 1})
    assert worker.run_once()
    again = _job(cloud_database_url, cloud_brand, job["id"])
    assert again["status"] == "queued" and again["params"] == {"retries": 1} and again["message"] == worker_module.RESTART_MESSAGE
    assert again["started_at"] is None and again["finished_at"] is None


def test_a_job_a_person_stopped_ends_even_when_the_worker_is_stopping(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    def stopped_by_both(self, ctx, org_id, brand_id, params):
        with cloud_db.connect(self.database_url) as conn:
            cloud_jobs.request_cancel(conn, brand_id, uuid.UUID(ctx.job["id"]))
        ctx._heartbeat()  # the next heartbeat tells the job a person asked to stop it
        self.stop()
        assert ctx.cancelled
        return "x", {}

    monkeypatch.setattr(worker_module.Worker, "_match_job", stopped_by_both)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    assert worker.run_once()
    assert _job(cloud_database_url, cloud_brand, job["id"])["status"] == "cancelled"


def test_a_read_started_again_is_lighter_skips_its_pages_and_says_so(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    """AXE-645: `retries` lightens the settings, `skip_pages` and the pages the job's own earlier run read are not read again."""
    seen = {}

    def fake_crawl(url, store, config, **kwargs):
        seen.update(kwargs, concurrency=config.crawl.concurrency)
        return worker_module.CrawlStats()

    monkeypatch.setattr(worker_module, "crawl_site", fake_crawl)
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.create_site(conn, org_id=cloud_org, brand_id=cloud_brand, url="https://skip.example")
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="crawl", params={
            "site_ids": [str(site)], "fresh": True, "then_match": False, "retries": 1, "skip_pages": ["https://skip.example/heavy"]})
        cloud_db.start_crawl_run(conn, org_id=cloud_org, site_id=site, triggered_by=None, job_id=uuid.UUID(job["id"]))
        conn.execute("INSERT INTO pages (org_id, site_id, url, status, read_started_at) VALUES (%s, %s, %s, 'done', now())",
                     (cloud_org, site, "https://skip.example/already"))
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done" and "1 page(s) ignorée(s)" in finished["message"]
    assert seen["skip"] == {"https://skip.example/heavy", "https://skip.example/already"} and seen["resume"] is False
    assert seen["concurrency"] == 1


def test_a_job_that_ended_before_it_saw_the_stop_is_done_not_started_again(worker, cloud_database_url, cloud_org, cloud_brand, monkeypatch):
    """A report fully built when SIGTERM arrives is kept: only a job that looked at the stop and left early goes back in the queue."""
    def finished_then_stopped(self, ctx, org_id, brand_id, params):
        self.stop()  # the handler never looks at `ctx.cancelled`, like `_report`
        return "Rapport prêt.", {}

    monkeypatch.setattr(worker_module.Worker, "_match_job", finished_then_stopped)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="match")
    assert worker.run_once()
    done = _job(cloud_database_url, cloud_brand, job["id"])
    assert done["status"] == "done" and done["message"] == "Rapport prêt."
