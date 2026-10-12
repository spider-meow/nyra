"""What a month of Nyra costs in Supabase usage, for N brands. Pure functions over plain numbers.

One `Brand` describes what a brand holds and how it is used in a month; `estimate` turns it into the figures of
Supabase's usage page (egress, storage, database, users), with the egress split by source so the biggest one is
visible. Sizes are measured when `measured` reads them from the `insights` views, assumed otherwise: every field
below says which. Compare the result with the dashboard (Usage > Egress is split by service) and adjust.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

# A 512-float CLIP vector read over psycopg as text (not verified: measure with `SELECT avg(length(embedding::text))`),
# and as stored (4 bytes per float + header).
EMBEDDING_TEXT_BYTES = 5_500
EMBEDDING_STORED_BYTES = 2_060
ROW_BYTES = 600  # the other columns and indexes of an image row (assumed; the costs doc measures ~10 MB per brand)

# Supabase quotas (Free, Pro): egress per month, storage and database size, monthly active users.
LIMITS = {"egress": (5e9, 250e9), "storage": (1e9, 100e9), "database": (0.5e9, 8e9), "users": (50_000, 100_000)}


@dataclass(frozen=True)
class Brand:
    # What a brand holds. Defaults are the costs doc's assumptions; `measured` replaces them with your data.
    references: int = 500
    ref_bytes: int = 5_000_000  # an original reference (HD supplied by the brand)
    site_images: int = 1_500  # distinct files read on its sites
    work_bytes: int = 150_000  # the 1024 px working copy of an image
    thumb_bytes: int = 14_000  # the 320 px thumbnail
    pages: int = 300
    matches: int = 2_000
    # How it is used, per month.
    new_images: int = 200  # site images that appear
    new_references: int = 20
    crawls: int = 4  # each one is followed by a comparison
    match_jobs: int = 8  # comparisons, including those after an upload
    verified_per_match: int = 300  # images read for the keypoint check of CLIP candidates in one comparison (assumed)
    locates: int = 20  # "find this reference's copies" requests; each downloads the original
    reports: int = 2
    users: int = 5
    sessions_per_user: int = 12
    thumbs_per_session: int = 300  # thumbnails a session loads (the browser keeps them across visits)
    reviews_per_session: int = 30  # comparison screens opened: one working copy each
    web_db_bytes_per_session: int = 300_000  # lists and polling the web process reads from Postgres
    duplicate_lookups: int = 300  # per crawl: a known image under a new URL reads its embedding back
    months: int = 12  # how long it has been running, for storage and database size


def estimate(brand: Brand, brands: int, *, cache_mb: int = 2048) -> dict[str, Any]:
    """Monthly figures in bytes (`users` in people) for `brands` identical brands served by one worker.

    `cache_mb` is the worker's image cache (NYRA_IMAGE_CACHE_MB; 0 when it has no persistent disk). Once the
    working copies of all brands exceed it, reads miss in proportion; the first month also fills it.
    """
    b = brand
    working_set = brands * (b.references + b.site_images) * b.work_bytes
    miss = 1.0 if cache_mb <= 0 else max(0.0, 1 - cache_mb * 1e6 / working_set) if working_set else 0.0
    sessions = brands * b.users * b.sessions_per_user
    images = b.references + b.site_images + b.months * b.new_images
    egress = {
        "worker reads working copies": brands * (b.new_references + b.match_jobs * b.verified_per_match * miss) * b.work_bytes,
        "worker reads reference originals (locate)": brands * b.locates * (b.ref_bytes + b.site_images * b.work_bytes * miss),
        "worker reads embeddings": brands * (b.match_jobs * (b.references + b.site_images) + b.crawls * b.duplicate_lookups)
        * EMBEDDING_TEXT_BYTES,
        "worker reads thumbnails for reports": brands * b.reports * b.matches * 2 * b.thumb_bytes,
        "browsers load thumbnails and working copies": sessions * (b.thumbs_per_session * b.thumb_bytes
                                                                   + b.reviews_per_session * b.work_bytes),
        "web process reads lists from Postgres": sessions * b.web_db_bytes_per_session,
    }
    storage = brands * (b.references * (b.ref_bytes + b.work_bytes + b.thumb_bytes)
                        + (b.site_images + b.months * b.new_images) * (b.work_bytes + b.thumb_bytes)
                        + b.months * b.reports * b.matches * 2 * b.thumb_bytes * 1.34)
    database = brands * (images * (EMBEDDING_STORED_BYTES + ROW_BYTES) + b.pages * 500 + b.matches * 300)
    return {
        "egress": sum(egress.values()), "egress by source": egress,
        "first month extra egress": working_set if cache_mb > 0 else 0,
        "storage": storage, "database": database, "users": brands * b.users,
        "cache miss rate": miss,
    }


def measured(rows: dict[str, Any]) -> Brand:
    """A `Brand` with the sizes read from the database (`MEASURE_SQL`); a missing figure keeps its default."""
    stored = rows.get("stored_per_file")
    found = {
        "references": rows.get("references"), "ref_bytes": rows.get("ref_bytes"), "site_images": rows.get("site_images"),
        "pages": rows.get("pages"), "matches": rows.get("matches"),
        "work_bytes": stored and max(1, int(stored) - Brand.thumb_bytes),
    }
    return replace(Brand(), **{key: int(value) for key, value in found.items() if value})


# Averages over the brands that have been read at least once.
MEASURE_SQL = """
SELECT AVG(l.references_total) AS references, AVG(l.avg_bytes) AS ref_bytes, AVG(s.distinct_files) AS site_images,
       SUM(s.stored_bytes)::float8 / NULLIF(SUM(s.distinct_files) FILTER (WHERE s.stored_bytes IS NOT NULL), 0) AS stored_per_file,
       AVG(s.pages_read) AS pages,
       (SELECT SUM(matches) FROM insights.matching)::float8 / COUNT(*) AS matches
FROM insights.library l JOIN insights.site_images s ON s.brand_id = l.brand_id WHERE s.distinct_files > 0"""


def override(brand: Brand, settings: list[str]) -> Brand:
    """`key=value` pairs (`new_images=400`) applied to a brand; an unknown key is an error, not ignored."""
    known = {field.name for field in fields(Brand)}
    changes: dict[str, int] = {}
    for item in settings:
        key, _, value = item.partition("=")
        if key not in known or not value.strip().isdigit():
            raise ValueError(f"{item!r}: expected one of {sorted(known)} = a whole number")
        changes[key] = int(value)
    return replace(brand, **changes)


def _gb(value: float) -> str:
    return f"{value / 1e9:.2f} GB"


def render(result: dict[str, Any], brands: int, cache_mb: int) -> list[str]:
    """The figures as text lines, each against the Free and Pro quotas."""
    lines = [f"{brands} brand(s), worker image cache {cache_mb} MB (miss rate {result['cache miss rate']:.0%})", ""]
    for name, value in result["egress by source"].items():
        lines.append(f"  {name:<46}{_gb(value):>12}")
    lines.append("")
    for name, quota in LIMITS.items():
        value = result.get(name, 0)
        shown = f"{value:,.0f} people" if name == "users" else _gb(value)
        free, pro = quota
        lines.append(f"{name:<14}{shown:>16}   Free {value / free:>6.0%}   Pro {value / pro:>6.0%}")
    lines.append("")
    lines.append(f"First month adds {_gb(result['first month extra egress'])} of egress to fill the worker's image cache.")
    lines.append("Cached egress (CDN hits) is not modelled apart: browser loads count as egress, the worst case.")
    return lines
