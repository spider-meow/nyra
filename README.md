# Nyra

Nyra watches websites for rights-managed images whose licence has expired
or is about to. It keeps a library of reference images with their expiry
dates, reads a site the way a visitor would, recognises the references
among the images it finds — resized, recompressed, flipped or cropped —
and ranks what it found by expiry urgency. There is no LLM in the loop:
matching is perceptual hashing plus CLIP embeddings, deterministic given
the same inputs and thresholds.

It is a hosted, multi-organization product (Supabase for Postgres, Auth
and Storage), plus a CLI that runs the same pipeline on a local SQLite
file for debugging and threshold calibration.

## How it works

1. **Library.** An admin uploads reference images and their expiry dates
   (one by one, in bulk, or from a CSV). Each image gets a pHash, a dHash,
   the hashes of its mirror image, a thumbnail and a CLIP embedding.
2. **Read the site.** The worker drives headless Chromium through the
   site's sitemaps and internal links, gets past cookie banners and age
   gates, and collects every image (`<img>`/`srcset`, `<picture>`, CSS
   backgrounds, `og:image`). Identical bytes served under several URLs
   are stored once.
3. **Compare.** Hamming distance on the hashes catches the same image
   re-encoded, resized or flipped; CLIP similarity catches crops and
   overlays. Each hit gets a confidence: *confirmed*, *probable*, *to
   verify*.
4. **Review.** People go through what was found, oldest expiry first, and
   mark each occurrence *to remove*, *false positive* or *removed*.
5. **Report.** A dated, self-contained HTML report (printable to PDF) and
   two CSVs, without the false positives.

## Running it

Two processes share one Postgres database and one Supabase project:

```bash
pip install -e ".[worker]"        # the worker needs Chromium and torch
playwright install chromium
cp .env.example .env              # fill in the Supabase values
nyra serve                        # API + interface on http://127.0.0.1:8000
nyra worker                       # runs crawl / compare / index / report jobs
```

Apply `supabase/migrations/` to the project first, and create the first
organization (there is no public sign-up):

```bash
nyra cloud-provision-org --name "Rémy Martin" --slug remy-martin --admin-email admin@example.com
nyra cloud-invite --org remy-martin --email reviewer@example.com --role client
```

In production, `docker compose up -d --build` runs both processes; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Offline CLI

The same crawler and matcher on a SQLite file, no account needed:

```bash
nyra run-all --site https://www.example.com --dir refs/ --csv refs.csv --within-days 90
nyra calibrate --ground-truth ground_truth.csv
```

See [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — processes, pipeline, module map, why it's built this way
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — images, Supabase setup, scaling, security notes
- [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) — every command
- [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) — Postgres and SQLite schemas, job queue
- [docs/MATCHING.md](docs/MATCHING.md) — the two-level matcher, calibration
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — setup, tests, conventions
- [supabase/README.md](supabase/README.md) — applying the migrations

## Layout

```
backend/nyra/
  crawl.py fetch.py netguard.py   the crawler, image processing, outbound-request guard
  match.py refs.py report.py      matching, reference ingestion, grouping and reports
  db.py                           SQLite store for the CLI
  cloud/                          hosted product: api (web), worker, jobs, store, db, storage, auth
  cli.py config.py
backend/tests/                    pytest; cloud tests need TEST_DATABASE_URL
frontend/                         React + TypeScript interface (Vite, Tailwind, React Router, TanStack Query)
supabase/migrations/              Postgres schema, RLS, storage buckets, job queue
config.yaml                       thresholds and crawl behavior
```
