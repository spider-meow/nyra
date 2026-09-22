"""Org-aware ingest/crawl/match/report — the cloud equivalents of
`nyra.refs.ingest`, `nyra.crawl.crawl_site`,
`nyra.match.run_matching`, and `nyra.report.generate_report`.

Same algorithms, different persistence: these write to `cloud.db`
(Postgres, org-scoped) and `cloud.storage` (Supabase Storage) instead of
SQLite and local disk. Every pure function the local pipeline uses —
hashing, HTML/sitemap parsing, the Hamming/cosine matching kernel, report
row shaping — is imported and reused unchanged; only the database- and
disk-coupled integration functions (`refs.ingest`, `fetch.fetch_and_store`,
`crawl.crawl_site`, `match.run_matching`, `report.generate_report`
themselves) are reimplemented here against the new persistence layer.
See `docs/DEVELOPMENT.md`'s "pure functions vs. integration layer" note —
this module is a second integration layer over the same pure core.
"""

from __future__ import annotations

import io
import json
import random
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, UnidentifiedImageError
from supabase import Client

from nyra import fetch as fetch_module
from nyra import report as report_module
from nyra.config import Config
from nyra.crawl import (
    BACKGROUND_IMAGE_JS,
    autoscroll,
    click_load_more,
    extract_images_from_html,
    extract_internal_links,
    fetch_sitemap_urls,
    is_allowed,
    load_robots,
    normalize_url,
    same_site,
)
from nyra.crawl import CrawlStats
from nyra.match import (
    CONFIDENCE_TO_VERIFY,
    LEVEL_CLIP,
    LEVEL_DHASH,
    LEVEL_PHASH,
    MatchStopped,
    _embedding_matrix,  # noqa: PLC2701 - pure numeric helper, no SQLite coupling
    _u64,  # noqa: PLC2701
    compute_clip_embedding,
    compute_hashes,
    match_index_pairs,
)
from nyra.refs import RefSource

from . import db as cloud_db
from . import storage as cloud_storage


# --- ingest -----------------------------------------------------------

def ingest(
    source: RefSource,
    org_id: uuid.UUID,
    database_url: str,
    storage_client: Client,
    config: Config,
    *,
    compute_embeddings: bool = True,
    progress=None,
) -> int:
    entries = list(source.iter_refs())

    with cloud_db.connect(database_url) as conn:
        for i, entry in enumerate(entries):
            with Image.open(entry.image_path) as img:
                img.load()
                width, height = img.size
                phash, dhash = compute_hashes(img)
                embedding = compute_clip_embedding(img, config.match) if compute_embeddings else None

            storage_path = cloud_storage.path_for(org_id, entry.filename)
            cloud_storage.upload(
                storage_client, cloud_storage.BUCKET_REFS, storage_path, entry.image_path.read_bytes()
            )

            cloud_db.upsert_reference_image(
                conn,
                org_id=org_id,
                filename=entry.filename,
                storage_path=storage_path,
                expiry_date=entry.expiry_date,
                credit=entry.credit,
                notes=entry.notes,
                phash=phash,
                dhash=dhash,
                embedding=embedding,
                width=width,
                height=height,
            )
            if progress:
                progress(i + 1, len(entries))

    return len(entries)


# --- crawl --------------------------------------------------------------

def _fetch_and_store_cloud(
    conn,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    url: str,
    page_id: uuid.UUID,
    client: httpx.Client,
    storage_client: Client,
    config: Config,
    compute_embeddings: bool,
) -> tuple[Optional[uuid.UUID], bool]:
    """Cloud counterpart of `nyra.fetch.fetch_and_store`: same
    download/filter/hash logic (reused from `fetch_module`), Supabase
    Storage instead of a local cache directory."""
    existing = conn.execute(
        "SELECT id, phash FROM site_images WHERE site_id = %s AND url = %s", (site_id, url)
    ).fetchone()
    if existing is not None and existing["phash"] is not None:
        cloud_db.link_image_page(conn, existing["id"], page_id)
        return existing["id"], False

    fetched = fetch_module.download(url, client, timeout=config.crawl.request_timeout_seconds)
    if fetched is None:
        return None, False
    data, content_type = fetched

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        return None, False

    if not fetch_module.meets_min_size(img, config.crawl.min_image_side_px):
        return None, False

    digest = fetch_module.content_hash(data)
    ext = Path(fetch_module.cache_path_for(Path("."), digest, content_type)).suffix
    storage_path = cloud_storage.path_for(org_id, f"{digest}{ext}")
    if not cloud_storage.exists(storage_client, cloud_storage.BUCKET_SITE_IMAGES, storage_path):
        cloud_storage.upload(
            storage_client, cloud_storage.BUCKET_SITE_IMAGES, storage_path, data, content_type=content_type
        )

    rgb = img.convert("RGB")
    phash, dhash = compute_hashes(rgb)
    embedding = compute_clip_embedding(rgb, config.match) if compute_embeddings else None

    image_id, is_new = cloud_db.upsert_site_image(
        conn,
        org_id=org_id,
        site_id=site_id,
        url=url,
        storage_path=storage_path,
        content_hash=digest,
        width=img.size[0],
        height=img.size[1],
        phash=phash,
        dhash=dhash,
        embedding=embedding,
    )
    cloud_db.link_image_page(conn, image_id, page_id)
    return image_id, is_new


def crawl_site(
    site_url: str,
    org_id: uuid.UUID,
    database_url: str,
    storage_client: Client,
    config: Config,
    *,
    max_pages: Optional[int] = None,
    compute_embeddings: bool = True,
    resume: bool = True,
    progress=None,
    should_stop=None,
) -> tuple[uuid.UUID, CrawlStats]:
    """Same BFS/sitemap/robots.txt logic as `nyra.crawl.crawl_site`
    (all imported, unchanged) — persists to Postgres + Storage instead of
    SQLite + local disk, and records progress in `crawl_runs` so it
    survives a server restart, not just the in-memory job state the local
    product uses."""
    from playwright.sync_api import sync_playwright

    max_pages = max_pages or config.crawl.max_pages
    site_netloc = urlparse(site_url).netloc
    stats = CrawlStats()

    headers = {"User-Agent": config.crawl.user_agent}
    with httpx.Client(headers=headers) as client:
        robots = load_robots(site_url, client) if config.crawl.respect_robots_txt else None
        sitemap_urls = fetch_sitemap_urls(site_url, client)

    with cloud_db.connect(database_url) as conn:
        site_id = cloud_db.upsert_site(conn, org_id=org_id, url=site_url)
        run_id = cloud_db.start_crawl_run(conn, org_id=org_id, site_id=site_id, triggered_by=None)
        already_done = cloud_db.get_crawled_urls(conn, site_id) if resume else set()

    seed_urls = [normalize_url(u) for u in sitemap_urls if same_site(u, site_netloc)]
    if not seed_urls:
        seed_urls = [normalize_url(site_url)]

    queue: list[str] = [u for u in seed_urls if u not in already_done]
    visited: set[str] = set(already_done)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=config.crawl.user_agent)

        with httpx.Client(headers=headers) as image_client:
            while queue and stats.pages_visited < max_pages:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)

                if robots is not None and not is_allowed(robots, url, config.crawl.user_agent):
                    stats.blocked_by_robots += 1
                    continue

                with cloud_db.connect(database_url) as conn:
                    page_id = cloud_db.upsert_page(conn, org_id=org_id, site_id=site_id, url=url)

                page = context.new_page()
                try:
                    response = page.goto(url, timeout=config.crawl.page_load_timeout_ms, wait_until="load")
                    autoscroll(page)
                    click_load_more(page)
                    autoscroll(page)
                    html = page.content()
                    try:
                        background_urls = page.evaluate(BACKGROUND_IMAGE_JS)
                    except Exception:
                        background_urls = []
                    http_status = response.status if response else 0
                except Exception as exc:  # noqa: BLE001 - one bad page must not kill the crawl
                    stats.errors.append(f"{url}: {exc}")
                    page.close()
                    with cloud_db.connect(database_url) as conn:
                        cloud_db.mark_page_crawled(conn, page_id, http_status=0)
                    continue

                image_urls = extract_images_from_html(html, url)
                image_urls |= {urljoin(url, u) for u in background_urls}
                internal_links = extract_internal_links(html, url, site_netloc)

                with cloud_db.connect(database_url) as conn:
                    for image_url in image_urls:
                        stats.images_found += 1
                        image_id, is_new = _fetch_and_store_cloud(
                            conn,
                            org_id=org_id,
                            site_id=site_id,
                            url=image_url,
                            page_id=page_id,
                            client=image_client,
                            storage_client=storage_client,
                            config=config,
                            compute_embeddings=compute_embeddings,
                        )
                        if image_id is not None:
                            stats.images_stored += 1
                            if is_new:
                                stats.images_new += 1

                    cloud_db.mark_page_crawled(conn, page_id, http_status=http_status)
                    cloud_db.update_crawl_run_progress(
                        conn,
                        run_id,
                        pages_visited=stats.pages_visited + 1,
                        images_found=stats.images_found,
                        images_stored=stats.images_stored,
                        images_new=stats.images_new,
                        blocked_by_robots=stats.blocked_by_robots,
                    )

                for link in internal_links:
                    if link not in visited and link not in queue:
                        queue.append(link)

                page.close()
                stats.pages_visited += 1

                if progress:
                    progress(stats)
                if should_stop and should_stop():
                    break

                delay = random.uniform(config.crawl.delay_seconds_min, config.crawl.delay_seconds_max)
                time.sleep(delay)

        browser.close()

    with cloud_db.connect(database_url) as conn:
        cloud_db.finish_crawl_run(
            conn, run_id, status="cancelled" if (should_stop and should_stop()) else "done", errors=stats.errors
        )

    return run_id, stats


# --- match --------------------------------------------------------------

def _signature(config, use_clip: bool) -> str:
    return "|".join(
        [
            str(config.match.phash_threshold),
            str(config.match.dhash_threshold),
            str(config.match.clip_similarity_high),
            str(config.match.clip_similarity_medium),
            str(config.match.clip_similarity_floor),
            "clip" if use_clip else "hash",
        ]
    )


def _pack_rows_cloud(rows: list[dict], use_clip: bool):
    import numpy as np

    ids = [row["id"] for row in rows]
    phash = np.zeros(len(rows), dtype=np.uint64)
    dhash = np.zeros(len(rows), dtype=np.uint64)
    p_ok = np.zeros(len(rows), dtype=bool)
    d_ok = np.zeros(len(rows), dtype=bool)
    vectors: list[Optional[np.ndarray]] = []
    for index, row in enumerate(rows):
        p_value, p_known = _u64(row["phash"])
        d_value, d_known = _u64(row["dhash"])
        phash[index] = p_value
        dhash[index] = d_value
        p_ok[index] = p_known
        d_ok[index] = d_known
        if use_clip:
            raw = row.get("embedding")
            vectors.append(None if raw is None else raw.to_numpy().astype(np.float32))
        else:
            vectors.append(None)
    matrix, mask = _embedding_matrix(vectors) if use_clip else (None, np.zeros(len(rows), dtype=bool))
    return ids, phash, dhash, p_ok, d_ok, matrix, mask


def run_matching(
    org_id: uuid.UUID, database_url: str, config: Config, use_clip: bool = True, progress=None, should_stop=None
) -> int:
    """Same incremental-cache algorithm as `nyra.match.run_matching`
    (full recompute when the threshold signature changed, delta-only
    otherwise) — ported rather than imported because it's inseparable from
    how rows are loaded (`cloud.db` vs. SQLite), but the actual comparison
    kernel (`match_index_pairs`) is the exact same imported function."""
    signature = _signature(config, use_clip)

    with cloud_db.connect(database_url) as conn:
        stored = cloud_db.get_match_signature(conn, org_id)
        refs = cloud_db.get_reference_images(conn, org_id)
        sites = cloud_db.get_site_images(conn, org_id)
        full = stored != signature

        if full:
            ref_rows = list(refs)
            clear_ref_ids = [row["id"] for row in refs]
            clear_site_ids = [row["id"] for row in sites]
            old_refs = []
        else:
            ref_rows = [row for row in refs if row["compared_at"] is None]
            new_sites = [row for row in sites if row["compared_at"] is None]
            if not ref_rows and not new_sites:
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM matches WHERE org_id = %s", (org_id,)
                ).fetchone()
                return count_row["c"]
            clear_ref_ids = [row["id"] for row in ref_rows]
            clear_site_ids = [row["id"] for row in new_sites]
            old_refs = [row for row in refs if row["compared_at"] is not None] if new_sites else []

    rectangles: list[tuple[list, list]] = []
    if full:
        rectangles.append((list(refs), list(sites)))
    else:
        if ref_rows:
            rectangles.append((ref_rows, list(sites)))
        if old_refs and clear_site_ids:
            rectangles.append((old_refs, [row for row in sites if row["compared_at"] is None]))

    hits: list[tuple] = []
    seen: set[tuple] = set()
    total_steps = 0
    prepared = []
    for ref_part, site_part in rectangles:
        if not ref_part or not site_part:
            continue
        prepared.append((_pack_rows_cloud(ref_part, use_clip), _pack_rows_cloud(site_part, use_clip)))
        total_steps += 1
    done_steps = 0

    def on_chunk(done: int, total: int) -> None:
        nonlocal done_steps
        done_steps += 1
        if progress:
            progress(done_steps, max(total_steps, 1))

    for ref_pack, site_pack in prepared:
        ref_ids, ref_ph, ref_dh, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok = ref_pack
        site_ids, site_ph, site_dh, site_p_ok, site_d_ok, site_emb, site_emb_ok = site_pack
        found = match_index_pairs(
            ref_ids, ref_ph, ref_dh, ref_p_ok, ref_d_ok, ref_emb, ref_emb_ok,
            site_ids, site_ph, site_dh, site_p_ok, site_d_ok, site_emb, site_emb_ok,
            config.match, use_clip,
            progress=on_chunk,
            should_stop=should_stop,
        )
        for hit in found:
            key = (hit[0], hit[1])
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)

    if should_stop and should_stop():
        raise MatchStopped()

    with cloud_db.connect(database_url) as conn:
        if full:
            cloud_db.clear_matches(conn, org_id)
        else:
            cloud_db.delete_matches_for(conn, org_id=org_id, reference_ids=clear_ref_ids, site_ids=clear_site_ids)
        if hits:
            cloud_db.write_matches(conn, org_id, hits)
        stamped_refs = [row["id"] for row in refs] if full else clear_ref_ids
        stamped_sites = [row["id"] for row in sites] if full else clear_site_ids
        cloud_db.stamp_compared(conn, stamped_refs, stamped_sites)
        cloud_db.set_match_signature(conn, org_id, signature)
        count_row = conn.execute("SELECT COUNT(*) AS c FROM matches WHERE org_id = %s", (org_id,)).fetchone()
        return count_row["c"]


# --- report -----------------------------------------------------------

def _thumbnail_data_uri(data: Optional[bytes], max_size: int = 220) -> Optional[str]:
    if not data:
        return None
    try:
        import base64

        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception:
        return None


def _download_for_thumb(storage_client: Client, bucket: str, path: Optional[str]) -> Optional[bytes]:
    if not path:
        return None
    try:
        return cloud_storage.download(storage_client, bucket, path)
    except Exception:
        return None


def generate_report(
    org_id: uuid.UUID,
    database_url: str,
    storage_client: Client,
    config: Config,
    *,
    within_days: Optional[int] = None,
    generated_by: Optional[uuid.UUID] = None,
    review_status: str = "pending",
) -> uuid.UUID:
    """Builds the same `ReportRow`/`NotFoundRow` shapes and reuses
    `report.render_html`/`write_csv`/`write_not_found_csv` unchanged —
    only where the thumbnails and matched rows come from differs
    (Storage + Postgres instead of local disk + SQLite). Uploads the
    three files to the `reports` bucket and records the run in the
    `reports` table so a client can look back at a past report, not just
    the live state.

    `review_status` mirrors the local product's `report --status`
    (supabase/migrations/migration_008_review_status_and_exclusions.sql):
    `"all"` includes every match, anything else keeps only that status."""
    from datetime import datetime, timezone

    within_days = within_days if within_days is not None else config.report.default_within_days
    today = datetime.now(timezone.utc).date()

    with cloud_db.connect(database_url) as conn:
        match_rows = cloud_db.get_matches(conn, org_id)
        unmatched_rows = cloud_db.get_unmatched_references(conn, org_id)
        stats = asdict(cloud_db.get_stats(conn, org_id))
        pages_cache: dict[uuid.UUID, list[str]] = {}
        for m in match_rows:
            if m["site_image_id"] not in pages_cache:
                pages_cache[m["site_image_id"]] = cloud_db.get_pages_for_image(conn, m["site_image_id"])

    rows: list[report_module.ReportRow] = []
    for m in match_rows:
        if review_status != "all" and m["match_status"] != review_status:
            continue
        days_left = report_module.days_until(m["expiry_date"].isoformat() if m["expiry_date"] else None, today)
        if days_left is not None and days_left > within_days:
            continue
        rows.append(
            report_module.ReportRow(
                filename=m["filename"],
                expiry_date=m["expiry_date"].isoformat() if m["expiry_date"] else None,
                days_left=days_left,
                status=report_module.urgency_status(days_left),
                credit=m["credit"],
                notes=m["notes"],
                pages=pages_cache.get(m["site_image_id"], []),
                site_url=m["site_url"],
                level=m["level"],
                score=m["score"],
                confidence=m["confidence"],
                ref_thumbnail=_thumbnail_data_uri(
                    _download_for_thumb(storage_client, cloud_storage.BUCKET_REFS, m["ref_storage_path"])
                ),
                site_thumbnail=_thumbnail_data_uri(
                    _download_for_thumb(storage_client, cloud_storage.BUCKET_SITE_IMAGES, m["site_storage_path"])
                ),
            )
        )
    rows.sort(key=lambda r: (1, 0) if r.days_left is None else (0, r.days_left))

    not_found_rows: list[report_module.NotFoundRow] = []
    for r in unmatched_rows:
        days_left = report_module.days_until(r["expiry_date"].isoformat() if r["expiry_date"] else None, today)
        if days_left is not None and days_left > within_days:
            continue
        not_found_rows.append(
            report_module.NotFoundRow(
                filename=r["filename"],
                expiry_date=r["expiry_date"].isoformat() if r["expiry_date"] else None,
                days_left=days_left,
                status=report_module.urgency_status(days_left),
                credit=r["credit"],
                notes=r["notes"],
                compared=r["compared_at"] is not None,
                ref_thumbnail=_thumbnail_data_uri(
                    _download_for_thumb(storage_client, cloud_storage.BUCKET_REFS, r["ref_storage_path"])
                ),
            )
        )
    not_found_rows.sort(key=lambda r: (1, 0) if r.days_left is None else (0, r.days_left))

    report_id = uuid.uuid4()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        html_path = tmp_path / "report.html"
        csv_path = tmp_path / "matches.csv"
        not_found_csv_path = tmp_path / "not_found.csv"
        report_module.render_html(rows, html_path, within_days=within_days, site_stats=stats, not_found=not_found_rows)
        report_module.write_csv(rows, csv_path)
        report_module.write_not_found_csv(not_found_rows, not_found_csv_path)

        base = cloud_storage.path_for(org_id, str(report_id))
        html_storage_path = f"{base}/report.html"
        csv_storage_path = f"{base}/matches.csv"
        not_found_storage_path = f"{base}/not_found.csv"
        cloud_storage.upload(storage_client, cloud_storage.BUCKET_REPORTS, html_storage_path, html_path.read_bytes(), content_type="text/html")
        cloud_storage.upload(storage_client, cloud_storage.BUCKET_REPORTS, csv_storage_path, csv_path.read_bytes(), content_type="text/csv")
        cloud_storage.upload(storage_client, cloud_storage.BUCKET_REPORTS, not_found_storage_path, not_found_csv_path.read_bytes(), content_type="text/csv")

    with cloud_db.connect(database_url) as conn:
        return cloud_db.create_report(
            conn,
            org_id=org_id,
            within_days=within_days,
            storage_path_html=html_storage_path,
            storage_path_csv=csv_storage_path,
            storage_path_not_found_csv=not_found_storage_path,
            stats=stats,
            generated_by=generated_by,
        )
