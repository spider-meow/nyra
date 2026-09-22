# Nyra

Deterministic CLI (no agent, no LLM in the loop) that takes a library of
rights-managed reference images with expiry dates, crawls a website, and
detects which reference images are present on the site. Output: an
autonomous HTML report + a CSV, sorted by expiry urgency.

Built for the Axel project's MVP demo, targeting `remymartin.com` as the
test site.

## Documentation

This README is a quickstart. For anything deeper, see `docs/`:

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — pipeline diagram, module
  responsibilities, why the codebase is structured the way it is.
- **[docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md)** — every command, every
  flag, typical workflows.
- **[docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)** — table-by-table
  schema reference, idempotency/resumability model.
- **[docs/MATCHING.md](docs/MATCHING.md)** — how the two-level matcher works,
  confidence bands, calibrating thresholds against ground truth.
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** — dev setup, running tests,
  project conventions, how to extend `RefSource`/crawling/matching.

## How it works

1. **Ingest** a folder of reference images + a CSV (`filename, expiry_date,
   credit, notes`) into a local SQLite database, computing a perceptual hash
   (pHash), a difference hash (dHash), and a CLIP embedding for each image.
2. **Crawl** a site with Playwright: discover pages via `sitemap.xml` (falling
   back to internal-link BFS), extract every image (`<img>` src/srcset/data-src,
   `<picture><source>`, CSS `background-image`, `og:image`, `twitter:image`),
   download and hash them.
3. **Match** every reference against every site image in two levels:
   - Level 1: Hamming distance on pHash/dHash — catches identical or
     lightly re-encoded/resized images, cheaply.
   - Level 2 (only for pairs level 1 missed): CLIP cosine similarity —
     catches crops, overlays, and other retouches.
   - A site image whose hash is on the exclusion list (`nyra exclude`,
     recurring false positives like a generic logo) is skipped before
     scoring and never written to `matches`.
4. **Review**: each match starts `pending`; mark it `confirmed`/`rejected`
   with an optional note (`nyra review`, or the UI's comparison panel) —
   traceability that survives future crawls, since a rematch never resets
   a status someone already set.
5. **Report**: a self-contained `report.html` (thumbnails inlined as base64)
   and a `matches.csv`, both sorted by expiry urgency, with a separate
   section for lower-confidence "à vérifier" matches. Only `pending`
   matches are included by default — a report is a to-do list, not a
   permanent log (`--status all` includes everything).

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium
```

(`requirements.txt` is provided as an alternative to the editable install.)

## Usage

```bash
# 1. Ingest the reference library (CSV columns: filename, expiry_date, credit, notes)
nyra ingest-refs --dir refs/ --csv refs.csv

# 2. Crawl the target site
nyra crawl --site https://www.remymartin.com --max-pages 300

# 3. Match references against everything found on the site
nyra match

# 4. Generate the report (refs expired or expiring within N days,
#    only "pending" matches by default — see below)
nyra report --within-days 90

# Or run all four steps in sequence:
nyra run-all --site https://www.remymartin.com --dir refs/ --csv refs.csv --within-days 90

# 5. Review a match found in the report (matches.id from matches.csv/report.html)
nyra review 42 --status confirmed --note "vérifié à la main"
nyra report --status all   # include confirmed/rejected matches too

# 6. Exclude a recurring false positive (e.g. a generic logo) for good
nyra exclude --from-match 42 --reason "logo générique du site"
```

All commands accept `--db path/to/nyra.db` (default `nyra.db`)
and `--config path/to/config.yaml` (default: the repo's `config.yaml`).
Crawling is resumable: pages already marked "done" in the database are
skipped on the next `crawl` run (`--no-resume` forces a full re-crawl).

### Calibrating match thresholds

```bash
nyra calibrate --ground-truth ground_truth.csv
```

`ground_truth.csv` (see `ground_truth.example.csv`) is a small hand-confirmed
set of `ref_filename, site_url, label` (`match`/`no_match`) pairs — both
images must already be in the database (via `ingest-refs` and `crawl`).
The command sweeps pHash/dHash Hamming-distance thresholds and CLIP cosine
thresholds and prints precision/recall/F1 at each, so `config.yaml`'s
`match:` thresholds can be tuned to the actual demo dataset rather than
guessed. See `docs/MATCHING.md` for the full calibration workflow and how
the two-level matcher works internally.

## Configuration

All thresholds and crawl behavior live in `config.yaml` — nothing is
hard-coded in the modules:

- `crawl:` max pages, delay range, user agent, minimum image side (filters
  out icons/tracking pixels), robots.txt compliance, timeouts.
- `match:` pHash/dHash Hamming-distance threshold for a level-1 match, and
  three CLIP cosine-similarity bands (`high` / `medium` / `floor`) that map
  to confidence levels `haut` / `moyen` / `a_verifier`.
- `report:` default expiry window in days.

## Project layout

```
backend/nyra/     pipeline and local API
  cli.py                 Typer commands, including `nyra ui`
  api.py                 FastAPI app: library, crawl, match, report
  refs.py                Reference ingestion: RefSource + CsvRefSource
  crawl.py               Page discovery and image extraction
  fetch.py               Image download, size filter, cache
  match.py               pHash/dHash, then CLIP
  report.py              HTML + CSV report
  db.py                  SQLite schema and access
  templates/             Jinja2 report template
backend/tests/           pytest suite, no network
frontend/                TypeScript interface (Vite, React, Tailwind)
config.yaml              thresholds and crawl limits
docs/                    architecture, CLI, schema, matching, dev guide
```

The interface is TypeScript. From `frontend/`, `npm install` then `npm run dev` (the API stays on port 8765 or 8000). `npm run build` writes `frontend/dist`, which `nyra ui` serves.

`refs.py` exposes a `RefSource` abstract base class so the CSV+folder input
used for the demo can later be swapped for a Brandcenter export adapter
without touching ingestion, matching, or reporting — see
`docs/DEVELOPMENT.md` for how to add one.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The test suite generates its own images with Pillow (original, recompressed,
resized, cropped, overlaid, and a genuinely different image) so it runs
without any binary fixtures, network access, or a Playwright browser. The
one CLIP end-to-end test is skipped automatically if `torch`/`open_clip`
aren't installed. See `docs/DEVELOPMENT.md` for what each test file covers
and the conventions to follow when adding more.

## Out of scope for this MVP

The local interface is `frontend/`, served by `nyra ui`. Multi-tenant
accounts, video, open-web search, a Brandcenter adapter, scheduled alerts,
and multi-site support are still phase 2.
