# CLI reference

All commands are subcommands of `nyra` (`pip install -e ".[worker]"`).
`nyra --help` and `nyra <command> --help` mirror this page.

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

Runs queued jobs: `crawl`, `match`, `index`, `report`. Run one or more
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
