"""backend/nyra/cloud/pipeline.py: ingest, run_matching, generate_report
against a real Postgres (via TEST_DATABASE_URL) and a fake in-memory
Supabase Storage (via the `fake_storage_client` fixture) — no live
Supabase project needed, no network. `crawl_site` isn't covered here for
the same reason the local `nyra.crawl.crawl_site` isn't unit-tested
either: it needs a real browser and a live site (see DEVELOPMENT.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("psycopg")

from nyra.cloud import db as cloud_db
from nyra.cloud import pipeline
from nyra.config import load_config
from nyra.match import compute_hashes
from nyra.refs import CsvRefSource


def _make_image(path: Path, seed: int) -> None:
    img = Image.new("RGB", (300, 300))
    draw = ImageDraw.Draw(img)
    for y in range(300):
        draw.line([(0, y), (300, y)], fill=((y + seed) % 256, (150 - y) % 256, (seed * 3) % 256))
    draw.ellipse([50, 50, 250, 250], fill=(seed % 256, 20, 220))
    img.save(path)


@pytest.fixture()
def config():
    return load_config()


def test_ingest_hashes_uploads_and_persists(cloud_database_url, cloud_org, fake_storage_client, config, tmp_path):
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    _make_image(refs_dir / "a.jpg", seed=1)
    (refs_dir / "refs.csv").write_text(
        "filename,expiry_date,credit,notes\na.jpg,2026-11-01,Credit,Notes\n", encoding="utf-8"
    )
    source = CsvRefSource(images_dir=refs_dir, csv_path=refs_dir / "refs.csv")

    count = pipeline.ingest(source, cloud_org, cloud_database_url, fake_storage_client, config, compute_embeddings=False)
    assert count == 1
    assert ("refs", f"{cloud_org}/a.jpg") in fake_storage_client.store

    with cloud_db.connect(cloud_database_url) as conn:
        refs = cloud_db.get_reference_images(conn, cloud_org)
    assert len(refs) == 1
    assert refs[0]["filename"] == "a.jpg"
    assert refs[0]["credit"] == "Credit"
    assert refs[0]["phash"] is not None


def test_run_matching_end_to_end(cloud_database_url, cloud_org, config):
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        ref_id = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="x",
            expiry_date=None, credit=None, notes=None, phash=same_hash, dhash=same_hash,
        )
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        site_img_id, _ = cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/a.jpg",
            phash=same_hash, dhash=same_hash,
        )

    count = pipeline.run_matching(cloud_org, cloud_database_url, config, use_clip=False)
    assert count == 1

    # incremental: a second site image matching the same reference
    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/b.jpg",
            phash=same_hash, dhash=same_hash,
        )
    count2 = pipeline.run_matching(cloud_org, cloud_database_url, config, use_clip=False)
    assert count2 == 2

    with cloud_db.connect(cloud_database_url) as conn:
        matches = cloud_db.get_matches(conn, cloud_org)
        unmatched = cloud_db.get_unmatched_references(conn, cloud_org)
    assert {m["site_url"] for m in matches} == {"https://example.com/a.jpg", "https://example.com/b.jpg"}
    assert unmatched == []


def test_run_matching_threshold_change_forces_full_recompute(cloud_database_url, cloud_org, config):
    from dataclasses import replace

    ref_hash = "ffff0000ffff0000"
    site_hash = "ffff0000ffff000f"  # Hamming distance 4

    with cloud_db.connect(cloud_database_url) as conn:
        cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="a.jpg", storage_path="x",
            expiry_date=None, credit=None, notes=None, phash=ref_hash, dhash=ref_hash,
        )
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/a.jpg",
            phash=site_hash, dhash=site_hash,
        )

    loose = replace(config, match=replace(config.match, phash_threshold=8, dhash_threshold=8))
    assert pipeline.run_matching(cloud_org, cloud_database_url, loose, use_clip=False) == 1

    strict = replace(config, match=replace(config.match, phash_threshold=0, dhash_threshold=0))
    assert pipeline.run_matching(cloud_org, cloud_database_url, strict, use_clip=False) == 0


def test_generate_report_flags_unmatched_and_uploads_files(cloud_database_url, cloud_org, fake_storage_client, config):
    same_hash = "ffff0000ffff0000"
    with cloud_db.connect(cloud_database_url) as conn:
        found_ref = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="found.jpg", storage_path="refs/found.jpg",
            expiry_date="2026-10-01", credit=None, notes=None, phash=same_hash, dhash=same_hash,
        )
        missing_ref = cloud_db.upsert_reference_image(
            conn, org_id=cloud_org, filename="missing.jpg", storage_path="refs/missing.jpg",
            expiry_date="2026-10-05", credit=None, notes=None, phash="0000ffff0000ffff", dhash="0000ffff0000ffff",
        )
        site_id = cloud_db.upsert_site(conn, org_id=cloud_org, url="https://example.com")
        cloud_db.upsert_site_image(
            conn, org_id=cloud_org, site_id=site_id, url="https://example.com/found.jpg",
            phash=same_hash, dhash=same_hash,
        )

    pipeline.run_matching(cloud_org, cloud_database_url, config, use_clip=False)

    report_id = pipeline.generate_report(cloud_org, cloud_database_url, fake_storage_client, config, within_days=90)

    with cloud_db.connect(cloud_database_url) as conn:
        report = cloud_db.get_report(conn, cloud_org, report_id)

    html = fake_storage_client.store[("reports", report["storage_path_html"])].decode("utf-8")
    csv_content = fake_storage_client.store[("reports", report["storage_path_csv"])].decode("utf-8")
    not_found_csv = fake_storage_client.store[("reports", report["storage_path_not_found_csv"])].decode("utf-8")

    assert "found.jpg" in html and "missing.jpg" in html  # both shown, one flagged not-found
    assert "found.jpg" in csv_content and "missing.jpg" not in csv_content
    assert "missing.jpg" in not_found_csv and "found.jpg" not in not_found_csv
