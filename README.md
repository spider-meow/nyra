# RightsWatch

Deterministic CLI (no agent, no LLM in the loop) that takes a library of
rights-managed reference images with expiry dates, crawls a website, and
detects which reference images are present on the site. Output: an
autonomous HTML report + a CSV, sorted by expiry urgency.

Built for the Axel project's MVP demo, targeting `remymartin.com` as the
test site.

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
4. **Report**: a self-contained `report.html` (thumbnails inlined as base64)
   and a `matches.csv`, both sorted by expiry urgency, with a separate
   section for lower-confidence "à vérifier" matches.

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
rightswatch ingest-refs --dir refs/ --csv refs.csv

# 2. Crawl the target site
rightswatch crawl --site https://www.remymartin.com --max-pages 300

# 3. Match references against everything found on the site
rightswatch match

# 4. Generate the report (refs expired or expiring within N days)
rightswatch report --within-days 90

# Or run all four steps in sequence:
rightswatch run-all --site https://www.remymartin.com --dir refs/ --csv refs.csv --within-days 90
```

All commands accept `--db path/to/rightswatch.db` (default `rightswatch.db`)
and `--config path/to/config.yaml` (default: the repo's `config.yaml`).
Crawling is resumable: pages already marked "done" in the database are
skipped on the next `crawl` run (`--no-resume` forces a full re-crawl).

### Calibrating match thresholds

```bash
rightswatch calibrate --ground-truth ground_truth.csv
```

`ground_truth.csv` (see `ground_truth.example.csv`) is a small hand-confirmed
set of `ref_filename, site_url, label` (`match`/`no_match`) pairs — both
images must already be in the database (via `ingest-refs` and `crawl`).
The command sweeps pHash/dHash Hamming-distance thresholds and CLIP cosine
thresholds and prints precision/recall/F1 at each, so `config.yaml`'s
`match:` thresholds can be tuned to the actual demo dataset rather than
guessed.

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
rightswatch/
  cli.py     Typer commands (ingest-refs, crawl, match, report, run-all, calibrate)
  refs.py    Reference ingestion: RefSource interface + CsvRefSource
  crawl.py   Page discovery (sitemap -> internal links) + image extraction
  fetch.py   Image download, normalization, size filtering, caching
  match.py   Two-level matching (pHash/dHash, then CLIP) + calibration sweep
  report.py  HTML (self-contained, base64 thumbnails) + CSV report
  db.py      SQLite schema and access helpers
templates/report.html.j2   Jinja2 report template
tests/                      pytest suite (pure-function + end-to-end, no network)
```

`refs.py` exposes a `RefSource` abstract base class so the CSV+folder input
used for the demo can later be swapped for a Brandcenter export adapter
without touching ingestion, matching, or reporting.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The test suite generates its own images with Pillow (original, recompressed,
resized, cropped, overlaid, and a genuinely different image) so it runs
without any binary fixtures, network access, or a Playwright browser. The
one CLIP end-to-end test is skipped automatically if `torch`/`open_clip`
aren't installed.

## Out of scope for this MVP

Web UI, multi-tenant, video, open-web search, detecting site images with no
DAM equivalent, a Brandcenter adapter, scheduled crawls + email alerts, a
FastAPI web interface, and multi-site support are all phase 2 — see the
project brief for details.
