"""Nyra CLI (Typer).

Two families of commands:

- Offline pipeline on a SQLite file (`ingest-refs`, `crawl`, `match`,
  `report`, `run-all`, `calibrate`, `init-db`) — for debugging the
  crawler on a new site and calibrating thresholds, no account needed.
- The hosted product (`serve`, `worker`, `cloud-*`), which reads
  DATABASE_URL / SUPABASE_* from the environment or a `.env` file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import typer
from tqdm import tqdm

from nyra import crawl as crawl_module
from nyra import db as db_module
from nyra import match as match_module
from nyra import refs as refs_module
from nyra import report as report_module
from nyra.config import load_config

app = typer.Typer(
    name="nyra",
    help="Detect rights-managed reference images on a crawled website, ranked by expiry urgency.",
    no_args_is_help=True,
)

DEFAULT_DB = Path("nyra.db")
DEFAULT_CACHE_DIR = Path("data/site_images")
DEFAULT_OUT_DIR = Path("out")

DbOption = typer.Option(DEFAULT_DB, "--db", help="Path to the SQLite database file.")
ConfigOption = typer.Option(None, "--config", help="Path to config.yaml (defaults to repo config.yaml).")


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _print_sweep(results: dict, out_csv: Optional[Path]) -> None:
    rows_for_csv: list[dict] = []
    for metric_name, sweep in results.items():
        typer.secho(f"\n{metric_name}", bold=True)
        typer.echo(f"{'threshold':>10} {'precision':>10} {'recall':>10} {'f1':>10} {'tp':>5} {'fp':>5} {'fn':>5}")
        for r in sweep:
            typer.echo(
                f"{r.threshold:>10} {r.precision:>10.3f} {r.recall:>10.3f} {r.f1:>10.3f} "
                f"{r.true_positives:>5} {r.false_positives:>5} {r.false_negatives:>5}"
            )
            rows_for_csv.append({
                "metric": metric_name, "threshold": r.threshold, "precision": r.precision, "recall": r.recall,
                "f1": r.f1, "tp": r.true_positives, "fp": r.false_positives, "fn": r.false_negatives,
            })
    if out_csv:
        import csv as csv_lib

        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv_lib.DictWriter(f, fieldnames=["metric", "threshold", "precision", "recall", "f1", "tp", "fp", "fn"])
            writer.writeheader()
            writer.writerows(rows_for_csv)
        typer.secho(f"\nFull sweep written to {out_csv}", fg=typer.colors.GREEN)


# --- offline pipeline (SQLite) -------------------------------------------------------

@app.command("ingest-refs")
def ingest_refs(
    dir: Path = typer.Option(..., "--dir", exists=True, file_okay=False, help="Folder containing reference images."),
    csv: Path = typer.Option(..., "--csv", exists=True, dir_okay=False, help="refs.csv (filename, expiry_date, credit, notes)."),
    db: Path = DbOption,
    config: Optional[Path] = ConfigOption,
    no_embeddings: bool = typer.Option(False, "--no-embeddings", help="Skip CLIP embeddings (phash/dhash only)."),
) -> None:
    """Ingest the reference image library (CSV + folder) into the database."""
    cfg = load_config(config)
    source = refs_module.CsvRefSource(images_dir=dir, csv_path=csv)
    with tqdm(desc="Ingesting refs", unit="img") as bar:
        def on_progress(done: int, total: int) -> None:
            bar.total = total
            bar.n = done
            bar.refresh()

        try:
            count = refs_module.ingest(source, db, cfg, compute_embeddings=not no_embeddings, progress=on_progress)
        except refs_module.RefValidationError as exc:
            _fail(f"Error: {exc}")
    typer.secho(f"Ingested {count} reference image(s) into {db}", fg=typer.colors.GREEN)


@app.command("crawl")
def crawl(
    site: str = typer.Option(..., "--site", help="Site to crawl, e.g. https://www.remymartin.com"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", help="Override config.yaml crawl.max_pages."),
    db: Path = DbOption,
    cache_dir: Path = typer.Option(DEFAULT_CACHE_DIR, "--cache-dir", help="Where downloaded images are cached."),
    config: Optional[Path] = ConfigOption,
    no_resume: bool = typer.Option(False, "--no-resume", help="Re-crawl pages already marked done."),
    no_embeddings: bool = typer.Option(False, "--no-embeddings", help="Skip CLIP embeddings during crawl."),
) -> None:
    """Crawl a site (sitemaps first, then internal links) and extract images."""
    from nyra import netguard

    cfg = load_config(config)
    store = db_module.LocalStore(db, cache_dir)
    embedder = None if no_embeddings else (lambda images: match_module.compute_clip_embeddings(images, cfg.match))
    with tqdm(desc="Crawling pages", unit="page") as bar:
        def on_progress(stats: crawl_module.CrawlStats) -> None:
            bar.n = stats.pages_visited
            bar.set_postfix(images=stats.images_stored, errors=len(stats.errors))
            bar.refresh()

        try:
            stats = crawl_module.crawl_site(
                site, store, cfg, max_pages=max_pages, embedder=embedder, resume=not no_resume, progress=on_progress
            )
        except netguard.BlockedURL as exc:
            _fail(f"Refused: {exc} (set NYRA_ALLOW_PRIVATE_HOSTS=1 to crawl a local test site)")
    typer.secho(
        f"Crawled {stats.pages_visited} page(s), found {stats.images_found} image(s), "
        f"stored {stats.images_stored}, {stats.images_new} new ({len(stats.errors)} error(s))",
        fg=typer.colors.GREEN,
    )
    for err in stats.errors[:10]:
        typer.secho(f"  ! {err}", fg=typer.colors.YELLOW, err=True)


@app.command("match")
def match_cmd(
    db: Path = DbOption,
    config: Optional[Path] = ConfigOption,
    no_clip: bool = typer.Option(False, "--no-clip", help="Level 1 (phash/dhash) only, skip CLIP."),
) -> None:
    """Match every reference image against every crawled site image."""
    count = match_module.run_matching(db_module.LocalStore(db), load_config(config), use_clip=not no_clip)
    typer.secho(f"Found {count} match(es).", fg=typer.colors.GREEN)


@app.command("report")
def report_cmd(
    db: Path = DbOption,
    out_dir: Path = typer.Option(DEFAULT_OUT_DIR, "--out-dir", help="Output folder for report.html and the CSVs."),
    within_days: Optional[int] = typer.Option(None, "--within-days", help="Only include refs expired or expiring within N days."),
    config: Optional[Path] = ConfigOption,
) -> None:
    """Generate report.html + matches.csv + not_found.csv, sorted by expiry urgency."""
    paths = report_module.generate_report(db, out_dir, load_config(config), within_days=within_days)
    typer.secho("Report written to " + ", ".join(str(p) for p in paths), fg=typer.colors.GREEN)


@app.command("run-all")
def run_all(
    site: str = typer.Option(..., "--site"),
    dir: Path = typer.Option(..., "--dir", exists=True, file_okay=False),
    csv: Path = typer.Option(..., "--csv", exists=True, dir_okay=False),
    max_pages: Optional[int] = typer.Option(None, "--max-pages"),
    within_days: Optional[int] = typer.Option(None, "--within-days"),
    db: Path = DbOption,
    cache_dir: Path = typer.Option(DEFAULT_CACHE_DIR, "--cache-dir"),
    out_dir: Path = typer.Option(DEFAULT_OUT_DIR, "--out-dir"),
    config: Optional[Path] = ConfigOption,
    no_clip: bool = typer.Option(False, "--no-clip"),
) -> None:
    """Run ingest-refs -> crawl -> match -> report in sequence."""
    ingest_refs(dir=dir, csv=csv, db=db, config=config, no_embeddings=no_clip)
    crawl(site=site, max_pages=max_pages, db=db, cache_dir=cache_dir, config=config, no_resume=False, no_embeddings=no_clip)
    match_cmd(db=db, config=config, no_clip=no_clip)
    report_cmd(db=db, out_dir=out_dir, within_days=within_days, config=config)


@app.command("calibrate")
def calibrate_cmd(
    ground_truth: Path = typer.Option(..., "--ground-truth", exists=True, dir_okay=False, help="CSV: ref_filename, site_url, label (match/no_match)."),
    db: Path = DbOption,
    config: Optional[Path] = ConfigOption,
    out_csv: Optional[Path] = typer.Option(None, "--out-csv", help="Optionally write the full threshold sweep to CSV."),
) -> None:
    """Sweep thresholds against a hand-confirmed set of ref/site pairs and report precision/recall."""
    _print_sweep(match_module.calibrate(db, ground_truth, load_config(config)), out_csv)


@app.command("init-db")
def init_db_cmd(db: Path = DbOption) -> None:
    """Create the SQLite database and tables if they don't exist yet."""
    db_module.init_db(db)
    typer.secho(f"Database ready at {db}", fg=typer.colors.GREEN)


# --- hosted product ---------------------------------------------------------------

def _cloud_env(*, need_storage: bool = True) -> dict[str, str]:
    from nyra.envfile import load_env_files

    load_env_files()
    env = {key: os.environ.get(key, "") for key in (
        "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "SUPABASE_ANON_KEY",
        "NYRA_PUBLIC_URL", "SENTRY_BROWSER_DSN",
    )}
    required = ["DATABASE_URL"] + (["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"] if need_storage else [])
    missing = [key for key in required if not env[key]]
    if missing:
        _fail(f"Missing {', '.join(missing)} — see .env.example.")
    return env


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Address to bind. Use 0.0.0.0 in a container."),
    port: int = typer.Option(8000, "--port"),
    config: Optional[Path] = ConfigOption,
    with_worker: bool = typer.Option(False, "--with-worker", envvar="NYRA_WITH_WORKER",
                                     help="Also run (and keep running) a worker: for development, not for the web image."),
) -> None:
    """Run the web process: API + interface. Long jobs go to `nyra worker`."""
    import sys
    import threading

    import uvicorn

    from nyra import observability
    from nyra.cloud.api import CloudSettings, create_app

    env = _cloud_env()
    observability.configure_logging("web")
    observability.init_sentry("web")
    if not env["SUPABASE_ANON_KEY"]:
        typer.secho("SUPABASE_ANON_KEY is missing — the interface cannot open a session.", fg=typer.colors.YELLOW)
    settings = CloudSettings(
        database_url=env["DATABASE_URL"], supabase_url=env["SUPABASE_URL"],
        service_role_key=env["SUPABASE_SERVICE_ROLE_KEY"], jwt_secret=env["SUPABASE_JWT_SECRET"] or None,
        anon_key=env["SUPABASE_ANON_KEY"], sentry_browser_dsn=env["SENTRY_BROWSER_DSN"],
        public_url=env["NYRA_PUBLIC_URL"], config_path=config,
    )
    typer.secho(f"Nyra  ->  http://{host}:{port}", fg=typer.colors.GREEN)
    stop = threading.Event()
    watcher = None
    if with_worker:
        from nyra.cloud.worker import supervise

        command = [sys.executable, "-m", "nyra.cli", "worker", *(["--config", str(config)] if config else [])]
        watcher = threading.Thread(target=supervise, args=(command, stop), name="worker-supervisor")
        watcher.start()
    try:
        uvicorn.run(create_app(settings), host=host, port=port, log_level="info", proxy_headers=True, log_config=None)
    finally:
        stop.set()
        if watcher is not None:
            watcher.join()


@app.command("worker")
def worker_cmd(
    config: Optional[Path] = ConfigOption,
    poll_seconds: float = typer.Option(2.0, "--poll-seconds"),
) -> None:
    """Run queued jobs (crawl, match, index, report). Run one or more next to `serve`."""
    import signal

    from nyra.cloud import storage as cloud_storage
    from nyra.cloud.worker import Worker, configure_logging, mail_settings_or_none

    env = _cloud_env()
    configure_logging()
    worker = Worker(
        mail_settings=mail_settings_or_none(config),  # RESEND_API_KEY, NYRA_MAIL_FROM and NYRA_PUBLIC_URL, for the monthly e-mails
        database_url=env["DATABASE_URL"],
        storage_client_factory=lambda: cloud_storage.get_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"]),
        config_path=config, poll_seconds=poll_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()


def _find_or_invite_user(env: dict[str, str], email: str) -> tuple[str, bool]:
    """Supabase Auth user id for this e-mail, inviting them if they don't exist. Returns (id, invited)."""
    from nyra.cloud.routes.members import find_or_invite_user

    return find_or_invite_user(env["DATABASE_URL"], env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"],
                               env["NYRA_PUBLIC_URL"], email)


@app.command("cloud-provision-org")
def cloud_provision_org(
    name: str = typer.Option(..., "--name", help="Organization display name, e.g. 'Rémy Martin'."),
    slug: str = typer.Option(..., "--slug", help="Normalized to lowercase ascii and hyphens, e.g. 'remy-martin'."),
    admin_email: str = typer.Option(..., "--admin-email", help="First admin. Invited by e-mail if they have no account."),
) -> None:
    """Create a client organization (with a first brand of the same name) and its first admin.

    There is no public sign-up. Further brands are added in the interface, under Réglages.
    """
    import uuid

    from nyra.cloud import db as cloud_db

    env = _cloud_env()
    try:
        slug = cloud_db.slugify(slug)
    except ValueError as exc:
        _fail(str(exc))
    user_id, invited = _find_or_invite_user(env, admin_email)
    with cloud_db.connect(env["DATABASE_URL"]) as conn:
        if cloud_db.get_organization_by_slug(conn, slug):
            _fail(f"An organization with slug {slug!r} already exists.")
        org_id = cloud_db.create_organization(conn, name=name, slug=slug)
        cloud_db.add_membership(conn, user_id=uuid.UUID(user_id), org_id=org_id, role="admin")
    note = " (invitation e-mail sent)" if invited else ""
    typer.secho(f"Organization {name!r} ({slug}) created, {admin_email} is admin{note}.", fg=typer.colors.GREEN)


@app.command("cloud-invite")
def cloud_invite(
    org: str = typer.Option(..., "--org", help="Organization slug."),
    email: str = typer.Option(..., "--email"),
    role: str = typer.Option("client", "--role", help="admin (library, crawls, settings) or client (read and review)."),
) -> None:
    """Add someone to an organization, sending a Supabase invitation if they have no account."""
    import uuid

    from nyra.cloud import db as cloud_db

    if role not in {"admin", "client"}:
        _fail("--role must be admin or client.")
    env = _cloud_env()
    with cloud_db.connect(env["DATABASE_URL"]) as conn:
        organization = cloud_db.get_organization_by_slug(conn, org)
    if organization is None:
        _fail(f"No organization with slug {org!r}.")
    user_id, invited = _find_or_invite_user(env, email)
    with cloud_db.connect(env["DATABASE_URL"]) as conn:
        cloud_db.add_membership(conn, user_id=uuid.UUID(user_id), org_id=organization["id"], role=role)
    note = " (invitation e-mail sent)" if invited else ""
    typer.secho(f"{email} is {role} of {organization['name']}{note}.", fg=typer.colors.GREEN)


@app.command("cloud-staff")
def cloud_staff(
    email: str = typer.Option(..., "--email"),
    remove: bool = typer.Option(False, "--remove", help="Take the access away instead of granting it."),
) -> None:
    """Grant (or remove) access to the Nyra team back office: every brand's statistics, the job queue."""
    import uuid

    from nyra.cloud import db as cloud_db
    from nyra.cloud import insights

    env = _cloud_env(need_storage=not remove)
    if remove:
        with cloud_db.connect(env["DATABASE_URL"]) as conn:
            row = conn.execute("SELECT id FROM auth.users WHERE lower(email) = lower(%s)", (email,)).fetchone()
            if row is None:
                _fail(f"No account for {email}.")
            insights.set_staff(conn, row["id"], False)
        typer.secho(f"{email} no longer has back office access.", fg=typer.colors.GREEN)
        return
    user_id, invited = _find_or_invite_user(env, email)
    with cloud_db.connect(env["DATABASE_URL"]) as conn:
        insights.set_staff(conn, uuid.UUID(user_id), True)
    note = " (invitation e-mail sent)" if invited else ""
    typer.secho(f"{email} has back office access{note}.", fg=typer.colors.GREEN)


@app.command("cloud-delete-org")
def cloud_delete_org(
    slug: str = typer.Argument(..., help="Slug of the organization to erase for good."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be deleted (rows, files, accounts); delete nothing."),
    confirm_name: Optional[str] = typer.Option(None, "--confirm-name", help="The organization's exact name (asked for when not given)."),
    operator: str = typer.Option("", "--operator", help="Who runs it, for the trace (default: the system user)."),
) -> None:
    """Erase an organization: database rows, Storage files, and the accounts that belong to nothing else. Irreversible.

    Prints a report; the trace stays in the logs and in `deleted_organizations`. Export first (`cloud-export-org`).
    """
    import json

    from nyra.cloud import storage as cloud_storage
    from nyra.cloud.offboarding import OffboardingError, delete_organization

    env = _cloud_env()
    if not dry_run and confirm_name is None:
        confirm_name = typer.prompt(f"This erases organization {slug!r} for good. Type its exact name to confirm")
    client = cloud_storage.get_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        report = delete_organization(env["DATABASE_URL"], client, slug, confirm_name=confirm_name, operator=operator, dry_run=dry_run)
    except OffboardingError as exc:
        if exc.report:
            typer.echo(json.dumps(exc.report, ensure_ascii=False, indent=2))  # what had been done before the failure
        _fail(str(exc))
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    if report["users_failed"]:
        _fail("Organization erased, but some accounts could not be deleted: delete them in the Supabase dashboard (Authentication).")
    typer.secho("Dry run: nothing was deleted." if dry_run else "Organization erased.", fg=typer.colors.GREEN)


@app.command("cloud-export-org")
def cloud_export_org(
    slug: str = typer.Argument(..., help="Slug of the organization to export."),
    out: Path = typer.Option(..., "--out", help="Folder to write to (created; run again on the same folder to finish a stopped export)."),
    operator: str = typer.Option("", "--operator", help="Who runs it, for the trace (default: the system user)."),
    max_file_mb: int = typer.Option(200, "--max-file-mb", min=1, help="A file bigger than this is reported as failed."),
    max_total_gb: int = typer.Option(50, "--max-total-gb", min=1, help="Stop when the files copied reach this size."),
) -> None:
    """Write an organization's data to a folder: CSV files, the library originals and the reports, and a manifest."""
    from nyra.cloud import storage as cloud_storage
    from nyra.cloud.offboarding import OffboardingError, export_organization

    env = _cloud_env()
    client = cloud_storage.get_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    try:
        manifest = export_organization(env["DATABASE_URL"], client, slug, out, operator=operator,
                                       max_file_bytes=max_file_mb * 1_000_000, max_total_bytes=max_total_gb * 1_000_000_000)
    except OffboardingError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - the operator reads a sentence, not a trace; the log has the trace
        logging.getLogger("nyra.offboarding").exception("export of %s failed", slug)
        _fail(f"Export failed ({type(exc).__name__}: {str(exc)[:200]}). Run the same command again: files already copied are kept.")
    files = manifest["files"]
    typer.echo(f"{sum(manifest['tables'].values())} CSV rows, {files['copied']} file(s) copied, {files['already_there']} already there -> {out}")
    if not manifest["complete"]:
        for failure in files["failures"]:
            typer.secho(f"FAILED {failure['bucket']}/{failure['path']}: {failure['error']}", fg=typer.colors.RED, err=True)
        _fail(f"Export incomplete: {len(files['failures'])} file(s) missing. Run the same command again.")
    typer.secho("Export complete.", fg=typer.colors.GREEN)


@app.command("cloud-purge-audit")
def cloud_purge_audit(
    months: int = typer.Option(12, "--months", min=1, help="Keep this many months of the audit log."),
) -> None:
    """Delete audit log rows older than the retention (the worker does it every day; this is for by hand)."""
    from nyra.cloud import audit

    removed = audit.purge(_cloud_env(need_storage=False)["DATABASE_URL"], months)
    typer.secho(f"{removed} audit row(s) older than {months} months deleted.", fg=typer.colors.GREEN)


@app.command("cloud-usage")
def cloud_usage(
    brands: int = typer.Option(3, "--brands", min=1, help="How many brands to plan for."),
    measure: bool = typer.Option(False, "--measure", help="Take the sizes from the database (DATABASE_URL) instead of assumptions."),
    cache_mb: int = typer.Option(2048, "--cache-mb", help="The worker's image cache (NYRA_IMAGE_CACHE_MB); 0 without a persistent disk."),
    set_: list[str] = typer.Option([], "--set", help="Override a figure, e.g. --set users=10 (repeatable)."),
) -> None:
    """Estimate a month of Supabase usage (egress by source, storage, database) for N brands."""
    from nyra import capacity

    brand = capacity.Brand()
    if measure:
        from nyra.cloud import db as cloud_db

        with cloud_db.connect(_cloud_env(need_storage=False)["DATABASE_URL"]) as conn:
            brand = capacity.measured(conn.execute(capacity.MEASURE_SQL).fetchone() or {})
    try:
        brand = capacity.override(brand, set_)
    except ValueError as exc:
        _fail(str(exc))
    for line in capacity.render(capacity.estimate(brand, brands, cache_mb=cache_mb), brands, cache_mb):
        typer.echo(line)


@app.command("cloud-calibrate")
def cloud_calibrate(
    org: str = typer.Option(..., "--org", help="Organization slug."),
    out_csv: Optional[Path] = typer.Option(None, "--out-csv"),
) -> None:
    """Threshold sweep using the decisions already taken in the interface as ground truth.

    "À retirer" and "Retiré" count as true matches, "Faux positif" as a
    non-match. The more decisions, the more trustworthy the sweep.
    """
    from nyra.cloud import db as cloud_db
    from nyra.cloud.store import feature_dict

    env = _cloud_env(need_storage=False)
    with cloud_db.connect(env["DATABASE_URL"]) as conn:
        organization = cloud_db.get_organization_by_slug(conn, org)
        if organization is None:
            _fail(f"No organization with slug {org!r}.")
        rows = conn.execute(
            """
            SELECT v.decision, r.filename, s.url,
                   r.phash AS r_phash, r.dhash AS r_dhash, r.phash_flip AS r_phash_flip,
                   r.dhash_flip AS r_dhash_flip, r.embedding AS r_embedding,
                   s.phash AS s_phash, s.dhash AS s_dhash, s.embedding AS s_embedding
            FROM reviews v
            JOIN reference_images r ON r.id = v.reference_id
            JOIN site_images s ON s.id = v.site_image_id
            WHERE v.org_id = %s
            """,
            (organization["id"],),
        ).fetchall()
    if not rows:
        _fail("No decisions recorded yet for this organization.")
    labeled = []
    for row in rows:
        gt = match_module.GroundTruthPair(row["filename"], row["url"], row["decision"] in {"retenu", "traite"})
        ref = feature_dict({"phash": row["r_phash"], "dhash": row["r_dhash"], "phash_flip": row["r_phash_flip"],
                            "dhash_flip": row["r_dhash_flip"], "embedding": row["r_embedding"]})
        site = feature_dict({"phash": row["s_phash"], "dhash": row["s_dhash"], "embedding": row["s_embedding"]})
        labeled.append((gt, ref, site))
    positives = sum(1 for gt, _, _ in labeled if gt.is_match)
    typer.secho(f"{len(labeled)} decision(s): {positives} match(es), {len(labeled) - positives} false positive(s).", bold=True)
    typer.secho("Only pairs the matcher already proposed are labeled, so recall here is relative to those.", fg=typer.colors.YELLOW)
    _print_sweep(match_module.sweep_features(labeled), out_csv)


if __name__ == "__main__":
    app()
