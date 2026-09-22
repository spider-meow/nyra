# Database schema

Nyra stores everything in a single SQLite file (default
`nyra.db`, override with `--db`). The full schema lives in
`backend/nyra/db.py::SCHEMA` — this document explains what each table is for
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

Written by `match.run_matching()`. Rows aren't unconditionally cleared on
every run any more (see "Idempotency and resumability" below for the
incremental-matching cache) — a full recompute (threshold change) still
clears and rebuilds the table, but the point of the cache is that most
runs only touch the delta. Unique on `(reference_id, site_image_id)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Referenced by `nyra review <id>` / `nyra exclude --from-match <id>` and the API's `PATCH /api/matches/{id}`. |
| `reference_id` | INTEGER, FK -> `reference_images.id` | `ON DELETE CASCADE` |
| `site_image_id` | INTEGER, FK -> `site_images.id` | `ON DELETE CASCADE` |
| `level` | TEXT | `"phash"`, `"dhash"`, or `"clip"` — whichever produced the match (see `MATCHING.md`). |
| `score` | REAL | `1 - distance/64` for hash matches, cosine similarity (0-1) for CLIP matches. Higher is a closer match either way. |
| `confidence` | TEXT | `"haut"`, `"moyen"`, or `"a_verifier"`. |
| `status` | TEXT | `"pending"` (default), `"confirmed"`, or `"rejected"` — set via `nyra review`/`PATCH /api/matches/{id}`. `report`/`nyra report --status` filter on this; a re-match never resets it (only `level`/`score`/`confidence`/`created_at` are touched on conflict). |
| `reviewed_at` | TEXT | ISO 8601 UTC timestamp of the last status change, `NULL` while `pending`. |
| `reviewed_note` | TEXT | Free-text note attached to the last status change. |
| `created_at` | TEXT | ISO 8601 UTC timestamp of the match run. |

### `reviews`

A lighter-weight, UI-driven per-(reference, site image) decision —
"retenu"/"ecarte"/"traite" — separate from `matches.status` above.
Predates it and is still what the results screen's "Retenir"/"Écarter"/
"Traité" buttons write to; `matches.status` is the newer, more formal
traceability layer (with a note and a timestamp) driven by `nyra review`
and the comparison panel's "Confirmer"/"Rejeter". Both can coexist on the
same match; neither is derived from the other.

| Column | Type | Notes |
|---|---|---|
| `reference_id` | INTEGER | Part of the composite PK, not a declared FK (matches `matches.reference_id`). |
| `site_image_id` | INTEGER | Part of the composite PK. |
| `decision` | TEXT | `"retenu"`, `"ecarte"`, or `"traite"`. |
| `updated_at` | TEXT | ISO 8601 UTC timestamp. |

### `excluded_hashes`

Recurring false positives (a generic logo, a stock asset reused across a
site) to permanently ignore. Populated by `nyra exclude --from-match` /
`POST /api/exclude`, which also purges any `matches` rows the hash already
produced. Checked by `match._excluded_site_ids()` before a site image's
hits are ever written — the pair never reaches `matches` on future runs
either, not just the current report.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `hash` | TEXT | The excluded pHash or dHash (hex, same format as `reference_images.phash`/`dhash`). |
| `hash_type` | TEXT | `"phash"` or `"dhash"` — which column on `site_images` this is compared against. |
| `reason` | TEXT | Free-text, e.g. `"logo générique du site"`. |
| `created_at` | TEXT | ISO 8601 UTC timestamp. |

Unique on `(hash, hash_type)`. A site image is excluded if its hash is
within the *usual* matching threshold (`config.yaml`'s
`phash_threshold`/`dhash_threshold`) of any row here — not just an exact
hex match — so a slightly re-encoded copy of the same excluded logo is
still caught.

### `match_meta`

One row (`id = 1`), used by `run_matching()`'s incremental-matching cache
(see below) to detect when `config.yaml`'s thresholds changed and a full
recompute is needed instead of comparing only the delta.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Always `1` — enforced by a `CHECK` constraint, not a real key. |
| `signature` | TEXT | Derived from the active thresholds + whether CLIP was used (`match._signature()`). |
| `finished_at` | TEXT | ISO 8601 UTC timestamp of the last completed `run_matching()` call. |

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
- Re-running `match` only recomputes what's new since the last run
  (references/site images not yet stamped `compared_at`, tracked via
  `match_meta`'s signature) — unless `config.yaml`'s thresholds (or the
  `--no-clip` switch) changed since, in which case it's a full recompute,
  so a threshold change (or a `calibrate` result you've applied) is always
  reflected immediately without stale rows lingering. Either way,
  `matches.status`/`reviewed_at`/`reviewed_note` on a surviving row are
  never touched — a rematch doesn't undo someone's review.
- `nyra exclude` immediately removes matches for the newly-excluded hash
  (it doesn't wait for the next `match` run), and future `match` runs skip
  that hash before writing anything.
- Re-running `report` never touches the database; it only reads.

## Inspecting the database directly

```bash
sqlite3 nyra.db
.tables
.schema matches
SELECT r.filename, r.expiry_date, COUNT(*) FROM matches m
  JOIN reference_images r ON r.id = m.reference_id GROUP BY r.id;
```
