# CLI reference

All commands are subcommands of `nyra` (`pip install -e ".[worker]"`).
`nyra --help` and `nyra <command> --help` mirror this page.

The commands under "Hosted product" are the ones that run the product. The
offline commands further down (`ingest-refs`, `crawl`, `match`, `run-all`,
`calibrate`...) work on a local SQLite file and exist for debugging and for the
test suite: they are not extended any more, and a local database made before
the keypoint check received its images keeps its old "to verify" results until a
full recomputation is forced (change a threshold or recreate the file).

## Hosted product

These read `DATABASE_URL` and `SUPABASE_*` from the environment or a
`.env` file (see `.env.example`).

### `serve`

The web process: API and interface. Queues long work for the worker.

```bash
nyra serve --host 0.0.0.0 --port 8000
```

| Option | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | `0.0.0.0` in a container. |
| `--port` | `8000` | |
| `--config` | repo `config.yaml` | Base configuration; organizations override part of it from Settings. |

### `worker`

Runs queued jobs: `crawl`, `match`, `index`, `report`, `locate`. Run one or more
next to `serve`; each takes one job at a time, never two for the same
organization. Stops cleanly on SIGTERM.

```bash
nyra worker
```

| Option | Default | Notes |
|---|---|---|
| `--config` | repo `config.yaml` | |
| `--poll-seconds` | `2.0` | Wait between two looks at an empty queue. |

### `cloud-provision-org`

Creates an organization and its first admin. The admin is invited by
e-mail if they have no account yet. There is no public sign-up.

```bash
nyra cloud-provision-org --name "Rémy Martin" --slug remy-martin --admin-email admin@example.com
```

### `cloud-invite`

Adds someone to an organization (inviting them if needed), or changes
their role.

```bash
nyra cloud-invite --org remy-martin --email reviewer@example.com --role client
```

`--role admin`: library, crawls, reports, settings. `--role client`:
read and record decisions.

### `cloud-staff`

Grants access to the Nyra team back office (`/back-office`: every
brand's statistics, the job queue, recent failures), inviting the
person if they have no account. `--remove` takes it away. See
`OBSERVABILITY.md`.

```bash
nyra cloud-staff --email arthur@example.com
nyra cloud-staff --email arthur@example.com --remove
```

### `cloud-export-org`

Writes an organization's data to a folder: CSV (brands, sites, references, decisions, exclusions, reports, members,
activity log), the originals of the library and the reports, and `manifeste.json`. Streams; files already in the folder
are kept, so a stopped export is finished by running the same command again. Exits 1 when a file is missing.

```bash
nyra cloud-export-org remy-martin --out exports/remy-martin [--max-file-mb 200] [--max-total-gb 50] [--operator name]
```

### `cloud-delete-org`

Erases an organization for good: rows, Storage files of the three buckets, and the Auth accounts that belong to
nothing else. Asks for the exact name of the organization (or `--confirm-name`); `--dry-run` lists and deletes
nothing; refuses while a task is queued or running. Prints a JSON report and leaves a trace (log line and
`deleted_organizations`).

```bash
nyra cloud-delete-org remy-martin --dry-run
nyra cloud-delete-org remy-martin
```

### `cloud-purge-audit`

Deletes activity-log rows older than `--months` (12 by default), in batches. The worker runs it daily.

### `cloud-calibrate`

Threshold sweep using the decisions already recorded in the interface as
ground truth ("to remove" and "removed" = match, "false positive" =
non-match). Only pairs the matcher proposed are labeled, so recall is
relative to those. See `MATCHING.md`.

```bash
nyra cloud-calibrate --org remy-martin --out-csv sweep.csv
```

## Offline pipeline (SQLite)

Every command accepts `--db` (default `nyra.db`) and `--config`.

### `ingest-refs`

```bash
nyra ingest-refs --dir refs/ --csv refs.csv
```

`refs.csv` columns: `filename, expiry_date, credit, notes`. Dates are
`YYYY-MM-DD` or day-first `DD/MM/YYYY` (also `-` or `.`); month-first
dates are refused rather than guessed. `--no-embeddings` skips CLIP.

### `crawl`

```bash
nyra crawl --site https://www.example.com --max-pages 300
```

| Option | Default | Notes |
|---|---|---|
| `--max-pages` | `crawl.max_pages` | Capped by `crawl.max_pages_limit`. |
| `--cache-dir` | `data/site_images` | Downloaded images. |
| `--no-resume` | off | Re-read pages already read. |
| `--no-embeddings` | off | Skip CLIP. |

Needs `playwright install chromium`. Private and local addresses are
refused; set `NYRA_ALLOW_PRIVATE_HOSTS=1` to crawl a test site on your
own machine (never on a server).

### `match`, `report`, `run-all`

```bash
nyra match                    # --no-clip for hashes only
nyra report --within-days 90  # out/report.html, matches.csv, not_found.csv
nyra run-all --site https://www.example.com --dir refs/ --csv refs.csv
```

### `calibrate`

```bash
nyra calibrate --ground-truth ground_truth.csv --out-csv sweep.csv
```

`ground_truth.csv`: `ref_filename, site_url, label` (`match`/`no_match`).

### `init-db`

Creates the SQLite file and tables.
