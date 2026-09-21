# Architecture

RightsWatch is a deterministic pipeline, not an agent: every step is a plain
function or CLI command that reads inputs, does one job, and writes outputs
to a shared SQLite database. There is no LLM anywhere in the runtime path —
matching decisions come from perceptual hashing and CLIP embeddings, both
fully reproducible given the same inputs and `config.yaml`.

## Pipeline overview

```
 refs/ + refs.csv                 https://target-site.com
        |                                   |
        v                                   v
 +--------------+                  +------------------+
 |   refs.py    |                  |    crawl.py      |
 |  (ingest)    |                  |  (Playwright)     |
 +------+-------+                  +---------+--------+
        |                                    |
        | phash/dhash/CLIP                   | page discovery
        | per reference image                | (sitemap -> links)
        v                                    v
 +-----------------------------------------------------+
 |                     db.py (SQLite)                   |
 |  reference_images  pages  site_images  image_pages    |
 +------------------------+------------------------------+
                           |
                           | fetch.py downloads each
                           | discovered image URL,
                           | filters by size, hashes it
                           v
 +-----------------------------------------------------+
 |                     match.py                          |
 |  level 1: pHash/dHash Hamming distance                |
 |  level 2: CLIP cosine similarity (unresolved pairs)   |
 |  writes rows into matches                             |
 +------------------------+------------------------------+
                           |
                           v
 +-----------------------------------------------------+
 |                     report.py                         |
 |  report.html (self-contained, base64 thumbnails)      |
 |  matches.csv                                           |
 +-----------------------------------------------------+
```

`cli.py` is the only thing that wires these together end to end (the
`run-all` command); every other module can be imported and used on its own,
which is what the test suite does.

## Module responsibilities

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Load `config.yaml` into typed dataclasses (`CrawlConfig`, `MatchConfig`, `ReportConfig`). Nothing else reads the YAML file directly. | — |
| `db.py` | SQLite schema + all reads/writes. Every write is an upsert keyed on a natural key (`filename` for refs, `url` for pages/images), so re-running any step is idempotent. | `config.py` (implicitly, via callers) |
| `refs.py` | `RefSource` abstract interface + `CsvRefSource`. Reads `refs.csv` + an image folder, computes hashes/embeddings, upserts into `reference_images`. | `db.py`, `match.py` (for hashing functions) |
| `crawl.py` | Discovers pages (sitemap.xml, falling back to internal-link BFS) and extracts image URLs from each page's rendered HTML. Drives a headless Playwright browser. | `db.py`, `fetch.py` |
| `fetch.py` | Downloads a single image URL, filters out anything smaller than `min_image_side_px`, hashes it, caches the bytes on disk, upserts into `site_images`. | `db.py`, `match.py` (for hashing functions) |
| `match.py` | Pure classification functions (`classify_level1`, `classify_level2`) plus `run_matching` (the DB-integrated pass) and `calibrate` (threshold sweep against hand-labeled ground truth). | `db.py` (only in `run_matching`/`calibrate`) |
| `report.py` | Reads `matches` + joins, computes expiry urgency, renders `report.html` (Jinja2, base64 thumbnails) and `matches.csv`. | `db.py` |
| `cli.py` | Typer commands wiring the above into `ingest-refs`, `crawl`, `match`, `report`, `run-all`, `calibrate`, `init-db`. | all of the above |

## Why it's built this way

**Two-level matching (pHash/dHash then CLIP).** Perceptual hashing is cheap
(microseconds, no GPU) and catches the common case — the same image
re-encoded, resized, or lightly compressed by a CDN. It produces a lot of
false negatives on crops and text overlays, though, since those move the
low-frequency image structure that pHash/dHash are built on. CLIP embeddings
catch those cases but cost more to compute, so level 2 only ever runs on
pairs level 1 already rejected — see `match.classify_pair`.

**Confidence bands, not a single yes/no.** A match is `haut` (high),
`moyen` (medium), or `a_verifier` (needs manual review), driven by
`config.yaml`'s `clip_similarity_high` / `_medium` / `_floor` thresholds
(pHash/dHash hits are always `haut` — see `MATCHING.md` for why). The report
puts `a_verifier` matches in their own section so a reviewer isn't stuck
re-checking obvious hits.

**SQLite instead of an in-memory pipeline.** Every step reads/writes the
same file, so `crawl` can be interrupted and resumed (`pages.status`),
`match` can be re-run after tuning `config.yaml` without re-crawling, and
`report` can be regenerated with a different `--within-days` window without
recomputing anything upstream.

**`RefSource` as an abstract interface.** The MVP's input is a hand-filled
CSV + folder (`CsvRefSource`), but the brief for phase 2 is a Brandcenter
DAM export. Anything that can yield `RefEntry` objects works as a drop-in
replacement — `refs.ingest()` and everything downstream never see the CSV
directly.

**Pure functions where it matters for testing.** HTML/sitemap parsing
(`crawl.extract_images_from_html`, `extract_internal_links`,
`parse_srcset`) and match classification (`match.classify_level1/2`) take
plain data in and return plain data out — no database, no browser, no
network. That's what lets the test suite run in under half a second with no
external dependencies (see `DEVELOPMENT.md`).

## Data flow in detail

1. **Ingest.** For each row in `refs.csv`, `refs.ingest()` opens the image,
   computes `phash`/`dhash` (`imagehash`) and a normalized CLIP embedding
   (`open_clip`), and upserts a row into `reference_images` keyed on
   `filename`.
2. **Crawl.** `crawl.crawl_site()` fetches `robots.txt` and `sitemap.xml`
   with `httpx`, seeds a BFS queue from the sitemap (or the homepage if
   there isn't one), then for each page: launches a Playwright page,
   auto-scrolls to trigger lazy-loaded images, reads `page.content()`,
   extracts image URLs (`<img>`, `<picture><source>`, CSS
   `background-image` via a JS `getComputedStyle` pass, `og:image`,
   `twitter:image`) and internal links, and hands each image URL to
   `fetch.fetch_and_store()`. Every visited page is marked `done` in
   `pages` so a second `crawl` run skips it (`--no-resume` disables this).
3. **Fetch.** For each image URL not already in `site_images` with hashes
   set, `fetch.fetch_and_store()` downloads the bytes, decodes with
   Pillow, drops anything smaller than `min_image_side_px` on its shortest
   side, writes the bytes to `data/site_images/<sha256 prefix>/<sha256>.<ext>`,
   computes hashes/embedding, and upserts the row. It always links the
   image to the current page in `image_pages`, even if the image itself was
   already cached from an earlier page (an image can appear on more than
   one page).
4. **Match.** `match.run_matching()` clears the `matches` table and, for
   every `(reference, site_image)` pair, calls `classify_pair()`: level 1
   first (Hamming distance on both hashes, best of the two if either is
   under threshold), then level 2 only if level 1 found nothing. Any hit is
   upserted into `matches` with its level, score, and confidence.
5. **Report.** `report.build_rows()` joins `matches` with `reference_images`
   and `site_images`, computes `days_left`/`status` (`expire`, `<30j`,
   `<90j`, `ok`, `inconnue`) from `expiry_date`, drops anything with more
   than `--within-days` days left, sorts by urgency (expired/soonest first,
   unknown-expiry last), and renders `report.html` + `matches.csv`.
   Thumbnails are inlined as base64 JPEG data URIs so `report.html` opens
   standalone, with no relative file dependencies.

## What's deliberately out of the MVP

See the project brief (top of the original issue) for the full list; in
short: no web UI, no multi-tenant support, no video, no open-web search, no
"site images absent from the DAM" detection (needs the full DAM), no
scheduled crawls/alerts, and no multi-site runs in one invocation. The
`RefSource` interface and the `config.yaml`-driven thresholds are the two
seams meant to make phase 2 additive rather than a rewrite.
