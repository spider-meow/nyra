"""End-to-end pipeline test: ingest refs -> simulate a crawl finding -> match -> report.

No network and no Playwright browser: the "site image" is written straight
into the DB the same way fetch.fetch_and_store would, so this exercises
db.py, refs.py, match.py, and report.py together without needing a live
site or a CLIP model download.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from rightswatch import db, refs, report
from rightswatch.config import load_config
from rightswatch.match import compute_hashes, run_matching


def make_image(path: Path, seed: int) -> None:
    img = Image.new("RGB", (300, 300))
    draw = ImageDraw.Draw(img)
    for y in range(300):
        draw.line([(0, y), (300, y)], fill=((y + seed) % 256, (200 - y) % 256, (seed * 7) % 256))
    draw.ellipse([50, 50, 250, 250], fill=(seed % 256, 20, 220))
    img.save(path)


def make_unrelated_image(path: Path) -> None:
    img = Image.new("RGB", (300, 300), color=(5, 5, 5))
    draw = ImageDraw.Draw(img)
    for i in range(0, 300, 12):
        draw.line([(i, 0), (0, i)], fill=(255, 240, 0), width=4)
    draw.polygon([(150, 20), (20, 280), (280, 280)], fill=(0, 90, 255))
    img.save(path)


def test_pipeline_end_to_end(tmp_path: Path):
    config = load_config()

    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    expiring_soon = date.today() + timedelta(days=10)
    far_future = date.today() + timedelta(days=400)

    make_image(refs_dir / "bottle.jpg", seed=1)
    make_unrelated_image(refs_dir / "unrelated.jpg")

    csv_path = tmp_path / "refs.csv"
    csv_path.write_text(
        "filename,expiry_date,credit,notes\n"
        f"bottle.jpg,{expiring_soon.isoformat()},Photographer A,hero shot\n"
        f"unrelated.jpg,{far_future.isoformat()},Photographer B,not on site\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "rw.db"
    source = refs.CsvRefSource(images_dir=refs_dir, csv_path=csv_path)
    count = refs.ingest(source, db_path, config, compute_embeddings=False)
    assert count == 2

    # Simulate the crawler finding "bottle.jpg" re-served (recompressed) on the site.
    site_img_path = tmp_path / "site_bottle.jpg"
    make_image(site_img_path, seed=1)
    with Image.open(site_img_path) as img:
        phash, dhash = compute_hashes(img)

    with db.connect(db_path) as conn:
        page_id = db.upsert_page(conn, "https://example.com/products/bottle", status="pending")
        db.mark_page_crawled(conn, "https://example.com/products/bottle", http_status=200)
        image_id = db.upsert_site_image(
            conn,
            url="https://example.com/media/bottle-hero.jpg",
            local_path=str(site_img_path),
            phash=phash,
            dhash=dhash,
        )
        db.link_image_page(conn, image_id, page_id)

    match_count = run_matching(db_path, config, use_clip=False)
    assert match_count == 1

    with db.connect(db_path) as conn:
        matches = db.get_matches(conn)
    assert len(matches) == 1
    assert matches[0]["filename"] == "bottle.jpg"
    assert matches[0]["site_url"] == "https://example.com/media/bottle-hero.jpg"

    out_dir = tmp_path / "out"
    html_path, csv_out_path = report.generate_report(db_path, out_dir, config, within_days=90)
    assert html_path.exists()
    assert csv_out_path.exists()

    html_content = html_path.read_text(encoding="utf-8")
    assert "bottle.jpg" in html_content
    assert "https://example.com/products/bottle" in html_content

    csv_content = csv_out_path.read_text(encoding="utf-8")
    assert "bottle.jpg" in csv_content
    # unrelated.jpg has no match and expires far in the future -> excluded either way
    assert "unrelated.jpg" not in csv_content
