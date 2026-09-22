"""Local interface: library upload, metadata, and ingest without a browser or a crawl."""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from rightswatch import db
from rightswatch.api import create_app


def _png(path: Path) -> None:
    img = Image.new("RGB", (240, 180), color=(255, 215, 49))
    draw = ImageDraw.Draw(img)
    draw.ellipse([30, 20, 210, 160], fill=(77, 162, 255))
    img.save(path)


def test_library_upload_ingest_and_page(tmp_path: Path):
    app = create_app(root=tmp_path, db_path=tmp_path / "rightswatch.db")
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "RightsWatch" in page.text
    assert "login" not in page.text.lower()

    src = tmp_path / "ref.png"
    _png(src)
    with src.open("rb") as handle:
        uploaded = client.post(
            "/api/library/upload",
            files=[("files", ("campagne.png", handle, "image/png"))],
        )
    assert uploaded.status_code == 200
    assert uploaded.json()["saved"] == ["campagne.png"]

    updated = client.put(
        "/api/library/campagne.png",
        json={"expiry_date": "2026-04-01", "credit": "Studio Nord", "notes": "Packshot"},
    )
    assert updated.status_code == 200

    started = client.post("/api/jobs/ingest", json={"fast": True})
    assert started.status_code == 200

    job = None
    for _ in range(50):
        job = client.get("/api/job").json()["job"]
        if job and job["status"] != "running":
            break
        time.sleep(0.1)

    assert job is not None
    assert job["status"] == "done", job
    assert job["result"]["ingested"] == 1

    overview = client.get("/api/overview").json()
    assert overview["stats"]["reference_images"] == 1
    item = overview["library"][0]
    assert item["filename"] == "campagne.png"
    assert item["indexed"] is True
    assert item["expiry_date"] == "2026-04-01"
    assert item["credit"] == "Studio Nord"

    media = client.get("/api/media/ref/campagne.png")
    assert media.status_code == 200
    assert media.content.startswith(b"\x89PNG")

    thumb = client.get("/api/media/ref/campagne.png?w=64")
    assert thumb.status_code == 200
    assert thumb.content[:2] == b"\xff\xd8"
    assert len(thumb.content) < len(media.content)

    removed = client.delete("/api/library/campagne.png")
    assert removed.status_code == 200
    after = client.get("/api/overview").json()
    assert after["library"] == []
    assert after["stats"]["reference_images"] == 0


def test_matches_are_grouped_and_a_review_is_kept(tmp_path: Path):
    app = create_app(root=tmp_path, db_path=tmp_path / "rightswatch.db")
    client = TestClient(app)
    far_expiry = (date.today() + timedelta(days=180)).isoformat()
    with db.connect(tmp_path / "rightswatch.db") as conn:
        ref_id = db.upsert_reference_image(
            conn,
            filename="campagne.png",
            path=str(tmp_path / "missing.png"),
            expiry_date=far_expiry,
            credit="",
            notes="",
            phash="ffff0000ffff0000",
            dhash="ffff0000ffff0000",
        )
        site_id = db.upsert_site_image(
            conn,
            url="https://example.test/a.webp",
            local_path="",
            content_hash="abc",
            phash="ffff0000ffff0000",
            dhash="ffff0000ffff0000",
        )
        db.upsert_match(conn, reference_id=ref_id, site_image_id=site_id, level="phash", score=1.0, confidence="haut")

    listed = client.get("/api/matches?within_days=90")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["later"]
    assert payload["later"][0]["hits"][0]["page_count"] == 0

    saved = client.post(
        "/api/reviews",
        json={"reference_id": ref_id, "site_image_ids": [site_id], "decision": "ecarte"},
    )
    assert saved.status_code == 200
    again = client.get("/api/matches?within_days=400").json()
    assert again["confirmed"][0]["hits"][0]["decision"] == "ecarte"


def test_unmatched_reference_is_flagged_not_found(tmp_path: Path):
    app = create_app(root=tmp_path, db_path=tmp_path / "rightswatch.db")
    client = TestClient(app)
    soon = (date.today() + timedelta(days=10)).isoformat()
    with db.connect(tmp_path / "rightswatch.db") as conn:
        db.upsert_reference_image(
            conn,
            filename="jamais-vue.png",
            path=str(tmp_path / "missing.png"),
            expiry_date=soon,
            credit="",
            notes="",
            phash="0000ffff0000ffff",
            dhash="0000ffff0000ffff",
        )
        # No site_images, no matches: this reference has never been compared.

    listed = client.get("/api/matches?within_days=90").json()
    assert len(listed["not_found"]) == 1
    assert listed["not_found"][0]["filename"] == "jamais-vue.png"
    assert listed["not_found"][0]["compared"] is False

    with db.connect(tmp_path / "rightswatch.db") as conn:
        # Simulate a match pass that ran and found nothing: compared_at gets stamped.
        db.stamp_compared(conn, [1], [], "sig")

    checked = client.get("/api/matches?within_days=90").json()
    assert checked["not_found"][0]["compared"] is True
