"""`nyra worker`: runs the jobs the web process enqueues.

Runs in its own process (and container): it's the only part of the
product that needs Chromium and torch. Several workers can run side by
side; `jobs.claim` hands each one a different job and never two jobs of
the same brand at once. Every job works on one brand: its library, its
sites.

Job kinds:
- crawl  — read one or several of the brand's sites, then compare (unless
           asked not to);
- match  — compare the brand's library with every image read on its sites;
- index  — fill in whatever a reference or site image is missing
           (CLIP embedding, mirror hashes, thumbnail), then compare. The
           web process enqueues one after every upload; it also
           backfills rows created before those columns existed;
- report — build report.html + the two CSVs and store them.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional

from nyra import fetch, netguard, observability
from nyra import report as report_module
from nyra.config import Config, load_config, with_overrides
from nyra.crawl import CrawlStats, crawl_site, normalize_url
from nyra.estimate import remaining_seconds
from nyra.match import MatchStopped, compute_clip_embeddings, compute_flip_hashes, run_matching, warm_clip

from . import db as cloud_db
from . import jobs as cloud_jobs
from . import storage as cloud_storage
from .store import CloudCrawlStore, CloudMatchStore

log = logging.getLogger("nyra.worker")

HEARTBEAT_SECONDS = 30
PROGRESS_SECONDS = 1.0


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
        self._beat = threading.Thread(target=self._beat_loop, name=f"heartbeat-{job['id']}", daemon=True)
        self._beat.start()

    def _beat_loop(self) -> None:
        while not self._done.wait(HEARTBEAT_SECONDS):
            self._heartbeat()

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
                 config_path: Optional[Path] = None, poll_seconds: float = 2.0):
        self.database_url = database_url
        self.storage_client_factory = storage_client_factory
        self.config_path = config_path
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    # --- loop -------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        log.info("worker started")
        last_reap = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_reap > 60:
                last_reap = time.monotonic()
                try:
                    with cloud_db.connect(self.database_url) as conn:
                        reaped = cloud_jobs.reap_stale(conn)
                    if reaped:
                        log.warning("marked %d stale job(s) as failed", reaped)
                except Exception:  # noqa: BLE001 - housekeeping must not stop the worker loop
                    log.exception("reaping failed")
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
        }[job["kind"]]
        try:
            message, result = handler(ctx, uuid.UUID(job["org_id"]), uuid.UUID(job["brand_id"]), job.get("params") or {})
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
                cloud_jobs.finish(conn, job["id"], status="error", message="La tâche a échoué.",
                                  error=f"{type(exc).__name__}: {str(exc)[:500]}")
        finally:
            ctx.close()
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

        ctx.report("Comparaison avec la bibliothèque…", {"phase": "match"}, force=True)
        store = CloudMatchStore(org_id=org_id, brand_id=brand_id, database_url=self.database_url,
                                storage_client=self.storage_client_factory(),
                                max_image_pixels=config.crawl.max_image_pixels)
        info: dict = {}
        started = time.perf_counter()
        count = run_matching(store, config, use_clip=True, progress=progress, should_stop=ctx.should_stop,
                             verify_progress=verifying, stats=info)
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
        for index, site in enumerate(sites):
            if ctx.cancelled:
                break
            prefix = f"{site['label'] or site['url']} ({index + 1}/{len(sites)}) · " if len(sites) > 1 else ""
            runs.append(self._crawl_site(ctx, org_id, site, config, limit, params, prefix))

        result = {
            "runs": [run["run_id"] for run in runs],
            **{key: sum(run[key] for run in runs)
               for key in ("pages_visited", "images_found", "images_stored", "images_new", "blocked_by_robots")},
            "errors": [error for run in runs for error in run["errors"]][:8],
        }
        if ctx.cancelled:
            return "Lecture arrêtée. Les pages déjà lues sont conservées.", result
        where = f" sur {len(runs)} sites" if len(runs) > 1 else ""
        message = f"{result['pages_visited']} page(s) lue(s){where}, {result['images_new']} nouvelle(s) image(s)."
        if params.get("then_match", True):
            result["match_metrics"] = {}
            result["matches"] = self._compare(ctx, org_id, brand_id, config, result["match_metrics"])
            message += f" {result['matches']} correspondance(s) au total."
        return message, result

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

        def progress(stats: CrawlStats) -> None:
            nonlocal last_run_update
            reporter.report(stats)
            if time.monotonic() - last_run_update > 5:
                last_run_update = time.monotonic()
                with cloud_db.connect(self.database_url) as conn:
                    cloud_db.update_crawl_run_progress(conn, run_id, stats)

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
        with cloud_db.connect(self.database_url) as conn:
            cloud_db.update_crawl_run_progress(conn, run_id, stats)
            cloud_db.finish_crawl_run(conn, run_id, status="cancelled" if ctx.cancelled else "done", errors=stats.errors)
        return {
            "run_id": str(run_id), "pages_visited": stats.pages_visited, "images_found": stats.images_found,
            "images_stored": stats.images_stored, "images_new": stats.images_new,
            "blocked_by_robots": stats.blocked_by_robots, "errors": stats.errors[:8],
        }

    def _match_job(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        metrics: dict = {}
        count = self._compare(ctx, org_id, brand_id, self.config_for(org_id), metrics)
        return f"Comparaison terminée : {count} correspondance(s).", {"matches": count, "match_metrics": metrics}

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
            organization=report_title(org, brand),
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


def configure_logging() -> None:
    observability.configure_logging("worker")
    observability.init_sentry("worker")
