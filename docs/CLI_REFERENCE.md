# CLI reference

All commands are subcommands of `nyra` (installed via `pip install -e .`,
or run as `python -m nyra.cli <command>` without installing). Every
command accepts `--db` (default `nyra.db`) and `--config` (default:
the repo's `config.yaml`) unless noted otherwise.

Run `nyra --help` or `nyra <command> --help` at any time —
this document mirrors that output with added context.

## `ingest-refs`

Reads the reference image library into the database.

```bash
nyra ingest-refs --dir refs/ --csv refs.csv
```

| Option | Required | Default | Notes |
|---|---|---|---|
| `--dir` | yes | — | Folder containing the reference images. Must exist. |
| `--csv` | yes | — | `refs.csv` with columns `filename, expiry_date, credit, notes` (see `refs.csv.example`). `expiry_date` accepts `YYYY-MM-DD` or `DD/MM/YYYY`/`MM/DD/YYYY`/`DD-MM-YYYY`; leave it blank for "unknown". |
| `--db` | no | `nyra.db` | |
| `--config` | no | repo `config.yaml` | |
| `--no-embeddings` | no | off | Skip CLIP embeddings — ingest is faster and doesn't need `torch`/`open_clip` installed, but level 2 matching will find nothing for these refs until you re-ingest with embeddings. |

Safe to re-run: updates existing rows (matched by `filename`) in place.
Fails fast with a clear message if a row's image file is missing from
`--dir`, or if `--csv` is missing the required columns.

## `crawl`

Crawls a site and extracts every image it finds.

```bash
nyra crawl --site https://www.remymartin.com --max-pages 300
```

| Option | Required | Default | Notes |
|---|---|---|---|
| `--site` | yes | — | Site root, e.g. `https://www.remymartin.com`. |
| `--max-pages` | no | `config.yaml`'s `crawl.max_pages` (300) | Caps how many pages this run visits. |
| `--db` | no | `nyra.db` | |
| `--cache-dir` | no | `data/site_images` | Where downloaded image bytes are cached on disk. |
| `--config` | no | repo `config.yaml` | |
| `--no-resume` | no | off | Re-crawl pages already marked `done` instead of skipping them. |
| `--no-embeddings` | no | off | Skip CLIP embeddings for discovered images (same trade-off as `ingest-refs --no-embeddings`). |

Requires a Playwright Chromium install (`playwright install chromium`).
Respects `robots.txt` (disable via `config.yaml`'s `crawl.respect_robots_txt:
false` if you have explicit authorization to ignore it) and waits
`delay_seconds_min`-`delay_seconds_max` between page loads. Interruptible
and resumable — see `DATABASE_SCHEMA.md`'s "Idempotency and resumability"
section.

## `match`

Matches every reference image against every image found on the site.

```bash
nyra match
```

| Option | Required | Default | Notes |
|---|---|---|---|
| `--db` | no | `nyra.db` | |
| `--config` | no | repo `config.yaml` | |
| `--no-clip` | no | off | Level 1 (pHash/dHash) only — skip CLIP entirely, no `torch`/`open_clip` needed. |

Always fully recomputes the `matches` table (see `DATABASE_SCHEMA.md`) —
re-run this after changing `config.yaml` thresholds or after any new
`ingest-refs`/`crawl` data lands.

## `report`

Generates the deliverables: `report.html` + `matches.csv`.

```bash
nyra report --within-days 90
```

| Option | Required | Default | Notes |
|---|---|---|---|
| `--db` | no | `nyra.db` | |
| `--out-dir` | no | `out/` | Output folder for `report.html` and `matches.csv`. |
| `--within-days` | no | `config.yaml`'s `report.default_within_days` (90) | Only include references that are expired or expiring within N days. References with no known `expiry_date` are always included (flagged `inconnue`). |
| `--config` | no | repo `config.yaml` | |

Read-only — never modifies the database. Re-run any time to regenerate
with a different `--within-days` window.

## `run-all`

Runs `ingest-refs` → `crawl` → `match` → `report` in sequence, for the
common case of a full pipeline run from a clean database.

```bash
nyra run-all \
  --site https://www.remymartin.com \
  --dir refs/ --csv refs.csv \
  --within-days 90
```

Accepts the union of the options above (`--site`, `--dir`, `--csv`,
`--max-pages`, `--within-days`, `--db`, `--cache-dir`, `--out-dir`,
`--config`, `--no-clip`). There's no `--no-resume` here — `run-all` always
does a fresh full crawl. For incremental runs (crawl already done,
tuning thresholds, regenerating the report), call the individual commands
instead — that's exactly what they're for.

## `calibrate`

Sweeps match thresholds against a hand-confirmed set of pairs and reports
precision/recall/F1. See `MATCHING.md`'s "Calibrating thresholds" section
for the full workflow.

```bash
nyra calibrate --ground-truth ground_truth.csv --out-csv sweep.csv
```

| Option | Required | Default | Notes |
|---|---|---|---|
| `--ground-truth` | yes | — | CSV: `ref_filename, site_url, label` (`match`/`no_match`, or `1`/`0`/`true`/`false`/`yes`). Both filenames/URLs must already exist in the database. |
| `--db` | no | `nyra.db` | |
| `--config` | no | repo `config.yaml` | |
| `--out-csv` | no | *(none)* | Optionally write the full threshold sweep to a CSV, in addition to the printed table. |

Read-only — never modifies `config.yaml` or the database. Apply the
thresholds you pick by hand-editing `config.yaml`, then re-run `match`.

## `init-db`

Creates the SQLite file and tables if they don't exist yet, without
ingesting or crawling anything. Mostly useful for scripting/tooling that
wants a database to inspect before running the real pipeline.

```bash
nyra init-db --db nyra.db
```

## Typical workflows

**First run on a new site:**
```bash
nyra run-all --site https://www.example.com --dir refs/ --csv refs.csv
```

**Iterating on match thresholds without re-crawling:**
```bash
# edit config.yaml's match: thresholds
nyra match
nyra report --within-days 90
```

**Extending an interrupted crawl:**
```bash
nyra crawl --site https://www.example.com --max-pages 300   # picks up where it left off
```

**Fast smoke test with no heavy dependencies:**
```bash
nyra ingest-refs --dir refs/ --csv refs.csv --no-embeddings
nyra crawl --site https://www.example.com --max-pages 5 --no-embeddings
nyra match --no-clip
nyra report
```
