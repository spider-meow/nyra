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
    where, params = ("brand_id = %s", (brand_id,)) if brand_id else ("TRUE", None)
    rows = _all(conn, f"SELECT * FROM insights.matching WHERE {where}", params)
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

def _platform_brands(conn: psycopg.Connection) -> list[Row]:
    return _all(conn, """
        SELECT b.id AS brand_id, b.name, b.slug, o.id AS org_id, o.name AS org_name, o.slug AS org_slug,
               s.distinct_files, s.total_bytes AS site_bytes, s.avg_bytes AS avg_image_bytes,
               s.stored_bytes AS site_stored_bytes, s.pages_read, s.sites,
               l.references_total, l.expired, l.expiring_90_days, l.total_bytes AS library_bytes,
               (SELECT COUNT(*) FROM matches m JOIN reference_images r ON r.id = m.reference_id
                 WHERE r.brand_id = b.id) AS matches,
               (SELECT COUNT(*) FROM memberships ms WHERE ms.org_id = o.id) AS members
        FROM brands b
        JOIN organizations o ON o.id = b.org_id
        LEFT JOIN insights.site_images s ON s.brand_id = b.id
        LEFT JOIN insights.library l ON l.brand_id = b.id
        ORDER BY o.name, b.name""")


def _platform_runs(conn: psycopg.Connection) -> list[Row]:
    return _all(conn, f"""SELECT {_RUN_COLUMNS} FROM insights.crawl_runs
                          WHERE started_at > now() - interval '90 days' ORDER BY started_at DESC""")


def _add_brand_figures(conn: psycopg.Connection, brands: list[Row], runs: list[Row]) -> None:
    """Add to each brand its crawl, review and job figures (the last 90 days of crawls, 30 of jobs)."""
    runs_by_brand: dict[str, list[Row]] = {}
    for run in runs:
        runs_by_brand.setdefault(run["brand_id"], []).append(run)
    fp_rates = {row["brand_id"]: row for row in _all(conn, """
        SELECT brand_id, SUM(reviewed) AS reviewed, SUM(false_positives) AS false_positives
        FROM insights.matching GROUP BY brand_id""")}
    job_errors = {row["brand_id"]: row for row in _all(conn, """
        SELECT brand_id, COUNT(*) AS jobs_30d, COUNT(*) FILTER (WHERE status = 'error') AS failed_30d
        FROM insights.jobs WHERE created_at > now() - interval '30 days' GROUP BY brand_id""")}

    for brand in brands:
        brand_runs = runs_by_brand.get(brand["brand_id"], [])
        summary = _crawl_summary(brand_runs)
        last = brand_runs[0] if brand_runs else None
        fp = fp_rates.get(brand["brand_id"]) or {}
        errors = job_errors.get(brand["brand_id"]) or {}
        brand.update({
            # What Nyra keeps: working copies + thumbnails of site images (the originals stay on the
            # sites; older rows without that figure count their weight on the site), the library's originals.
            "storage_bytes": (brand.get("site_stored_bytes") or brand.get("site_bytes") or 0)
                             + (brand.get("library_bytes") or 0),
            "crawls_90d": summary["runs"],
            "last_crawl_at": last["started_at"] if last else None,
            "last_crawl_status": last["status"] if last else None,
            "last_crawl_seconds": last["duration_seconds"] if last else None,
            "avg_crawl_seconds": summary["avg_duration_seconds"],
            "avg_pages_per_minute": summary["avg_pages_per_minute"],
            "clip_images_per_second": summary["clip_images_per_second"],
            "false_positive_rate": (fp["false_positives"] / fp["reviewed"]) if fp.get("reviewed") else None,
            "jobs_30d": errors.get("jobs_30d", 0),
            "failed_jobs_30d": errors.get("failed_30d", 0),
        })


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


def _platform_totals(conn: psycopg.Connection, brands: list[Row]) -> Row:
    organizations = {brand["org_id"] for brand in brands}
    return {
        "organizations": len(organizations),
        "brands": len(brands),
        "members": _one(conn, "SELECT COUNT(DISTINCT user_id) AS c FROM memberships")["c"],
        "references": sum(brand["references_total"] or 0 for brand in brands),
        "site_files": sum(brand["distinct_files"] or 0 for brand in brands),
        "pages_read": sum(brand["pages_read"] or 0 for brand in brands),
        "matches": sum(brand["matches"] or 0 for brand in brands),
        "storage_bytes": sum(brand["storage_bytes"] for brand in brands),
    }


def platform_insights(conn: psycopg.Connection) -> Row:
    brands = _platform_brands(conn)
    runs = _platform_runs(conn)
    _add_brand_figures(conn, brands, runs)
    health = _queue_health(conn)
    history = [{key: run[key] for key in (
        "org_name", "brand_name", "started_at", "status", "duration_seconds", "pages_visited", "images_found", "images_new",
        "pages_per_minute", "images_scanned_per_second", "clip_images_per_second", "avg_new_image_bytes",
    )} for run in reversed(runs[:60])]
    return {
        "totals": _platform_totals(conn, brands),
        "brands": brands,
        "crawls": {"summary": _crawl_summary(runs), "history": history},
        "jobs": _jobs_by_kind(conn, "created_at > now() - interval '30 days'", None),
        "compare": _last_compare(conn, "TRUE", None),
        "matching": _matching(conn, None),
        **health,
    }
