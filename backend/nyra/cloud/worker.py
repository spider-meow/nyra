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
import os
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional

from nyra import fetch, netguard
from nyra import report as report_module
from nyra.config import Config, load_config, with_overrides
from nyra.crawl import CrawlStats, crawl_site, normalize_url
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
                except Exception:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            log.error("job %s failed:\n%s", job["id"], traceback.format_exc())
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

    def _compare(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, config: Config) -> int:
        def progress(done: int, total: int) -> None:
            ctx.report(f"Comparaison {done}/{total}", {"phase": "match", "done": done, "total": total})

        def verifying(done: int, total: int) -> None:
            ctx.report(f"Vérification des ressemblances · {done}/{total}", {"phase": "verify", "done": done, "total": total})

        ctx.report("Comparaison avec la bibliothèque…", {"phase": "match"}, force=True)
        store = CloudMatchStore(org_id=org_id, brand_id=brand_id, database_url=self.database_url,
                                storage_client=self.storage_client_factory(),
                                max_image_pixels=config.crawl.max_image_pixels)
        return run_matching(store, config, use_clip=True, progress=progress, should_stop=ctx.should_stop,
                            verify_progress=verifying)

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
            result["matches"] = self._compare(ctx, org_id, brand_id, config)
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

        def progress(stats: CrawlStats) -> None:
            nonlocal last_run_update
            ctx.report(
                f"{prefix}Page {stats.pages_visited}/{limit} · {stats.images_new} nouvelle(s) image(s)",
                {"phase": "crawl", "done": stats.pages_visited, "total": limit, "images_new": stats.images_new,
                 "images_stored": stats.images_stored, "errors": len(stats.errors),
                 "blocked_by_robots": stats.blocked_by_robots},
            )
            if time.monotonic() - last_run_update > 5:
                last_run_update = time.monotonic()
                with cloud_db.connect(self.database_url) as conn:
                    cloud_db.update_crawl_run_progress(conn, run_id, stats)

        ctx.report(f"{prefix}Lecture du sitemap…", {"phase": "crawl", "done": 0, "total": limit}, force=True)
        try:
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

        with cloud_db.connect(self.database_url) as conn:
            cloud_db.update_crawl_run_progress(conn, run_id, stats)
            cloud_db.finish_crawl_run(conn, run_id, status="cancelled" if ctx.cancelled else "done", errors=stats.errors)
        return {
            "run_id": str(run_id), "pages_visited": stats.pages_visited, "images_found": stats.images_found,
            "images_stored": stats.images_stored, "images_new": stats.images_new,
            "blocked_by_robots": stats.blocked_by_robots, "errors": stats.errors[:8],
        }

    def _match_job(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        count = self._compare(ctx, org_id, brand_id, self.config_for(org_id))
        return f"Comparaison terminée : {count} correspondance(s).", {"matches": count}

    def _index(self, ctx: JobContext, org_id: uuid.UUID, brand_id: uuid.UUID, params: dict) -> tuple[str, dict]:
        config = self.config_for(org_id)
        storage = self.storage_client_factory()
        batch = max(1, config.match.embedding_batch_size)
        with cloud_db.connect(self.database_url) as conn:
            refs = conn.execute(
                """SELECT id, filename, storage_path, thumb_path, work_path, phash, phash_flip IS NULL AS needs_flip,
                          embedding IS NULL AS needs_embedding
                   FROM reference_images
                   WHERE brand_id = %s
                     AND (embedding IS NULL OR phash_flip IS NULL OR thumb_path IS NULL OR work_path IS NULL)""",
                (brand_id,),
            ).fetchall()
            sites = conn.execute(
                """SELECT si.id, si.storage_path, si.content_hash, si.phash, si.thumb_path,
                          si.embedding IS NULL AS needs_embedding
                   FROM site_images si JOIN sites s ON s.id = si.site_id
                   WHERE s.brand_id = %s AND si.storage_path IS NOT NULL
                     AND (si.embedding IS NULL OR si.thumb_path IS NULL)""",
                (brand_id,),
            ).fetchall()
        total = len(refs) + len(sites)
        done = 0
        updated = 0
        # Say what's happening before the first batch: loading the model can take minutes the first time.
        if any(row["needs_embedding"] for row in (*refs, *sites)):
            ctx.report(f"Chargement du modèle d'analyse ({total} image(s) à indexer ; plus long la toute première fois)…",
                       {"phase": "index", "done": 0, "total": total}, force=True)
            warm_clip(config.match)

        def load(bucket: str, path: str, version: str):
            ctx.report(f"Lecture des images · {done}/{total}", {"phase": "index", "done": done, "total": total})
            try:
                data = cloud_storage.cached_download(storage, bucket, path, version=version or "")
                if bucket == cloud_storage.BUCKET_REFS:
                    img, _ = fetch.decode_reference(data, config.crawl.max_image_pixels)
                else:
                    img = fetch.decode(data, config.crawl.max_image_pixels)
            except Exception:  # noqa: BLE001 - a missing object must not stop the others
                return None
            return None if img is None else img.convert("RGB")

        for start in range(0, len(refs), batch):
            if ctx.should_stop():
                break
            # The working copy is enough; the original only to make a missing working copy.
            chunk = [(row, load(cloud_storage.BUCKET_REFS, row["work_path"] or row["storage_path"], row["phash"]))
                     for row in refs[start : start + batch]]
            chunk = [(row, img) for row, img in chunk if img is not None]
            to_embed = [img for row, img in chunk if row["needs_embedding"]]
            embeddings = iter(compute_clip_embeddings(to_embed, config.match))
            for row, img in chunk:
                embedding = next(embeddings) if row["needs_embedding"] else None
                phash_flip = dhash_flip = None
                if row["needs_flip"]:
                    phash_flip, dhash_flip = compute_flip_hashes(img)
                thumb_path = row["thumb_path"]
                if not thumb_path:
                    thumb_path = cloud_storage.ref_thumb_path(org_id, brand_id, row["filename"])
                    cloud_storage.upload(storage, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                                         content_type="image/jpeg")
                work_path = row["work_path"]
                if not work_path:
                    work_path = cloud_storage.ref_work_path(org_id, brand_id, row["filename"])
                    cloud_storage.upload(storage, cloud_storage.BUCKET_REFS, work_path, fetch.make_working_copy(img),
                                         content_type="image/jpeg")
                with cloud_db.connect(self.database_url) as conn:
                    conn.execute(
                        """UPDATE reference_images SET
                               embedding = COALESCE(%s, embedding),
                               phash_flip = COALESCE(%s, phash_flip),
                               dhash_flip = COALESCE(%s, dhash_flip),
                               thumb_path = %s,
                               work_path = %s,
                               compared_at = CASE WHEN %s OR %s THEN NULL ELSE compared_at END
                           WHERE id = %s""",
                        (embedding, phash_flip, dhash_flip, thumb_path, work_path, embedding is not None,
                         phash_flip is not None, row["id"]),
                    )
                updated += 1
            done += len(refs[start : start + batch])
            ctx.report(f"Analyse des images · {done}/{total}", {"phase": "index", "done": done, "total": total})

        for start in range(0, len(sites), batch):
            if ctx.should_stop():
                break
            chunk = [(row, load(cloud_storage.BUCKET_SITE_IMAGES, row["storage_path"], row["content_hash"] or row["phash"]))
                     for row in sites[start : start + batch]]
            chunk = [(row, img) for row, img in chunk if img is not None]
            to_embed = [img for row, img in chunk if row["needs_embedding"]]
            embeddings = iter(compute_clip_embeddings(to_embed, config.match))
            for row, img in chunk:
                embedding = next(embeddings) if row["needs_embedding"] else None
                thumb_path = row["thumb_path"]
                if not thumb_path:
                    thumb_path = cloud_storage.site_thumb_path(org_id, row["content_hash"] or str(row["id"]))
                    cloud_storage.upload(storage, cloud_storage.BUCKET_SITE_IMAGES, thumb_path,
                                         fetch.make_thumbnail(img), content_type="image/jpeg")
                with cloud_db.connect(self.database_url) as conn:
                    conn.execute(
                        """UPDATE site_images SET embedding = COALESCE(%s, embedding), thumb_path = %s,
                               compared_at = CASE WHEN %s THEN NULL ELSE compared_at END
                           WHERE org_id = %s AND (id = %s OR (content_hash = %s AND %s::text IS NOT NULL))""",
                        (embedding, thumb_path, embedding is not None, org_id, row["id"], row["content_hash"],
                         row["content_hash"]),
                    )
                updated += 1
            done += len(sites[start : start + batch])
            ctx.report(f"Analyse des images · {done}/{total}", {"phase": "index", "done": done, "total": total})

        result: dict[str, Any] = {"indexed": updated}
        if ctx.cancelled:
            return "Indexation arrêtée.", result
        with cloud_db.connect(self.database_url) as conn:
            has_sites = conn.execute(
                """SELECT EXISTS (SELECT 1 FROM site_images si JOIN sites s ON s.id = si.site_id
                                  WHERE s.brand_id = %s) AS e""",
                (brand_id,),
            ).fetchone()["e"]
        if has_sites:
            result["matches"] = self._compare(ctx, org_id, brand_id, config)
            return f"{updated} image(s) indexée(s), comparaison à jour.", result
        return f"{updated} image(s) indexée(s).", result

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


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("NYRA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # One line per image request drowns everything else. (Not "root": that name is the
    # root logger itself, and silencing it hid every INFO line of the worker.)
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
