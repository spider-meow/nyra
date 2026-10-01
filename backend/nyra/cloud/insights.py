"""Statistics for the insights pages, read from the `insights` views.

Two audiences, one source:
- `brand_insights` — one brand's numbers, for its organization's admins
  (`/o/<org>/m/<brand>/statistiques`);
- `platform_insights` — every brand of every organization side by side,
  plus the health of the job queue, for the Nyra team (`/interne`,
  `platform_staff` only).

The views live in `supabase/migrations/20260929000015_insights.sql`;
Grafana reads the same ones (docs/OBSERVABILITY.md). Everything returned
here is plain JSON: numbers are floats or ints, dates ISO strings.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg

Row = dict[str, Any]

# Crawls shown in the history charts, and averaged in the summaries.
HISTORY_RUNS = 30
PHASES = ("render_seconds", "download_seconds", "process_seconds", "embed_seconds", "store_seconds")


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _one(conn: psycopg.Connection, sql: str, params: Any = None) -> Row:
    row = conn.execute(sql, params).fetchone()
    return _plain(dict(row)) if row else {}


def _all(conn: psycopg.Connection, sql: str, params: Any = None) -> list[Row]:
    return [_plain(dict(row)) for row in conn.execute(sql, params).fetchall()]


def _merge_counts(rows: list[Row], key: str) -> dict[str, int]:
    """Sum jsonb {name: count} objects across rows."""
    total: dict[str, int] = {}
    for row in rows:
        for name, count in (row.get(key) or {}).items():
            total[name] = total.get(name, 0) + int(count or 0)
    return dict(sorted(total.items(), key=lambda item: -item[1]))


# --- staff -----------------------------------------------------------------------

def is_staff(conn: psycopg.Connection, user_id: uuid.UUID) -> bool:
    return conn.execute("SELECT 1 FROM platform_staff WHERE user_id = %s", (user_id,)).fetchone() is not None


def set_staff(conn: psycopg.Connection, user_id: uuid.UUID, staff: bool) -> None:
    if staff:
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    else:
        conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (user_id,))


# --- building blocks ----------------------------------------------------------------

_RUN_COLUMNS = """
    id, org_id, org_name, brand_id, brand_name, site_url, status, started_at, finished_at, duration_seconds,
    pages_visited, images_found, images_stored, images_new, blocked_by_robots, error_count,
    sitemap_urls, pages_failed, images_known, images_duplicate, images_rejected,
    downloads, downloads_failed, bytes_downloaded, bytes_new, thumb_bytes_new, pixels_new,
    discover_seconds, render_seconds, download_seconds, process_seconds, embed_seconds, embedded, store_seconds,
    http_statuses, formats_new,
    pages_per_minute, images_scanned_per_second, clip_images_per_second, seconds_per_page,
    avg_new_image_bytes, images_per_page
"""


def _crawl_summary(runs: list[Row]) -> Row:
    """Averages over finished runs, and totals. Runs without a measurement don't count toward averages."""
    finished = [run for run in runs if run["status"] == "done"]

    def avg(key: str) -> Optional[float]:
        values = [run[key] for run in finished if run.get(key) is not None]
        return sum(values) / len(values) if values else None

    embedded = sum(run.get("embedded") or 0 for run in finished)
    embed_seconds = sum(run.get("embed_seconds") or 0 for run in finished)
    bytes_new = sum(run.get("bytes_new") or 0 for run in finished)
    images_new = sum(run.get("images_new") or 0 for run in finished if run.get("bytes_new") is not None)
    phases = {phase: sum(run.get(phase) or 0 for run in finished) for phase in PHASES}
    return {
        "runs": len(runs),
        "finished": len(finished),
        "failed": sum(1 for run in runs if run["status"] == "error"),
        "avg_duration_seconds": avg("duration_seconds"),
        "max_duration_seconds": max((run["duration_seconds"] or 0 for run in finished), default=None),
        "avg_pages": avg("pages_visited"),
        "avg_pages_per_minute": avg("pages_per_minute"),
        "avg_images_scanned_per_second": avg("images_scanned_per_second"),
        "avg_seconds_per_page": avg("seconds_per_page"),
        "avg_images_per_page": avg("images_per_page"),
        # Weighted: total images embedded / total time spent embedding.
        "clip_images_per_second": embedded / embed_seconds if embed_seconds else None,
        "avg_new_image_bytes": bytes_new / images_new if images_new else None,
        "images_found": sum(run.get("images_found") or 0 for run in finished),
        "images_new": sum(run.get("images_new") or 0 for run in finished),
        "bytes_downloaded": sum(run.get("bytes_downloaded") or 0 for run in finished),
        "bytes_new": bytes_new,
        "phases_seconds": phases,
        "http_statuses": _merge_counts(finished, "http_statuses"),
        "formats_new": _merge_counts(finished, "formats_new"),
    }


def _jobs_by_kind(conn: psycopg.Connection, where: str, params: Any) -> list[Row]:
    return _all(conn, f"""
        SELECT kind,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'done') AS done,
               COUNT(*) FILTER (WHERE status = 'error') AS failed,
               COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
               AVG(run_seconds) FILTER (WHERE status = 'done') AS avg_run_seconds,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY run_seconds) FILTER (WHERE status = 'done')
                   AS p95_run_seconds,
               AVG(wait_seconds) AS avg_wait_seconds
        FROM insights.jobs WHERE {where}
        GROUP BY kind ORDER BY kind""", params)


def _last_compare(conn: psycopg.Connection, where: str, params: Any) -> Optional[Row]:
    row = _one(conn, f"""
        SELECT finished_at, match_metrics FROM insights.jobs
        WHERE {where} AND status = 'done' AND COALESCE((match_metrics ->> 'pairs')::bigint, 0) > 0
        ORDER BY finished_at DESC LIMIT 1""", params)
    if not row:
        return None
    metrics = row["match_metrics"] or {}
    seconds = metrics.get("seconds") or 0
    return {**metrics, "finished_at": row["finished_at"],
            "pairs_per_second": metrics.get("pairs", 0) / seconds if seconds else None}


def _matching(conn: psycopg.Connection, brand_id: Optional[uuid.UUID]) -> Row:
    if brand_id:
        rows = _all(conn, "SELECT * FROM insights.matching WHERE brand_id = %s", (brand_id,))
    else:  # the whole platform: summed per band and level here, so the rows don't grow with the brands
        rows = _all(conn, """
            SELECT confidence, level, SUM(matches)::bigint AS matches, SUM(reviewed)::bigint AS reviewed,
                   SUM(to_remove)::bigint AS to_remove, SUM(removed)::bigint AS removed,
                   SUM(false_positives)::bigint AS false_positives
            FROM insights.matching GROUP BY confidence, level""")
    by_confidence: dict[str, Row] = {}
    by_level: dict[str, Row] = {}
    for row in rows:
        for bucket, name in ((by_confidence, row["confidence"]), (by_level, row["level"])):
            entry = bucket.setdefault(name, {"matches": 0, "reviewed": 0, "to_remove": 0, "removed": 0,
                                             "false_positives": 0})
            for key in ("matches", "reviewed", "to_remove", "removed", "false_positives"):
                entry[key] += row[key] or 0
    for bucket in (by_confidence, by_level):
        for entry in bucket.values():
            entry["false_positive_rate"] = (entry["false_positives"] / entry["reviewed"]) if entry["reviewed"] else None
    total = {key: sum(entry[key] for entry in by_confidence.values())
             for key in ("matches", "reviewed", "to_remove", "removed", "false_positives")}
    total["false_positive_rate"] = total["false_positives"] / total["reviewed"] if total["reviewed"] else None
    total["review_progress"] = total["reviewed"] / total["matches"] if total["matches"] else None
    return {"total": total, "by_confidence": by_confidence, "by_level": by_level}


# --- one brand -------------------------------------------------------------------------

def brand_insights(conn: psycopg.Connection, brand_id: uuid.UUID) -> Row:
    runs = _all(conn, f"""SELECT {_RUN_COLUMNS} FROM insights.crawl_runs
                          WHERE brand_id = %s ORDER BY started_at DESC LIMIT %s""", (brand_id, HISTORY_RUNS))
    return {
        "site": _one(conn, "SELECT * FROM insights.site_images WHERE brand_id = %s", (brand_id,)),
        "formats": _all(conn, """SELECT format, files, total_bytes, avg_bytes FROM insights.site_image_formats
                                  WHERE brand_id = %s ORDER BY files DESC""", (brand_id,)),
        "library": _one(conn, "SELECT * FROM insights.library WHERE brand_id = %s", (brand_id,)),
        "crawls": {
            "summary": _crawl_summary(runs),
            "last": runs[0] if runs else None,
            # Oldest first, for the charts.
            "history": list(reversed(runs)),
        },
        "jobs": _jobs_by_kind(conn, "brand_id = %s", (brand_id,)),
        "compare": _last_compare(conn, "brand_id = %s", (brand_id,)),
        "matching": _matching(conn, brand_id),
    }


# --- every brand ------------------------------------------------------------------------
#
# The page is for the Nyra team, who read it every 30 s: nothing here may grow with the number of brands
# or runs beyond what is shown. Every platform figure is a SQL aggregate, each source is read once (no
# per-brand subquery), and only the rows the page displays leave the database.

# The brands listed on the page: the most recently active (last crawl or job), not all of them. The
# totals still count every brand.
PLATFORM_BRANDS_LIMIT = 200
# Crawls shown in the platform history chart.
PLATFORM_HISTORY_RUNS = 60
_CRAWL_WINDOW = "started_at > now() - interval '90 days'"
_DONE = "FILTER (WHERE status = 'done')"

# What `_crawl_summary` computes in Python, as SQL aggregates over a group of runs (columns named after
# its keys, in its order). Rates are ratios of sums, as in the Python version: images embedded / seconds
# embedding, bytes of new images / new images measured.
_RUN_AGGREGATES = f"""
    COUNT(*) AS runs,
    COUNT(*) {_DONE} AS finished,
    COUNT(*) FILTER (WHERE status = 'error') AS failed,
    AVG(duration_seconds) {_DONE} AS avg_duration_seconds,
    MAX(COALESCE(duration_seconds, 0)) {_DONE} AS max_duration_seconds,
    AVG(pages_visited::float8) {_DONE} AS avg_pages,
    AVG(pages_per_minute) {_DONE} AS avg_pages_per_minute,
    AVG(images_scanned_per_second) {_DONE} AS avg_images_scanned_per_second,
    AVG(seconds_per_page) {_DONE} AS avg_seconds_per_page,
    AVG(images_per_page) {_DONE} AS avg_images_per_page,
    SUM(embedded) {_DONE}::float8 / NULLIF(SUM(embed_seconds) {_DONE}, 0) AS clip_images_per_second,
    SUM(bytes_new) {_DONE}::float8
        / NULLIF(SUM(images_new) FILTER (WHERE status = 'done' AND bytes_new IS NOT NULL), 0) AS avg_new_image_bytes,
    COALESCE(SUM(images_found) {_DONE}, 0) AS images_found,
    COALESCE(SUM(images_new) {_DONE}, 0) AS images_new,
    COALESCE(SUM(bytes_downloaded) {_DONE}, 0)::bigint AS bytes_downloaded,
    COALESCE(SUM(bytes_new) {_DONE}, 0)::bigint AS bytes_new,
    {", ".join(f"COALESCE(SUM({phase}) {_DONE}, 0) AS {phase}" for phase in PHASES)}"""

# One row per brand, computed once: its place in the 90-day crawls, the reviews and the 30-day jobs, then
# the platform totals over ALL brands, then the brands kept, whatever the order they are listed in.
# Brands without a figure get NULL (or 0 for counts), like the old per-brand lookups did.
_PLATFORM_BRANDS_SQL = f"""
WITH brand_runs AS (
    SELECT brand_id, {_RUN_AGGREGATES},
           MAX(started_at) AS last_crawl_at,
           (ARRAY_AGG(status ORDER BY started_at DESC))[1] AS last_crawl_status,
           (ARRAY_AGG(duration_seconds ORDER BY started_at DESC))[1] AS last_crawl_seconds
    FROM insights.crawl_runs WHERE {_CRAWL_WINDOW} GROUP BY brand_id),
brand_matches AS (
    SELECT brand_id, SUM(matches)::bigint AS matches, SUM(reviewed) AS reviewed, SUM(false_positives) AS false_positives
    FROM insights.matching GROUP BY brand_id),
brand_jobs AS (
    SELECT brand_id, COUNT(*) AS jobs_30d, COUNT(*) FILTER (WHERE status = 'error') AS failed_30d,
           MAX(created_at) AS last_job_at
    FROM insights.jobs WHERE created_at > now() - interval '30 days' GROUP BY brand_id),
org_members AS (SELECT org_id, COUNT(*) AS members FROM memberships GROUP BY org_id),
figures AS (
    SELECT b.id AS brand_id, b.name, b.slug, o.id AS org_id, o.name AS org_name, o.slug AS org_slug,
           s.distinct_files, s.total_bytes AS site_bytes, s.avg_bytes AS avg_image_bytes,
           s.stored_bytes AS site_stored_bytes, s.pages_read, s.sites,
           l.references_total, l.expired, l.expiring_90_days, l.total_bytes AS library_bytes,
           COALESCE(m.matches, 0) AS matches, COALESCE(om.members, 0) AS members,
           -- What Nyra keeps: working copies + thumbnails of site images (the originals stay on the sites;
           -- older rows without that figure count their weight on the site), the library's originals.
           COALESCE(NULLIF(s.stored_bytes, 0), NULLIF(s.total_bytes, 0), 0) + COALESCE(l.total_bytes, 0)
               AS storage_bytes,
           COALESCE(r.runs, 0) AS crawls_90d, r.last_crawl_at, r.last_crawl_status, r.last_crawl_seconds,
           r.avg_duration_seconds AS avg_crawl_seconds, r.avg_pages_per_minute, r.clip_images_per_second,
           m.false_positives::float8 / NULLIF(m.reviewed, 0) AS false_positive_rate,
           COALESCE(j.jobs_30d, 0) AS jobs_30d, COALESCE(j.failed_30d, 0) AS failed_jobs_30d,
           j.last_job_at
    FROM brands b
    JOIN organizations o ON o.id = b.org_id
    LEFT JOIN insights.site_images s ON s.brand_id = b.id
    LEFT JOIN insights.library l ON l.brand_id = b.id
    LEFT JOIN brand_matches m ON m.brand_id = b.id
    LEFT JOIN org_members om ON om.org_id = o.id
    LEFT JOIN brand_runs r ON r.brand_id = b.id
    LEFT JOIN brand_jobs j ON j.brand_id = b.id),
totals AS (
    SELECT COUNT(DISTINCT org_id) AS total_organizations, COUNT(*) AS total_brands,
           (SELECT COUNT(DISTINCT user_id) FROM memberships) AS total_members,
           COALESCE(SUM(references_total), 0)::bigint AS total_references,
           COALESCE(SUM(distinct_files), 0)::bigint AS total_site_files,
           COALESCE(SUM(pages_read), 0)::bigint AS total_pages_read,
           COALESCE(SUM(matches), 0)::bigint AS total_matches,
           COALESCE(SUM(storage_bytes), 0) AS total_storage_bytes
    FROM figures),
kept AS (
    SELECT * FROM figures
    ORDER BY GREATEST(last_crawl_at, last_job_at) DESC NULLS LAST, org_name, name, brand_id LIMIT %s)
SELECT * FROM totals LEFT JOIN kept ON TRUE ORDER BY kept.org_name, kept.name, kept.brand_id"""


def _platform_brands(conn: psycopg.Connection) -> tuple[Row, list[Row]]:
    """The platform totals (every brand) and the brands listed (at most PLATFORM_BRANDS_LIMIT), by name."""
    rows = _all(conn, _PLATFORM_BRANDS_SQL, (PLATFORM_BRANDS_LIMIT,))
    totals = {key.removeprefix("total_"): value for key, value in rows[0].items() if key.startswith("total_")}
    brands = [{key: value for key, value in row.items() if not key.startswith("total_") and key != "last_job_at"}
              for row in rows if row["brand_id"] is not None]
    return totals, brands


# `http_statuses` and `formats_new` of the finished runs, summed per key, most frequent first.
_RUN_COUNTS_SQL = f"""
    SELECT k.kind, e.key, COALESCE(SUM(trunc(e.value::numeric)), 0)::bigint AS n
    FROM insights.crawl_runs r
    CROSS JOIN LATERAL (VALUES ('http_statuses', r.http_statuses), ('formats_new', r.formats_new)) AS k(kind, counts)
    CROSS JOIN LATERAL jsonb_each_text(CASE WHEN jsonb_typeof(k.counts) = 'object' THEN k.counts ELSE '{{}}' END) AS e
    WHERE r.status = 'done' AND r.{_CRAWL_WINDOW}
    GROUP BY k.kind, e.key ORDER BY n DESC, e.key"""


def _platform_crawl_summary(conn: psycopg.Connection) -> Row:
    """`_crawl_summary` of every crawl of the last 90 days, from SQL aggregates instead of the rows."""
    summary = _one(conn, f"SELECT {_RUN_AGGREGATES} FROM insights.crawl_runs WHERE {_CRAWL_WINDOW}")
    phases = {phase: summary.pop(phase) for phase in PHASES}
    counts: dict[str, dict[str, int]] = {"http_statuses": {}, "formats_new": {}}
    for row in _all(conn, _RUN_COUNTS_SQL):
        counts[row["kind"]][row["key"]] = row["n"]
    return {**summary, "phases_seconds": phases, **counts}


def _platform_history(conn: psycopg.Connection) -> list[Row]:
    """The latest PLATFORM_HISTORY_RUNS crawls, oldest first, with only the columns the chart reads."""
    runs = _all(conn, f"""
        SELECT org_name, brand_name, started_at, status, duration_seconds, pages_visited, images_found, images_new,
               pages_per_minute, images_scanned_per_second, clip_images_per_second, avg_new_image_bytes
        FROM insights.crawl_runs WHERE {_CRAWL_WINDOW} ORDER BY started_at DESC LIMIT %s""", (PLATFORM_HISTORY_RUNS,))
    return runs[::-1]


def _queue_health(conn: psycopg.Connection) -> Row:
    """The job queue: counts and ages, the running jobs, the latest failures."""
    queue = _one(conn, """
        SELECT COUNT(*) FILTER (WHERE status = 'queued') AS queued,
               COUNT(*) FILTER (WHERE status = 'running') AS running,
               EXTRACT(EPOCH FROM now() - MIN(created_at) FILTER (WHERE status = 'queued')) AS oldest_queued_seconds,
               EXTRACT(EPOCH FROM now() - MAX(heartbeat_at) FILTER (WHERE status = 'running')) AS last_heartbeat_seconds,
               COUNT(*) FILTER (WHERE created_at > now() - interval '24 hours') AS jobs_24h,
               COUNT(*) FILTER (WHERE status = 'error' AND finished_at > now() - interval '24 hours') AS failed_24h,
               MAX(finished_at) AS last_finished_at
        FROM jobs""")
    running = _all(conn, """
        SELECT j.id, o.name AS org_name, b.name AS brand_name, j.kind, j.message, j.progress, j.started_at,
               EXTRACT(EPOCH FROM now() - j.heartbeat_at) AS heartbeat_age_seconds
        FROM jobs j JOIN organizations o ON o.id = j.org_id LEFT JOIN brands b ON b.id = j.brand_id
        WHERE j.status = 'running' ORDER BY j.started_at""")
    failures = _all(conn, """
        SELECT id, org_name, brand_name, kind, error, finished_at FROM insights.jobs
        WHERE status = 'error' ORDER BY finished_at DESC NULLS LAST LIMIT 15""")
    return {"queue": queue, "running": running, "failures": failures}



def platform_insights(conn: psycopg.Connection) -> Row:
    # These queries are big plans over a few ms of real work: Postgres' JIT compiler would spend seconds
    # (measured: 2.6 s of 3 s at 1,000 brands) compiling them. Off for this transaction only.
    conn.execute("SET LOCAL jit = off")
    totals, brands = _platform_brands(conn)
    return {
        "totals": totals,
        "brands": brands,
        # How many brands exist: `brands` holds at most PLATFORM_BRANDS_LIMIT of them.
        "total_brands": totals["brands"],
        "crawls": {"summary": _platform_crawl_summary(conn), "history": _platform_history(conn)},
        "jobs": _jobs_by_kind(conn, "created_at > now() - interval '30 days'", None),
        "compare": _last_compare(conn, "TRUE", None),
        "matching": _matching(conn, None),
        **_queue_health(conn),
    }
