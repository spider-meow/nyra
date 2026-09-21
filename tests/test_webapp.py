"""Local interface: library upload, metadata, and ingest without a browser or a crawl."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from rightswatch.webapp import create_app


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
    assert "Bibliothèque" in page.text
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

    removed = client.delete("/api/library/campagne.png")
    assert removed.status_code == 200
    after = client.get("/api/overview").json()
    assert after["library"] == []
    assert after["stats"]["reference_images"] == 0
