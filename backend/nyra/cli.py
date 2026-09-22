"""Nyra CLI (Typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from tqdm import tqdm

from nyra.config import load_config
from nyra import db as db_module
from nyra import refs as refs_module
from nyra import crawl as crawl_module
from nyra import match as match_module
from nyra import report as report_module

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
            count = refs_module.ingest(
                source, db, cfg, compute_embeddings=not no_embeddings, progress=on_progress
            )
        except refs_module.RefValidationError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

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
    """Crawl a site (sitemap.xml first, then internal links) and extract images."""
    cfg = load_config(config)

    with tqdm(desc="Crawling pages", unit="page") as bar:
        def on_progress(stats: crawl_module.CrawlStats) -> None:
            bar.n = stats.pages_visited
            bar.set_postfix(images=stats.images_stored, errors=len(stats.errors))
            bar.refresh()

        stats = crawl_module.crawl_site(
            site,
            db,
            cache_dir,
            cfg,
            max_pages=max_pages,
            compute_embeddings=not no_embeddings,
            resume=not no_resume,
            progress=on_progress,
        )

    typer.secho(
        f"Crawled {stats.pages_visited} page(s), found {stats.images_found} image(s), "
        f"stored {stats.images_stored} ({len(stats.errors)} error(s))",
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
    cfg = load_config(config)
    count = match_module.run_matching(db, cfg, use_clip=not no_clip)
    typer.secho(f"Found {count} match(es).", fg=typer.colors.GREEN)


@app.command("report")
def report_cmd(
    db: Path = DbOption,
    out_dir: Path = typer.Option(DEFAULT_OUT_DIR, "--out-dir", help="Output folder for report.html and matches.csv."),
    within_days: Optional[int] = typer.Option(None, "--within-days", help="Only include refs expired or expiring within N days."),
    config: Optional[Path] = ConfigOption,
) -> None:
    """Generate report.html + matches.csv, sorted by expiry urgency."""
    cfg = load_config(config)
    html_path, csv_path, not_found_csv_path = report_module.generate_report(db, out_dir, cfg, within_days=within_days)
    typer.secho(
        f"Report written to {html_path}, {csv_path}, and {not_found_csv_path}", fg=typer.colors.GREEN
    )


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
    cfg = load_config(config)
    results = match_module.calibrate(db, ground_truth, cfg)

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
                "metric": metric_name, "threshold": r.threshold, "precision": r.precision,
                "recall": r.recall, "f1": r.f1, "tp": r.true_positives,
                "fp": r.false_positives, "fn": r.false_negatives,
            })

    if out_csv:
        import csv as csv_lib

        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv_lib.DictWriter(f, fieldnames=["metric", "threshold", "precision", "recall", "f1", "tp", "fp", "fn"])
            writer.writeheader()
            writer.writerows(rows_for_csv)
        typer.secho(f"\nFull sweep written to {out_csv}", fg=typer.colors.GREEN)


@app.command("init-db")
def init_db_cmd(db: Path = DbOption) -> None:
    """Create the SQLite database and tables if they don't exist yet."""
    db_module.init_db(db)
    typer.secho(f"Database ready at {db}", fg=typer.colors.GREEN)


@app.command("ui")
def ui_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Address to bind. Defaults to this machine only."),
    port: int = typer.Option(8000, "--port", help="Port for the interface."),
    db: Path = DbOption,
    config: Optional[Path] = ConfigOption,
) -> None:
    """Open the interface to run the pipeline without the CLI.

    Local mode (default): SQLite + local disk, no accounts — unchanged.
    Cloud mode: automatic the moment DATABASE_URL is set in the
    environment (see .env.example) — Postgres/Supabase Storage/Supabase
    Auth instead, multi-organization. Nothing else about this command
    changes; the switch is entirely env-var driven.
    """
    import os

    import uvicorn

    from nyra.envfile import load_env_files

    load_env_files()
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        from nyra.cloud.api import CloudSettings, create_app as create_cloud_app

        supabase_url = os.environ.get("SUPABASE_URL")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not service_role_key:
            typer.secho(
                "DATABASE_URL is set but SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are missing — "
                "see .env.example.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

        anon_key = os.environ.get("SUPABASE_ANON_KEY") or ""
        if not anon_key:
            typer.secho(
                "SUPABASE_ANON_KEY is missing — the interface cannot open a session. "
                "Copy the anon / publishable key from Project Settings > API. See .env.example.",
                fg=typer.colors.YELLOW,
            )
        settings = CloudSettings(
            database_url=database_url,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            jwt_secret=os.environ.get("SUPABASE_JWT_SECRET"),
            anon_key=anon_key,
        )
        typer.secho(f"Nyra (cloud mode)  ->  http://{host}:{port}", fg=typer.colors.GREEN)
        uvicorn.run(create_cloud_app(settings), host=host, port=port, log_level="info")
        return

    from nyra.api import create_app

    root = Path.cwd()
    db_path = db if db.is_absolute() else root / db
    typer.secho(f"Nyra  ->  http://{host}:{port}", fg=typer.colors.GREEN)
    uvicorn.run(
        create_app(root=root, db_path=db_path, config_path=config),
        host=host,
        port=port,
        log_level="info",
    )


@app.command("cloud-provision-org")
def cloud_provision_org(
    name: str = typer.Option(..., "--name", help="Organization display name, e.g. 'Remy Martin'."),
    slug: str = typer.Option(..., "--slug", help="Organization slug. Normalized to lowercase ascii and hyphens, e.g. 'Rémy Martin' becomes 'remy-martin'."),
    admin_email: str = typer.Option(..., "--admin-email", help="Email of the Supabase Auth user to make admin."),
) -> None:
    """Bootstrap a new client organization (cloud mode only).

    A user can't create their own first organization — Row Level Security
    requires an existing admin membership to add one (see
    supabase/migrations/migration_005_row_level_security.sql), which is exactly the chicken-and-egg this
    command exists to break. The admin_email user must already exist in
    Supabase Auth (sign them up first, e.g. via the Supabase dashboard or
    your own invite flow); this only creates the organization and their
    first membership.
    """
    import os

    from nyra.envfile import load_env_files

    load_env_files()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        typer.secho("DATABASE_URL is not set — see .env.example.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    from nyra.cloud import db as cloud_db

    try:
        slug = cloud_db.slugify(slug)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    with cloud_db.connect(database_url) as conn:
        user_row = conn.execute("SELECT id FROM auth.users WHERE email = %s", (admin_email,)).fetchone()
        if user_row is None:
            typer.secho(
                f"No Supabase Auth user with email {admin_email!r} — they need to sign up first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

        org_id = cloud_db.create_organization(conn, name=name, slug=slug)
        cloud_db.add_membership(conn, user_id=user_row["id"], org_id=org_id, role="admin")

    typer.secho(f"Organization {name!r} ({slug}) created, {admin_email} is admin.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
