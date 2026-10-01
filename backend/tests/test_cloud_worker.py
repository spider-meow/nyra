"""backend/nyra/cloud/worker.py: jobs claimed from Postgres and run end to end,
against a real Postgres (TEST_DATABASE_URL) and the in-memory fake Storage.

CLIP is replaced by a deterministic fake embedding so no model is
downloaded; a real crawl needs a browser and a live site and isn't run
here (the crawler's parsing is covered in test_crawl.py).
"""

from __future__ import annotations

import io
import uuid
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("psycopg")

import psycopg

from nyra.cloud import db as cloud_db
from nyra.cloud import jobs as cloud_jobs
from nyra.cloud import worker as worker_module
from nyra.match import compute_hashes


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
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, brand_id=cloud_brand, kind="report", params={"within_days": 90})

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_brand, job["id"])
    assert finished["status"] == "done", finished
    with cloud_db.connect(cloud_database_url) as conn:
        report = cloud_db.get_report(conn, cloud_brand, uuid.UUID(finished["result"]["report_id"]))
    html = fake_storage_client.store[("reports", report["storage_path_html"])].decode("utf-8")
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
