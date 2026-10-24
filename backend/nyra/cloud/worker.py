"""`nyra worker`: runs the jobs the web process enqueues.

Runs in its own process (and container): it's the only part of the
product that needs Chromium and torch. Several workers can run side by
side; `jobs.claim` hands each one a different job and never two jobs of
the same organization at once. Every job works on one brand: its library, its
sites.

Job kinds:
- crawl  — read one or several of the brand's sites, then compare (unless
           asked not to);
- match  — compare the brand's library with every image read on its sites;
- index  — fill in whatever a reference or site image is missing
           (CLIP embedding, mirror hashes, thumbnail), then compare. The
           web process enqueues one after every upload; it also
           backfills rows created before those columns existed;
- report — build report.html + the two CSVs and store them;
- locate — find every crawled image that is a crop or copy of one reference;
- check  — read again only the pages a person gives (50 at most), to see whether a visual is still on them.
"""

from __future__ import annotations

import errno
import itertools
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import psycopg

from nyra import fetch, netguard, observability
from nyra import report as report_module
from nyra.config import Config, load_config, with_overrides
from nyra.crawl import CrawlStats, crawl_pages, crawl_site, normalize_url
from nyra.estimate import remaining_seconds
from nyra.match import MatchStopped, compute_clip_embeddings, compute_flip_hashes, run_matching, warm_clip

from . import audit as cloud_audit
from . import db as cloud_db
from . import digest as cloud_digest
from . import hostmetrics
from . import jobs as cloud_jobs
from . import presence as cloud_presence
from . import schedule as cloud_schedule
from . import storage as cloud_storage
from . import usage as cloud_usage
from .locate import NotSearchable, locate_reference
from .store import CloudCrawlStore, CloudMatchStore
from .variants import update_variants

log = logging.getLogger("nyra.worker")

HEARTBEAT_SECONDS = 30
PROGRESS_SECONDS = 1.0
AUDIT_PURGE_SECONDS = 24 * 3600  # retention of the audit log, see `audit.purge`
SCHEDULE_SECONDS = 300  # how often the monthly reads are looked for, see `schedule.enqueue_due`
# The longest a job of each kind may run, in minutes, with what the message calls it and what lightens it. A job
# running longer is taken for hung (a frozen browser keeps the heartbeat thread beating): it is marked failed with that
# message and the worker exits, so the container restarts. STARTING VALUES, to adjust from the durations in the
# Statistiques page: a crawl reads every page of every address of the brand, then compares (a brand with several large
# sites is long); an index embeds images on CPU, then compares (a first backfill is long); a match, a locate and a
# report read stored data; a check reads 50 pages at most. Each can be set with `NYRA_JOB_LIMIT_<KIND>_MIN`.
JOB_LIMITS: dict[str, tuple[int, str, str]] = {
    "crawl": (12 * 60, "La lecture des sites", "le nombre de pages ou d'adresses"),
    "index": (6 * 60, "L'analyse des images", "le nombre d'images à analyser d'un coup"),
    "match": (60, "La comparaison", "le nombre d'images à comparer"),
    "locate": (60, "La recherche de copies", "le nombre d'images lues sur les sites"),
    "report": (30, "La génération du rapport", "la période du rapport"),
    "check": (30, "La vérification des pages", "le nombre de pages à relire"),
}
LIMIT_MIN_MINUTES, LIMIT_MAX_MINUTES = 1, 24 * 60  # what an override may ask for
# `_time_out` leaves the process after this long even if the database does not answer.
TIME_OUT_GRACE_SECONDS = 20
# The worker exits when its alive file is older than this (the image's health check calls 120 s unhealthy).
WATCHDOG_SECONDS = 300
# Chromium (Playwright) leaves its profile and scratch folders in the temp directory when it is killed; a folder in
# which nothing was written for this long is removed. At most `CLEAN_SCAN_LIMIT` entries of a folder are looked at.
TEMP_LEFTOVERS = ("playwright_chromiumdev_profile-*", "playwright-artifacts-*")
TEMP_LEFTOVER_AGE_SECONDS = 600
CLEAN_SCAN_LIMIT = 20_000
# Touched while the worker lives, idle or busy; the container's health check reads its age.
ALIVE_FILE = Path(os.environ.get("NYRA_WORKER_ALIVE_FILE") or Path(tempfile.gettempdir()) / "nyra-worker-alive")


def touch_alive() -> None:
    try:
        ALIVE_FILE.touch()
    except OSError:
        log.warning("could not touch %s; the health check will fail", ALIVE_FILE)


def stalled(path: Path, limit: float, now: float) -> bool:
    """True when `path` was last touched more than `limit` seconds before `now` (raises OSError if it is missing)."""
    return now - path.stat().st_mtime > limit


def _newest_mtime(folder: Path) -> float:
    """The latest modification time in `folder` and under it (a folder's own time ignores writes in its sub-folders).
    ponytail: stops after `CLEAN_SCAN_LIMIT` entries; a write only in the unseen rest of a huge profile is not seen."""
    newest = folder.stat().st_mtime
    for entry in itertools.islice(folder.rglob("*"), CLEAN_SCAN_LIMIT):
        try:
            newest = max(newest, entry.lstat().st_mtime)
        except OSError:
            continue  # gone since the listing
    return newest


def clean_temp(now: float, root: Optional[Path] = None) -> int:
    """Remove Chromium's leftover folders in the temp directory in which nothing was written for
    `TEMP_LEFTOVER_AGE_SECONDS`; returns how many are gone. Run at start (after a crash) and after each job. The
    quiet period spares a browser still running (another worker sharing this directory in development)."""
    removed = 0
    for pattern in TEMP_LEFTOVERS:
        for path in (root or Path(tempfile.gettempdir())).glob(pattern):
            try:
                if now - _newest_mtime(path) <= TEMP_LEFTOVER_AGE_SECONDS:
                    continue
            except OSError:
                continue  # gone since the listing: nothing to remove
            shutil.rmtree(path, ignore_errors=True)  # best effort: a leftover that stays is retried at the next job
            removed += not path.exists()
    return removed


def job_limit_seconds(kind: str) -> int:
    """The longest `kind` may run: `NYRA_JOB_LIMIT_<KIND>_MIN` if it is a whole number of minutes in range, else the default."""
    default = JOB_LIMITS[kind][0]
    setting = os.environ.get(f"NYRA_JOB_LIMIT_{kind.upper()}_MIN", "").strip()
    if not setting:
        return default * 60
    if setting.isdigit() and LIMIT_MIN_MINUTES <= int(setting) <= LIMIT_MAX_MINUTES:
        return int(setting) * 60
    log.warning("NYRA_JOB_LIMIT_%s_MIN=%r ignored: expected whole minutes from %d to %d", kind.upper(), setting,
                LIMIT_MIN_MINUTES, LIMIT_MAX_MINUTES)
    return default * 60


def overrun_message(kind: str) -> str:
    _, label, lighter = JOB_LIMITS[kind]
    return (f"{label} a dépassé sa durée maximale de {job_limit_seconds(kind) // 60} min. Le travail déjà enregistré est "
            f"conservé : relancez-la, ou réduisez {lighter}.")


class JobFailed(Exception):
    """A failure with a message fit for the interface."""


class JobContext:
    """Progress, cancellation and liveness for one running job."""

    def __init__(self, database_url: str, job: dict):
        self.database_url = database_url
        self.job = job
        self.cancelled = False
        self._last_report = 0.0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._started = time.monotonic()
        self.max_seconds = job_limit_seconds(job["kind"])
        self._beat = threading.Thread(target=self._beat_loop, name=f"heartbeat-{job['id']}", daemon=True)
        self._beat.start()

    def _beat_loop(self) -> None:
        while not self._done.wait(HEARTBEAT_SECONDS):
            touch_alive()  # before the database: a slow database must not look like a dead worker
            if time.monotonic() - self._started > self.max_seconds and not self._done.is_set():
                self._time_out()
            self._heartbeat()

    def _time_out(self) -> None:
        """Fail the job with a message that says why, close its crawl runs, and exit: a hung job cannot be stopped
        from a thread, and the container restarts. Only if this run is still running (it may have just ended, or
        been requeued): otherwise nothing is touched. The exit is forced after `TIME_OUT_GRACE_SECONDS` even if the
        database does not answer; the lost heartbeat then requeues the job."""
        log.error("job %s (%s) ran over %d s: marking it failed", self.job["id"], self.job["kind"], self.max_seconds)
        timer = threading.Timer(TIME_OUT_GRACE_SECONDS, os._exit, (70,))
        timer.daemon = True
        timer.start()
        try:
            with cloud_db.connect(self.database_url) as conn:
                failed = cloud_jobs.fail_running(conn, self.job, message=overrun_message(self.job["kind"]),
                                                 error=f"max duration {self.max_seconds} s")
        except Exception:  # noqa: BLE001 - exiting matters more
            log.exception("could not record the overrun of job %s", self.job["id"])
            failed = True
        try:
            if failed:
                os._exit(70)
        finally:
            timer.cancel()  # the run had already ended (this worker keeps going), or the exit was simulated by a test

    def _heartbeat(self, message: Optional[str] = None, progress: Optional[dict] = None) -> None:
        with self._lock:
            try:
                with cloud_db.connect(self.database_url) as conn:
                    if cloud_jobs.heartbeat(conn, self.job["id"], message=message, progress=progress):
                        self.cancelled = True
            except Exception:  # noqa: BLE001 - a missed heartbeat must not kill the job
                log.exception("heartbeat failed")

    def report(self, message: str, progress: Optional[dict] = None, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_report >= PROGRESS_SECONDS:
            self._last_report = now
            self._heartbeat(message, progress)

    def should_stop(self) -> bool:
        if time.monotonic() - self._last_report >= 2.0:
            self._last_report = time.monotonic()
            self._heartbeat()
        return self.cancelled

    def close(self) -> None:
        self._done.set()


class _CrawlProgress:
    """What a read reports about one address: the pages done out of the pages known so far, and the time left.

    The total is what is known to read (read + queued + in progress), not the cap, so a 40-page site with a
    sitemap is at 50% after 20 pages. Without a sitemap it grows as links are found; it never shrinks. The pace
    is measured from the first finished page, so the sitemap and the browser start-up don't slow it."""

    def __init__(self, ctx: JobContext, prefix: str, limit: int):
        self.ctx, self.prefix, self.limit = ctx, prefix, limit
        self.expected = 0
        self.first: Optional[tuple[float, int]] = None  # (time, pages done) at the first report with a page done

    def report(self, stats: CrawlStats) -> None:
        done = stats.pages_visited
        self.expected = max(self.expected, min(self.limit, max(1, done + stats.pages_queued)))
        if self.first is None and done:
            self.first = (time.monotonic(), done)
        eta = None
        if self.first:
            began, done_then = self.first
            eta = remaining_seconds(done - done_then, self.expected - done_then, time.monotonic() - began)
        self.ctx.report(
            f"{self.prefix}Page {done}/{self.expected} · {stats.images_new} nouvelle(s) image(s)",
            {"phase": "crawl", "done": done, "total": self.expected, "images_new": stats.images_new,
             "images_stored": stats.images_stored, "errors": len(stats.errors),
             "blocked_by_robots": stats.blocked_by_robots, "eta_seconds": eta},
        )


def _check_message(cancelled: bool, asked: int, stats: CrawlStats) -> str:
    """What a page check says: the pages that were read, then what kept the others from being read (told, never hidden)."""
    if cancelled:
        return "Vérification arrêtée. Les pages déjà relues sont à jour."
    read = stats.pages_visited - stats.pages_failed - stats.pages_redirected - stats.pages_walled
    left_out = [f"{count} {text}" for count, text in ((stats.pages_failed, "en échec"), (stats.pages_redirected, "redirigée(s) ailleurs"),
                                                       (stats.pages_walled, "cachée(s) par un bandeau de consentement"),
                                                       (stats.blocked_by_robots, "bloquée(s) par robots.txt")) if count]
    return f"{read} page(s) relue(s) sur {asked}" + (f" ; {', '.join(left_out)}" if left_out else "") + "."


# An index job reads its candidates `INDEX_CHUNK_BATCHES` embedding batches at a time (keyset on `id`), so only one
# chunk of light rows is ever in memory, however many images the brand has. A row that can never be indexed
# (unreadable image) stays pending: it is passed once, never fetched again, so the job always ends.
INDEX_CHUNK_BATCHES = 32
_FIRST_ID = uuid.UUID(int=0)  # sorts before every id

_REFERENCES_PENDING = """FROM reference_images
    WHERE brand_id = %s AND (embedding IS NULL OR phash_flip IS NULL OR thumb_path IS NULL OR work_path IS NULL)"""
_SITES_PENDING = """FROM site_images si JOIN sites s ON s.id = si.site_id
    WHERE s.brand_id = %s AND si.storage_path IS NOT NULL AND (si.embedding IS NULL OR si.thumb_path IS NULL)"""
_REFERENCE_CHUNK = f"""SELECT id, filename, storage_path, thumb_path, work_path, phash, phash_flip IS NULL AS needs_flip,
        embedding IS NULL AS needs_embedding
    {_REFERENCES_PENDING} AND id > %s ORDER BY id LIMIT %s"""
_SITE_CHUNK = f"""SELECT si.id, si.storage_path, si.content_hash, si.phash, si.thumb_path,
        si.embedding IS NULL AS needs_embedding
    {_SITES_PENDING} AND si.id > %s ORDER BY si.id LIMIT %s"""
_PENDING_COUNT = f"""SELECT (SELECT COUNT(*) {_REFERENCES_PENDING}) + (SELECT COUNT(*) {_SITES_PENDING}) AS total,
        EXISTS (SELECT 1 {_REFERENCES_PENDING} AND embedding IS NULL)
        OR EXISTS (SELECT 1 {_SITES_PENDING} AND si.embedding IS NULL) AS needs_embedding"""
_UPDATE_REFERENCE = """UPDATE reference_images SET
        embedding = COALESCE(%s, embedding),
        phash_flip = COALESCE(%s, phash_flip),
        dhash_flip = COALESCE(%s, dhash_flip),
        thumb_path = %s,
        work_path = %s,
        compared_at = CASE WHEN %s OR %s THEN NULL ELSE compared_at END
    WHERE org_id = %s AND id = %s"""
_UPDATE_SITE = """UPDATE site_images SET embedding = COALESCE(%s, embedding), thumb_path = %s,
        compared_at = CASE WHEN %s THEN NULL ELSE compared_at END
    WHERE org_id = %s AND (id = %s OR (content_hash = %s AND %s::text IS NOT NULL))"""


def _one_per_hash(rows: list[dict]) -> list[dict]:
    """One row per (content_hash, file): `_UPDATE_SITE` gives the embedding and the thumbnail to every row of the
    hash, so a file is read and embedded once. Rows of one hash that live in different files stay apart, so an
    unreadable file cannot keep its twin from being indexed. `twins` counts the rows a kept row stands for."""
    heads: dict[tuple, dict] = {}
    out = []
    for row in rows:
        key = (row["content_hash"], row["storage_path"])
        head = heads.get(key)  # a NULL hash is never a key: those rows stay apart
        if head is None:
            head = {**row, "twins": 0}
            out.append(head)
            if row["content_hash"] is not None:
                heads[key] = head
        else:
            head["twins"] += 1
            head["needs_embedding"] = head["needs_embedding"] or row["needs_embedding"]
    return out


class _IndexRun:
    """One index job: the images still missing something, the timings, and the progress made."""

    def __init__(self, ctx: JobContext, database_url: str, storage: Any, org_id: uuid.UUID, brand_id: uuid.UUID,
                 config: Config, total: int, needs_model: bool):
        self.ctx = ctx
        self.database_url = database_url
        self.storage = storage
        self.org_id = org_id
        self.brand_id = brand_id
        self.config = config
        self.batch = max(1, config.match.embedding_batch_size)
        self.chunk = self.batch * INDEX_CHUNK_BATCHES
        self.total = total
        self.needs_model = needs_model
        self.done = 0
        self.updated = 0
        self.timing = {"embedded": 0, "embed_seconds": 0.0, "load_seconds": 0.0, "model_load_seconds": 0.0}
        self.started = time.perf_counter()

    def warm_model(self) -> None:
        # Say what's happening before the first batch: loading the model can take minutes the first time.
        if self.needs_model:
            self.ctx.report(
                f"Chargement du modèle d'analyse ({self.total} image(s) à indexer ; plus long la toute première fois)…",
                {"phase": "index", "done": 0, "total": self.total}, force=True)
            warm_clip(self.config.match)
            self.timing["model_load_seconds"] = time.perf_counter() - self.started

    def process_references(self) -> None:
        self._process(
            _REFERENCE_CHUNK, list,
            # The working copy is enough; the original only to make a missing working copy.
            lambda row: (cloud_storage.BUCKET_REFS, row["work_path"] or row["storage_path"], row["phash"]),
            self._prepare_reference, _UPDATE_REFERENCE)

    def process_sites(self) -> None:
        self._process(
            _SITE_CHUNK, _one_per_hash,
            lambda row: (cloud_storage.BUCKET_SITE_IMAGES, row["storage_path"], row["content_hash"] or row["phash"]),
            self._prepare_site, _UPDATE_SITE)

    def result(self) -> dict[str, Any]:
        return {"indexed": self.updated, "metrics": {
            **{key: round(value, 3) if isinstance(value, float) else value for key, value in self.timing.items()},
            "duration_seconds": round(time.perf_counter() - self.started, 3),
        }}

    def _process(self, select: str, group: Callable[[list[dict]], list[dict]], source: Callable[[dict], tuple],
                 prepare: Callable[..., tuple], update: str) -> None:
        """Chunk by chunk (keyset on `id`), then batch by batch: stop if asked, read, embed, save, report.
        Twins of a site image that fall in a later chunk are already filled by their head's UPDATE, so they are
        not fetched again (and not counted again)."""
        after = _FIRST_ID
        while True:  # `after` moves forward on every pass, so this ends once a chunk comes back short
            with cloud_db.connect(self.database_url) as conn:
                fetched = conn.execute(select, (self.brand_id, after, self.chunk)).fetchall()
            rows = group(fetched)
            for start in range(0, len(rows), self.batch):
                if self.ctx.should_stop():
                    return
                self._process_batch(rows[start : start + self.batch], source, prepare, update)
            if len(fetched) < self.chunk:
                return
            after = fetched[-1]["id"]

    def _process_batch(self, part: list[dict], source: Callable[[dict], tuple], prepare: Callable[..., tuple],
                       update: str) -> None:
        loaded = [(row, self._load(*source(row))) for row in part]
        loaded = [(row, img) for row, img in loaded if img is not None]
        embeddings = iter(self._embed([img for row, img in loaded if row["needs_embedding"]]))
        params = [prepare(row, img, next(embeddings) if row["needs_embedding"] else None) for row, img in loaded]
        if params:  # one transaction for the whole batch
            with cloud_db.connect(self.database_url) as conn, conn.cursor() as cur:
                cur.executemany(update, params)
        self.updated += sum(1 + row.get("twins", 0) for row, _ in loaded)
        self.done += sum(1 + row.get("twins", 0) for row in part)
        done = min(self.done, self.total)  # rows a crawl added during the job can push `done` past `total`
        self.ctx.report(f"Analyse des images · {done}/{self.total}", {"phase": "index", "done": done, "total": self.total})

    def _embed(self, images: list) -> list:
        started = time.perf_counter()
        vectors = compute_clip_embeddings(images, self.config.match)
        if images:
            self.timing["embed_seconds"] += time.perf_counter() - started
            self.timing["embedded"] += len(images)
        return vectors

    def _load(self, bucket: str, path: str, version: str):
        done = min(self.done, self.total)
        self.ctx.report(f"Lecture des images · {done}/{self.total}", {"phase": "index", "done": done, "total": self.total})
        started = time.perf_counter()
        try:
            data = cloud_storage.cached_download(self.storage, bucket, path, version=version or "")
            if bucket == cloud_storage.BUCKET_REFS:
                img, _ = fetch.decode_reference(data, self.config.crawl.max_image_pixels)
            else:
                img = fetch.decode(data, self.config.crawl.max_image_pixels)
        except Exception:  # noqa: BLE001 - a missing object must not stop the others
            log.warning("reading %s/%s failed; image skipped", bucket, path)
            return None
        finally:
            self.timing["load_seconds"] += time.perf_counter() - started
        return None if img is None else img.convert("RGB")

    def _prepare_reference(self, row: dict, img: Any, embedding: Any) -> tuple:
        """Make what is missing (flip hashes, thumbnail, working copy) and return the `_UPDATE_REFERENCE` values."""
        phash_flip = dhash_flip = None
        if row["needs_flip"]:
            phash_flip, dhash_flip = compute_flip_hashes(img)
        thumb_path = row["thumb_path"]
        if not thumb_path:
            thumb_path = cloud_storage.ref_thumb_path(self.org_id, self.brand_id, row["filename"])
            cloud_storage.upload(self.storage, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                                 content_type="image/jpeg")
        work_path = row["work_path"]
        if not work_path:
            work_path = cloud_storage.ref_work_path(self.org_id, self.brand_id, row["filename"])
            cloud_storage.upload(self.storage, cloud_storage.BUCKET_REFS, work_path, fetch.make_working_copy(img),
                                 content_type="image/jpeg")
        return (embedding, phash_flip, dhash_flip, thumb_path, work_path, embedding is not None,
                phash_flip is not None, self.org_id, row["id"])

    def _prepare_site(self, row: dict, img: Any, embedding: Any) -> tuple:
        """Make the thumbnail if missing and return the `_UPDATE_SITE` values."""
        thumb_path = row["thumb_path"]
        if not thumb_path:
            thumb_path = cloud_storage.site_thumb_path(self.org_id, row["content_hash"] or str(row["id"]))
            cloud_storage.upload(self.storage, cloud_storage.BUCKET_SITE_IMAGES, thumb_path,
                                 fetch.make_thumbnail(img), content_type="image/jpeg")
        return (embedding, thumb_path, embedding is not None, self.org_id, row["id"], row["content_hash"],
                row["content_hash"])


class Worker:
    def __init__(self, *, database_url: str, storage_client_factory: Callable[[], Any],
                 config_path: Optional[Path] = None, poll_seconds: float = 2.0, mail_settings: Any = None):
        self.database_url = database_url
        self.mail_settings = mail_settings  # the sender's settings (`CloudSettings`): without them no monthly e-mail is looked for
        self.storage_client_factory = storage_client_factory
        self.config_path = config_path
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    # --- loop -------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def _publish_metrics(self) -> None:
        """Every few seconds, for the back office: what this container uses (a thread, since a job holds the loop)."""
        sampler, instance = hostmetrics.Sampler(), socket.gethostname()
        while not self._stop.wait(hostmetrics.PUBLISH_SECONDS):
            try:
                with cloud_db.connect(self.database_url) as conn:
                    hostmetrics.publish(conn, instance, "worker", sampler.read())
            except Exception:  # noqa: BLE001 - the figures are a convenience; a missed one must not stop the worker
                log.exception("publishing server metrics failed")

    def _watchdog(self) -> None:
        """Exit when nothing touches the alive file any more (the main loop between jobs, or the heartbeat thread
        during one): `restart: unless-stopped` then restarts the worker without any component reading the Docker
        socket. A job stuck inside Chromium or torch keeps the file fresh: it is stopped by `JOB_LIMITS`."""
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                hung = stalled(ALIVE_FILE, WATCHDOG_SECONDS, time.time())
            except OSError:
                continue  # `touch_alive` already warns that the file cannot be written
            if hung:
                log.error("no sign of life for %d s: worker exiting so the container restarts", WATCHDOG_SECONDS)
                os._exit(70)

    def _enqueue_scheduled(self) -> None:
        """Queue the monthly reads that are due (not while a job runs: the worker does one at a time)."""
        if not cloud_schedule.scheduler_enabled():
            return
        try:
            with cloud_db.connect(self.database_url) as conn:
                queued = cloud_schedule.enqueue_due(conn, datetime.now(timezone.utc))
            if queued:
                log.info("monthly reads queued: %d", len(queued))
        except psycopg.errors.UndefinedTable:
            if cloud_schedule.once_per_hour("missing-table"):  # a warning an hour, not an event every 5 minutes
                log.warning("monthly reads are not queued: table brand_schedules is missing, apply migration 20261024000033")
        except Exception:  # noqa: BLE001 - housekeeping must not stop the worker loop; the brands stay due
            log.exception("queueing the monthly reads failed")

    def _send_digests(self) -> None:
        """Send the monthly e-mails that are due, right after the reads are queued (same switch, same protections)."""
        if self.mail_settings is None or not cloud_schedule.scheduler_enabled():
            return
        try:
            sent = cloud_digest.run(self.mail_settings, datetime.now(timezone.utc))
            if sent:
                log.info("monthly e-mails sent: %d", sent)
        except psycopg.errors.UndefinedTable:
            if cloud_schedule.once_per_hour("missing-digest-table"):  # a warning an hour, not an event every 5 minutes
                log.warning("monthly e-mails are not sent: table monthly_digests or brand_schedules is missing, apply migrations "
                            "20261024000033 and 20261025000034")
        except Exception:  # noqa: BLE001 - housekeeping must not stop the worker loop; the mails stay due
            log.exception("sending the monthly e-mails failed")

    def run_forever(self) -> None:
        log.info("worker started")
        touch_alive()
        log.info("%d leftover(s) removed from the temp directory", clean_temp(time.time()))
        try:
            cloud_storage.trim_cache()
        except Exception:  # noqa: BLE001 - tidying the cache is best effort; a bad cache directory must not crash-loop the worker
            log.exception("trimming the image cache failed")
        threading.Thread(target=self._publish_metrics, name="metrics", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
        last_reap, last_purge, last_schedule = 0.0, -AUDIT_PURGE_SECONDS, -SCHEDULE_SECONDS
        while not self._stop.is_set():
            touch_alive()
            if time.monotonic() - last_purge > AUDIT_PURGE_SECONDS:
                last_purge = time.monotonic()
                try:
                    purged = cloud_audit.purge(self.database_url, between=touch_alive)
                    if purged:
                        log.info("audit log: %d row(s) older than %d months deleted", purged, cloud_audit.RETENTION_MONTHS)
                except Exception:  # noqa: BLE001 - retention is housekeeping; tomorrow's run takes what this one missed
                    log.exception("purging the audit log failed")
            if time.monotonic() - last_reap > 60:
                last_reap = time.monotonic()
                try:
                    with cloud_db.connect(self.database_url) as conn:
                        requeued, failed = cloud_jobs.reap_stale(conn)
                    if requeued or failed:
                        log.warning("stale jobs: %d put back in the queue, %d marked as failed", requeued, failed)
                except Exception:  # noqa: BLE001 - housekeeping must not stop the worker loop
                    log.exception("reaping failed")
            if time.monotonic() - last_schedule >= SCHEDULE_SECONDS:
                last_schedule = time.monotonic()
                self._enqueue_scheduled()
                self._send_digests()
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("worker loop error")
                ran = False
            if not ran:
                self._stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        with cloud_db.connect(self.database_url) as conn:
            job = cloud_jobs.claim(conn)
        if job is None:
            return False
        self.run_job(job)
        return True

    def run_job(self, job: dict) -> None:
        with observability.job_scope(org_id=str(job["org_id"]), job_id=str(job["id"]), kind=job["kind"]):
            self._run_job(job)

    def _run_job(self, job: dict) -> None:
        ctx = JobContext(self.database_url, job)
        log.info("job %s (%s) for org %s, brand %s started", job["id"], job["kind"], job["org_id"], job["brand_id"])
        handler = {
            "crawl": self._crawl,
            "match": self._match_job,
            "index": self._index,
            "report": self._report,
            "locate": self._locate,
            "check": self._check,
        }[job["kind"]]
        try:
            try:
                message, result = handler(ctx, uuid.UUID(job["org_id"]), uuid.UUID(job["brand_id"]), job.get("params") or {})
            finally:
                ctx.close()  # the heartbeat stops before the outcome is written: it must not time out a job that just ended
            status = "cancelled" if ctx.cancelled else "done"
            with cloud_db.connect(self.database_url) as conn:
                cloud_jobs.finish(conn, job["id"], status=status, message=message, result=result)
        except MatchStopped:
            with cloud_db.connect(self.database_url) as conn:
                cloud_jobs.finish(conn, job["id"], status="cancelled",
                                  message="Arrêté. Les résultats précédents sont conservés.", result={})
        except JobFailed as exc:
            with cloud_db.connect(self.database_url) as conn:
                cloud_jobs.finish(conn, job["id"], status="error", message=str(exc), error=str(exc))
        except Exception as exc:  # noqa: BLE001 - any job bug is logged, reported and recorded as the job's error
            log.error("job %s failed:\n%s", job["id"], traceback.format_exc())
            _capture(exc)
            with cloud_db.connect(self.database_url) as conn:
                cloud_jobs.finish(conn, job["id"], status="error", message=resource_problem(exc) or "La tâche a échoué.",
                                  error=f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            clean_temp(time.time())
            log.info("job %s finished", job["id"])

    # --- helpers --------------------------------------------------------------

    def config_for(self, org_id: uuid.UUID) -> Config:
        with cloud_db.connect(self.database_url) as conn:
            overrides = cloud_db.get_overrides(conn, org_id)
        return with_overrides(load_config(self.config_path), overrides)

    def _compare(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, config: Config,
                 metrics: Optional[dict] = None) -> int:
        """Run a comparison pass. `metrics`, if given, receives what the pass did and how long it took."""
        def progress(done: int, total: int) -> None:
            ctx.report(f"Comparaison {done}/{total}", {"phase": "match", "done": done, "total": total})

        def verifying(done: int, total: int) -> None:
            ctx.report(f"Vérification des ressemblances · {done}/{total}", {"phase": "verify", "done": done, "total": total})

        def grouping(done: int, total: int) -> None:
            ctx.report(f"Regroupement des recadrages · {done}/{total}", {"phase": "variants", "done": done, "total": total})

        ctx.report("Comparaison avec la bibliothèque…", {"phase": "match"}, force=True)
        storage = self.storage_client_factory()
        store = CloudMatchStore(org_id=org_id, brand_id=brand_id, database_url=self.database_url,
                                storage_client=storage, max_image_pixels=config.crawl.max_image_pixels)
        info: dict = {}
        started = time.perf_counter()
        count = run_matching(store, config, use_clip=True, progress=progress, should_stop=ctx.should_stop,
                             verify_progress=verifying, stats=info)
        ctx.report("Regroupement des recadrages…", {"phase": "variants"}, force=True)
        try:
            info.update(update_variants(self.database_url, storage, org_id, brand_id, config, progress=grouping,
                                        should_stop=ctx.should_stop))
        except MatchStopped:
            raise
        except Exception as exc:  # noqa: BLE001 - grouping is a derived step: the matches are saved, the job stays done
            log.exception("grouping the crops of brand %s failed", brand_id)
            _capture(exc)
            info["variant_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        info["seconds"] = round(time.perf_counter() - started, 3)
        info["matches_total"] = count
        if metrics is not None:
            metrics.update(info)
        return count

    def _sites_to_crawl(self, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> list[dict]:
        with cloud_db.connect(self.database_url) as conn:
            if params.get("site") and not params.get("site_ids"):
                # Queued before brands existed: a bare address, filed under the job's brand.
                site_id = cloud_db.create_site(conn, org_id=org_id, brand_id=brand_id,
                                               url=normalize_url(str(params["site"])))
                return cloud_db.get_sites(conn, brand_id, [site_id])
            ids = [uuid.UUID(str(value)) for value in params.get("site_ids") or []]
            if not ids:
                ids = [row["id"] for row in cloud_db.list_sites(conn, brand_id)]
            return cloud_db.get_sites(conn, brand_id, ids)

    # --- handlers ---------------------------------------------------------------

    def _crawl(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        config = self.config_for(org_id)
        sites = self._sites_to_crawl(org_id, brand_id, params)
        if not sites:
            raise JobFailed("Aucune adresse à lire pour cette marque.")
        limit = min(int(params.get("max_pages") or config.crawl.max_pages), config.crawl.max_pages_limit)
        runs = []
        capped = False  # the month's page limit stopped the read before its own limit did
        for index, site in enumerate(sites):
            if ctx.cancelled:
                break
            left = self._pages_left(org_id, config)
            # ponytail: jobs of several brands of one organization run side by side and each takes `min(limit, left)`
            # without a lock, so the month can be passed by (concurrent jobs - 1) x crawl.max_pages_limit pages.
            if left <= 0:
                capped = True
                break
            prefix = f"{site['label'] or site['url']} ({index + 1}/{len(sites)}) · " if len(sites) > 1 else ""
            runs.append(self._crawl_site(ctx, org_id, site, config, min(limit, left), params, prefix))
            # stopped by the quota, not by the end of the site: it used what was left and pages were still waiting
            capped = capped or (left < limit and runs[-1]["pages_visited"] >= left and runs[-1]["pages_queued"] > 0)

        result = {
            "runs": [run["run_id"] for run in runs], "quota_reached": capped,
            **{key: sum(run[key] for run in runs)
               for key in ("pages_visited", "images_found", "images_stored", "images_new", "blocked_by_robots")},
            "errors": [error for run in runs for error in run["errors"]][:8],
        }
        if ctx.cancelled:
            return "Lecture arrêtée. Les pages déjà lues sont conservées.", result
        where = f" sur {len(runs)} sites" if len(runs) > 1 else ""
        message = f"{result['pages_visited']} page(s) lue(s){where}, {result['images_new']} nouvelle(s) image(s)."
        for run in runs:
            if run["suspect"]:
                message += f" La lecture de {run['site']} semble incomplète ({run['suspect']}) : rien n'est marqué retiré."
        message += f" {cloud_usage.CAPPED}" if capped else self._usage_note(org_id, config)
        if params.get("then_match", True):
            result["match_metrics"] = {}
            result["matches"] = self._compare(ctx, org_id, brand_id, config, result["match_metrics"])
            message += f" {result['matches']} correspondance(s) au total."
        return message, result

    def _pages_left(self, org_id: uuid.UUID, config: Config) -> int:
        """Pages this organization may still read this month; 0 when its storage is full (asked again before each site)."""
        with cloud_db.connect(self.database_url) as conn:
            if cloud_usage.check(conn, config.quota, org_id, "storage"):
                return 0
            used, limit = cloud_usage.pages_used(conn, config.quota, org_id)
        return limit - used

    def _usage_note(self, org_id: uuid.UUID, config: Config) -> str:
        """Once the month passes 80 % of its pages, what a finished read tells the person (nothing before)."""
        with cloud_db.connect(self.database_url) as conn:
            return cloud_usage.pages_note(*cloud_usage.pages_used(conn, config.quota, org_id))

    def _crawl_site(self, ctx: JobContext, org_id: uuid.UUID, site: dict, config: Config, limit: int,
                    params: dict, prefix: str) -> dict:
        storage = self.storage_client_factory()
        created_by = ctx.job.get("created_by")
        with cloud_db.connect(self.database_url) as conn:
            run_id = cloud_db.start_crawl_run(conn, org_id=org_id, site_id=site["id"],
                                              triggered_by=uuid.UUID(created_by) if created_by else None,
                                              job_id=uuid.UUID(ctx.job["id"]))
        store = CloudCrawlStore(org_id=org_id, site_id=site["id"], database_url=self.database_url,
                                storage_client=storage)
        last_run_update = 0.0
        reporter = _CrawlProgress(ctx, prefix, limit)
        meter = cloud_usage.Meter(self.database_url, org_id)  # a crash loses at most the last few seconds of the count

        def progress(stats: CrawlStats) -> None:
            nonlocal last_run_update
            reporter.report(stats)
            if time.monotonic() - last_run_update > 5:
                last_run_update = time.monotonic()
                with cloud_db.connect(self.database_url) as conn:
                    cloud_db.update_crawl_run_progress(conn, run_id, stats)
                meter.flush(stats)

        ctx.report(f"{prefix}Lecture du sitemap…", {"phase": "crawl", "done": 0, "total": 0}, force=True)
        try:
            # Loaded up front so the first batch's embedding time isn't mostly model loading.
            load_started = time.perf_counter()
            warm_clip(config.match)
            model_load_seconds = time.perf_counter() - load_started
            stats = crawl_site(
                site["url"], store, config, max_pages=limit,
                embedder=lambda images: compute_clip_embeddings(images, config.match),
                resume=not params.get("fresh", False), progress=progress, should_stop=ctx.should_stop,
            )
        except netguard.BlockedURL as exc:
            with cloud_db.connect(self.database_url) as conn:
                cloud_db.finish_crawl_run(conn, run_id, status="error", errors=[str(exc)])
            raise JobFailed(f"Adresse refusée : {exc}.") from exc
        except Exception as exc:
            problem = browser_problem(exc)
            with cloud_db.connect(self.database_url) as conn:
                cloud_db.finish_crawl_run(conn, run_id, status="error",
                                          errors=[problem or traceback.format_exc(limit=2)])
            if problem:
                raise JobFailed(problem) from exc
            raise

        stats.model_load_seconds = model_load_seconds
        meter.flush(stats)
        with cloud_db.connect(self.database_url) as conn:
            cloud_db.update_crawl_run_progress(conn, run_id, stats)
            suspect = None if ctx.cancelled else cloud_db.record_read_health(conn, run_id, stats)
            cloud_db.finish_crawl_run(conn, run_id, status="cancelled" if ctx.cancelled else "done", errors=stats.errors)
        return {"suspect": suspect, "site": site["label"] or site["url"],
            "run_id": str(run_id), "pages_visited": stats.pages_visited, "pages_queued": stats.pages_queued, "images_found": stats.images_found,
            "images_stored": stats.images_stored, "images_new": stats.images_new,
            "blocked_by_robots": stats.blocked_by_robots, "errors": stats.errors[:8],
        }

    def _check(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        """Read again only the pages given, `{site id: [addresses]}`: no sitemap, no link followed, no comparison. Each page
        stamps what it shows, so the page of a visual says at once whether it is still there. No crawl run is recorded: it
        would stand for a read of the whole site."""
        config = self.config_for(org_id)
        groups = {key: list(urls) for key, urls in (params.get("pages") or {}).items() if urls}
        with cloud_db.connect(self.database_url) as conn:
            sites = {str(row["id"]): row for row in cloud_db.get_sites(conn, brand_id, [uuid.UUID(key) for key in groups])}
        total = sum(len(urls) for urls in groups.values())
        if not total or not sites:
            raise JobFailed("Aucune page à vérifier pour cette marque.")
        left = self._pages_left(org_id, config)
        if left <= 0:
            raise JobFailed(cloud_usage.CAPPED)
        stats = CrawlStats()
        capped = False
        for key, urls in groups.items():
            if ctx.cancelled or key not in sites:
                continue
            left = self._pages_left(org_id, config)  # again: the pages and the storage used by the groups before
            capped = capped or len(urls) > left
            urls = urls[:left]  # the month's page limit: what does not fit is not read
            if not urls:
                continue
            site = sites[key]
            store = CloudCrawlStore(org_id=org_id, site_id=site["id"], database_url=self.database_url,
                                    storage_client=self.storage_client_factory())
            before = stats.pages_visited

            def progress(current: CrawlStats, before: int = before) -> None:
                done = before + current.pages_visited
                ctx.report(f"Page {done}/{total}", {"phase": "check", "done": done, "total": total})

            try:
                read = crawl_pages(site["url"], urls, store, config, progress=progress, should_stop=ctx.should_stop,
                                   embedder=lambda images: compute_clip_embeddings(images, config.match))
            except netguard.BlockedURL as exc:
                raise JobFailed(f"Adresse refusée : {exc}.") from exc
            except Exception as exc:
                problem = browser_problem(exc)
                if problem:
                    raise JobFailed(problem) from exc
                raise
            for name in ("pages_visited", "pages_failed", "pages_redirected", "pages_walled", "blocked_by_robots"):
                setattr(stats, name, getattr(stats, name) + getattr(read, name))
            stats.errors += read.errors
            cloud_usage.Meter(self.database_url, org_id).flush(read)
        message = _check_message(ctx.cancelled, total, stats)
        return (f"{message} {cloud_usage.CAPPED}" if capped and not ctx.cancelled else message), {
            "pages_read": stats.pages_visited - stats.pages_failed - stats.pages_redirected - stats.pages_walled, "pages_asked": total,
            "pages_failed": stats.pages_failed, "pages_redirected": stats.pages_redirected,
            "blocked_by_robots": stats.blocked_by_robots, "errors": stats.errors[:8]}

    def _match_job(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        metrics: dict = {}
        count = self._compare(ctx, org_id, brand_id, self.config_for(org_id), metrics)
        return f"Comparaison terminée : {count} correspondance(s).", {"matches": count, "match_metrics": metrics}

    def _locate(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        try:
            reference_id = uuid.UUID(str(params.get("reference_id")))
        except ValueError as exc:
            raise JobFailed("Référence manquante.") from exc

        def progress(done: int, total: int) -> None:
            ctx.report(f"Recherche dans les images lues · {done}/{total}", {"phase": "locate", "done": done, "total": total})

        ctx.report("Recherche de la référence…", {"phase": "locate"}, force=True)
        try:
            result = locate_reference(self.database_url, self.storage_client_factory(), org_id, brand_id, reference_id,
                                      self.config_for(org_id), progress=progress, should_stop=ctx.should_stop)
        except NotSearchable as exc:
            raise JobFailed(str(exc)) from exc
        return f"{result['found']} image(s) trouvée(s) sur {result['searched']} lue(s).", result

    def _index(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        config = self.config_for(org_id)
        storage = self.storage_client_factory()
        total, needs_model = self._index_pending(brand_id)
        run = _IndexRun(ctx, self.database_url, storage, org_id, brand_id, config, total, needs_model)
        run.warm_model()
        run.process_references()
        run.process_sites()
        result = run.result()
        if ctx.cancelled:
            return "Indexation arrêtée.", result
        if not self._has_site_images(brand_id):
            return f"{run.updated} image(s) indexée(s).", result
        result["match_metrics"] = {}
        result["matches"] = self._compare(ctx, org_id, brand_id, config, result["match_metrics"])
        return f"{run.updated} image(s) indexée(s), comparaison à jour.", result

    def _index_pending(self, brand_id: uuid.UUID) -> tuple[int, bool]:
        """How many reference and site images are still missing an embedding, hashes or a thumbnail, and whether
        any of them needs the model. Counted, not loaded: the rows are read chunk by chunk by `_IndexRun`."""
        with cloud_db.connect(self.database_url) as conn:
            row = conn.execute(_PENDING_COUNT, (brand_id,) * 4).fetchone()
        return row["total"], row["needs_embedding"]

    def _has_site_images(self, brand_id: uuid.UUID) -> bool:
        with cloud_db.connect(self.database_url) as conn:
            return conn.execute(
                """SELECT EXISTS (SELECT 1 FROM site_images si JOIN sites s ON s.id = si.site_id
                                  WHERE s.brand_id = %s) AS e""",
                (brand_id,),
            ).fetchone()["e"]

    def _report(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        config = self.config_for(org_id)
        storage = self.storage_client_factory()
        within_days = params.get("within_days")
        within_days = config.report.default_within_days if within_days is None else int(within_days)
        ctx.report("Préparation du rapport…", {"phase": "report"}, force=True)
        with cloud_db.connect(self.database_url) as conn:
            org = cloud_db.get_organization(conn, org_id)
            brand = cloud_db.get_brand(conn, org_id, brand_id)
            matches = cloud_db.match_rows(conn, brand_id)
            unmatched = cloud_db.unmatched_rows(conn, brand_id)
            stats = asdict(cloud_db.get_stats(conn, brand_id))
            provenance = cloud_db.report_provenance(conn, brand_id)
            replaced = cloud_presence.replaced_references(conn, brand_id)
        for row in matches:
            row["ref_thumb"] = (cloud_storage.BUCKET_REFS, row["ref_thumb_path"] or row["ref_storage_path"])
            row["site_thumb"] = (cloud_storage.BUCKET_SITE_IMAGES, row["site_thumb_path"] or row["site_storage_path"])
        for row in unmatched:
            row["ref_thumb"] = (cloud_storage.BUCKET_REFS, row["ref_thumb_path"] or row["ref_storage_path"])

        def loader(key):
            bucket, path = key
            return cloud_storage.download(storage, bucket, path) if path else None

        files = report_module.build_report(
            matches, unmatched, within_days=within_days, stats=stats, thumb_loader=loader,
            organization=report_title(org, brand), provenance=provenance, replaced=replaced,
        )
        report_id = uuid.uuid4()
        base = cloud_storage.path_for(org_id, str(report_id))
        paths = {
            "html": f"{base}/report.html", "csv": f"{base}/matches.csv", "not_found": f"{base}/not_found.csv",
        }
        cloud_storage.upload(storage, cloud_storage.BUCKET_REPORTS, paths["html"], files.html,
                             content_type="text/html; charset=utf-8")
        cloud_storage.upload(storage, cloud_storage.BUCKET_REPORTS, paths["csv"], files.matches_csv,
                             content_type="text/csv; charset=utf-8")
        cloud_storage.upload(storage, cloud_storage.BUCKET_REPORTS, paths["not_found"], files.not_found_csv,
                             content_type="text/csv; charset=utf-8")
        created_by = ctx.job.get("created_by")
        with cloud_db.connect(self.database_url) as conn:
            saved_id = cloud_db.create_report(
                conn, org_id=org_id, brand_id=brand_id, within_days=within_days, storage_path_html=paths["html"],
                storage_path_csv=paths["csv"], storage_path_not_found_csv=paths["not_found"],
                stats={**stats, **{k: v for k, v in files.summary.items() if k != "upcoming"}},
                generated_by=uuid.UUID(created_by) if created_by else None,
            )
        return "Rapport prêt.", {"report_id": str(saved_id)}


def browser_problem(exc: Exception) -> Optional[str]:
    """The worker's Chromium is missing or won't start: say how to fix it instead of a traceback."""
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return ("Le navigateur de lecture (Chromium) n'est pas installé pour ce worker. Sur la machine du worker, lancez "
                "« python -m playwright install chromium », dans le même terminal que « nyra worker » "
                "(PLAYWRIGHT_BROWSERS_PATH doit pointer au même endroit), puis relancez la lecture.")
    if type(exc).__module__.startswith("playwright") and "launch" in text:
        return f"Le navigateur de lecture (Chromium) n'a pas pu démarrer sur le worker : {text.splitlines()[0][:200]}"
    return None


def resource_problem(exc: BaseException) -> Optional[str]:
    """A job that ran out of memory or disk: say so, and what to do, instead of "La tâche a échoué."."""
    if isinstance(exc, MemoryError):
        return "La mémoire du serveur n'a pas suffi : relancez la tâche, ou réduisez le nombre de pages ou d'images traitées."
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Le disque du serveur est plein : prévenez l'équipe Nyra, puis relancez la tâche."
    return None


def report_title(org: Optional[dict], brand: Optional[dict]) -> str:
    """"Rémy Martin", or "Rémy Martin · Louis XIII" once the brand isn't just the organization."""
    names = [item["name"] for item in (org, brand) if item]
    if len(names) == 2 and names[0].casefold() == names[1].casefold():
        names = names[:1]
    return " · ".join(names)


def _capture(exc: BaseException) -> None:
    """Report a job failure to Sentry when it is enabled."""
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.capture_exception(exc)


def supervise(command: list[str], stop: threading.Event, restart_seconds: float = 5.0) -> None:
    """Keep `command` running (a `nyra worker` next to the web process, in development): started again when it exits,
    until `stop` is set, which also ends it. Meant for a thread."""
    while not stop.is_set():
        process = subprocess.Popen(command)
        while process.poll() is None and not stop.wait(1.0):
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            return
        log.warning("worker exited with code %s; starting it again in %.0f s", process.returncode, restart_seconds)
        stop.wait(restart_seconds)


def configure_logging() -> None:
    observability.configure_logging("worker")
    observability.init_sentry("worker")
