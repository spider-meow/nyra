# Database schema

RightsWatch stores everything in a single SQLite file (default
`rightswatch.db`, override with `--db`). The full schema lives in
`rightswatch/db.py::SCHEMA` — this document explains what each table is for
and how the tables relate.

## Entity relationship overview

```
reference_images                    site_images
+----+----------+      matches      +----+-----+
| id | filename |<---+          +-->| id | url |
+----+----------+    |          |   +----+-----+
                      |          |
                 +----+----------+----+
                 | reference_id       |
                 | site_image_id      |
                 | level              |
                 | score              |
                 | confidence         |
                 +--------------------+

site_images                pages
+----+-----+           +----+-----+
| id | url |           | id | url |
+----+-----+           +----+-----+
     ^                      ^
     |                      |
     +------ image_pages ---+
           (image_id, page_id)
```

## Tables

### `reference_images`

The rights-managed library ingested from `refs.csv` + the images folder.
One row per `filename` (unique) — re-ingesting updates the row in place.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `filename` | TEXT, UNIQUE | The natural key. Matches the `filename` column in `refs.csv`. |
| `path` | TEXT | Absolute path to the source image on disk at ingest time. |
| `expiry_date` | TEXT | ISO 8601 `YYYY-MM-DD`, or `NULL` if unknown. Drives report urgency. |
| `credit` | TEXT | Free text, shown in the report. |
| `notes` | TEXT | Free text, shown in the report. |
| `phash` | TEXT | Hex-encoded perceptual hash (`imagehash.phash`). |
| `dhash` | TEXT | Hex-encoded difference hash (`imagehash.dhash`). |
| `embedding` | BLOB | CLIP embedding, packed as raw `float32` bytes via `db.pack_embedding`. `NULL` if ingested with `--no-embeddings`. |
| `width`, `height` | INTEGER | Source image dimensions in pixels. |
| `created_at`, `updated_at` | TEXT | ISO 8601 UTC timestamps. |

### `pages`

Every URL the crawler has discovered, whether or not it's been visited yet.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `url` | TEXT, UNIQUE | Fragment-stripped (see `crawl.normalize_url`). |
| `status` | TEXT | `'pending'` until crawled, then `'done'`. Resumable crawls skip `'done'` pages. |
| `http_status` | INTEGER | Response status code, or `0` if the page load raised an exception. |
| `crawled_at` | TEXT | Set when `status` becomes `'done'`. |

### `site_images`

Every distinct image URL found on the crawled site, past the
`min_image_side_px` size filter. One row per `url` (unique) — an image
served at two different URLs (e.g. two CDN variants) gets two rows, each
matched independently against the reference library.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `url` | TEXT, UNIQUE | Absolute URL, resolved against the page it was found on. |
| `local_path` | TEXT | Where the downloaded bytes are cached (`data/site_images/<sha256[:2]>/<sha256>.<ext>`). |
| `content_hash` | TEXT | SHA-256 of the downloaded bytes. Used as the cache filename; two URLs serving byte-identical content still get separate cache files today (cheap to dedupe further later if needed). |
| `width`, `height` | INTEGER | Pixel dimensions as decoded. |
| `phash`, `dhash` | TEXT | Same hashing as `reference_images`. |
| `embedding` | BLOB | CLIP embedding, `NULL` if crawled with `--no-embeddings`. |
| `first_seen`, `last_seen` | TEXT | `first_seen` is set once; `last_seen` updates whenever the URL is touched again (a re-crawl doesn't re-download if hashes are already set, but does update this). |

### `image_pages`

Many-to-many join: the same image can legitimately appear on several pages
(a hero image reused across a category and a product page, for instance).
Composite primary key `(image_id, page_id)` makes re-linking a no-op.

| Column | Type | Notes |
|---|---|---|
| `image_id` | INTEGER, FK -> `site_images.id` | `ON DELETE CASCADE` |
| `page_id` | INTEGER, FK -> `pages.id` | `ON DELETE CASCADE` |

### `matches`

Written by `match.run_matching()`, which **clears this table first** on
every run — it's a derived table, always fully recomputed from the current
`reference_images` and `site_images` rows and the current `config.yaml`
thresholds. Unique on `(reference_id, site_image_id)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `reference_id` | INTEGER, FK -> `reference_images.id` | `ON DELETE CASCADE` |
| `site_image_id` | INTEGER, FK -> `site_images.id` | `ON DELETE CASCADE` |
| `level` | TEXT | `"phash"`, `"dhash"`, or `"clip"` — whichever produced the match (see `MATCHING.md`). |
| `score` | REAL | `1 - distance/64` for hash matches, cosine similarity (0-1) for CLIP matches. Higher is a closer match either way. |
| `confidence` | TEXT | `"haut"`, `"moyen"`, or `"a_verifier"`. |
| `created_at` | TEXT | ISO 8601 UTC timestamp of the match run. |

## Idempotency and resumability

Every writer in `db.py` is an `INSERT ... ON CONFLICT DO UPDATE` (or `DO
NOTHING` for `pages`), keyed on a natural key rather than the autoincrement
`id`. In practice this means:

- Re-running `ingest-refs` with an updated `refs.csv` updates existing
  reference rows in place (e.g. a corrected `expiry_date`) without touching
  `matches` — you still need to re-run `match` after.
- Re-running `crawl` skips pages already marked `done` (`pages.status`),
  so an interrupted 300-page crawl can be safely resumed from where it left
  off, or extended with `--max-pages` for pages the previous run didn't
  reach. Pass `--no-resume` to force a full re-crawl.
- Re-running `match` always fully recomputes `matches` from scratch — this
  is intentional, since a threshold change in `config.yaml` (or a
  `calibrate` result you've applied) should be reflected immediately without
  stale rows from a previous threshold lingering.
- Re-running `report` never touches the database; it only reads.

## Inspecting the database directly

```bash
sqlite3 rightswatch.db
.tables
.schema matches
SELECT r.filename, r.expiry_date, COUNT(*) FROM matches m
  JOIN reference_images r ON r.id = m.reference_id GROUP BY r.id;
```
