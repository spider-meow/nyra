"""backend/nyra/cloud/worker.py: jobs claimed from Postgres and run end to end,
against a real Postgres (TEST_DATABASE_URL) and the in-memory fake Storage.

CLIP is replaced by a deterministic fake embedding so no model is
downloaded; a real crawl needs a browser and a live site and isn't run
here (the crawler's parsing is covered in test_crawl.py).
"""

from __future__ import annotations

import io
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("psycopg")

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
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled' WHERE status IN ('queued', 'running')")
    return worker_module.Worker(database_url=cloud_database_url, storage_client_factory=lambda: fake_storage_client)


def _job(cloud_database_url, org_id, job_id):
    with cloud_db.connect(cloud_database_url) as conn:
        return cloud_jobs.get(conn, org_id, uuid.UUID(job_id))


def _add_reference(conn, storage, org_id, name, data, expiry="2020-01-01"):
    img = Image.open(io.BytesIO(data))
    phash, dhash = compute_hashes(img)
    path = f"{org_id}/{name}"
    storage.store[("refs", path)] = data
    return cloud_db.upsert_reference_image(conn, org_id=org_id, filename=name, storage_path=path, expiry_date=expiry,
                                           credit=None, notes=None, phash=phash, dhash=dhash)


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


def test_index_job_backfills_references_and_site_images_then_compares(worker, cloud_database_url, cloud_org, fake_storage_client):
    data = _image_bytes(0)
    with cloud_db.connect(cloud_database_url) as conn:
        _add_reference(conn, fake_storage_client, cloud_org, "hero.jpg", data)
        site = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://t.test/")
        _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/hero.jpg", data)
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="index")

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_org, job["id"])
    assert finished["status"] == "done", finished
    assert finished["result"]["indexed"] == 2
    assert finished["result"]["matches"] == 1

    with cloud_db.connect(cloud_database_url) as conn:
        ref = cloud_db.get_reference_by_filename(conn, cloud_org, "hero.jpg")
        site_row = conn.execute("SELECT * FROM site_images WHERE org_id = %s", (cloud_org,)).fetchone()
    assert ref["embedding"] is not None and ref["phash_flip"] and ref["thumb_path"]
    assert ref["compared_at"] is not None
    assert site_row["embedding"] is not None and site_row["thumb_path"]
    assert ("refs", ref["thumb_path"]) in fake_storage_client.store


def test_report_job_stores_files_without_false_positives(worker, cloud_database_url, cloud_org, fake_storage_client):
    with cloud_db.connect(cloud_database_url) as conn:
        site = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://t.test/")
        kept = _add_reference(conn, fake_storage_client, cloud_org, "kept.jpg", _image_bytes(1))
        dismissed = _add_reference(conn, fake_storage_client, cloud_org, "dismissed.jpg", _image_bytes(40))
        img_a = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/a.jpg", _image_bytes(1))
        img_b = _add_site_image(conn, fake_storage_client, cloud_org, site, "https://t.test/b.jpg", _image_bytes(40))
        cloud_db.write_matches(conn, cloud_org, [(kept, img_a, "phash", 1.0, "haut"), (dismissed, img_b, "phash", 1.0, "haut")])
        cloud_db.set_reviews(conn, org_id=cloud_org, reference_id=dismissed, site_image_ids=[img_b],
                             decision="ecarte", reviewed_by=None)
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="report", params={"within_days": 90})

    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_org, job["id"])
    assert finished["status"] == "done", finished
    with cloud_db.connect(cloud_database_url) as conn:
        report = cloud_db.get_report(conn, cloud_org, uuid.UUID(finished["result"]["report_id"]))
    html = fake_storage_client.store[("reports", report["storage_path_html"])].decode("utf-8")
    assert "kept.jpg" in html and "dismissed.jpg" not in html
    assert "data:image/jpeg;base64," in html  # thumbnails inlined, the file stands alone
    assert report["stats"]["expired_online"] == 1


def test_crawl_of_a_private_address_fails_cleanly(worker, cloud_database_url, cloud_org, monkeypatch):
    monkeypatch.delenv("NYRA_ALLOW_PRIVATE_HOSTS", raising=False)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="crawl", params={"site": "http://127.0.0.1:8000/"})
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_org, job["id"])
    assert finished["status"] == "error"
    assert finished["message"].startswith("Adresse refusée")
    with cloud_db.connect(cloud_database_url) as conn:
        run = cloud_db.list_crawl_runs(conn, cloud_org)[0]
    assert run["status"] == "error"


def test_unexpected_failure_is_recorded_and_the_worker_survives(worker, cloud_database_url, cloud_org, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(worker_module, "run_matching", boom)
    with cloud_db.connect(cloud_database_url) as conn:
        job = cloud_jobs.enqueue(conn, org_id=cloud_org, kind="match")
    assert worker.run_once()
    finished = _job(cloud_database_url, cloud_org, job["id"])
    assert finished["status"] == "error"
    assert finished["message"] == "La tâche a échoué."
    assert "disk on fire" in finished["error"]
    assert worker.run_once() is False
