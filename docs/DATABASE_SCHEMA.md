# Database schema

The hosted product uses Postgres (Supabase); the source of truth is
`supabase/migrations/`. The CLI uses a SQLite file with the same core
tables (`backend/nyra/db.py::SCHEMA`) minus the multi-tenant ones.

## Tenancy

Every table carries `org_id`. `memberships` ties Supabase Auth users to
organizations with a role (`admin` or `client`); a person can belong to
several organizations. The backend connects with a privileged role and
filters on `org_id` in every query (or on a `brand_id` it has checked
belongs to the organization); Row Level Security policies
(`..._row_level_security.sql`) guard any other access path.

An organization holds **brands** (Rémy Martin, Louis XIII...). A brand
owns its library, its sites (one per market: US, INT, UK, FR) and
everything derived from them: its references are only compared with
images crawled on its own sites. Exclusions, reports, the match
signature and the job queue are per brand; settings and memberships stay
per organization. Tables that belong to one brand carry `brand_id`, with
a composite foreign key `(brand_id, org_id)` so a row can't point at
another organization's brand; pages, site images, matches, reviews and
crawl runs reach their brand through `sites` or `reference_images`.
Deleting a brand cascades to everything it owns.

Storage objects live under `{org_id}/...` in three private buckets
(`refs`, `site-images`, `reports`). References under
`{org_id}/{brand_id}/`: the original, its thumbnail under `thumbs/` and a
working copy (JPEG, 1024 px at most, `work_path`) under `work/` that
indexing, the keypoint check and the comparison screen read instead of the
original. References uploaded before brands keep their `{org_id}/` path
until re-uploaded. Crawled images are stored once per organization, keyed
by content hash, and only as a working copy (`{org_id}/work/`) and a
thumbnail (`{org_id}/thumbs/`): the original is on the site. Rows crawled
before September 26, 2026 still point at their original.

## Tables

| Table | Purpose | Key columns |
|---|---|---|
| `organizations` | Clients | `slug` (lowercase, hyphens, unique) |
| `memberships` | User ↔ organization | `role` ∈ {admin, client} |
| `brands` | Brands of an organization | `slug` (unique per org) |
| `org_settings` | Per-organization overrides of `config.yaml`, shared by its brands | `overrides` jsonb (whitelisted keys only) |
| `sites` | A brand's addresses | `brand_id`, `url` (canonical, unique per org: an address belongs to one brand), `label` (US, FR...) |
| `reference_images` | A brand's library | `brand_id`, `filename` (unique per brand), `expiry_date`, `unlimited_rights` (never expires; excludes a date: a check forbids both), `tags text[]` (free labels, normalized by the API, GIN-indexed; never read by matching), `phash`, `dhash`, `phash_flip`, `dhash_flip`, `embedding vector(512)`, `storage_path`, `thumb_path`, `compared_at` |
| `pages` | Pages discovered | `url` (canonical, unique per site), `status` pending/done |
| `site_images` | Images found | `url` (unique per site), `content_hash` (indexed; same bytes are reused), hashes, `embedding`, `storage_path`, `thumb_path`, `compared_at`, `variant_group` (shared by the crops and resizes of one photo, set by the worker after each comparison; NULL = no twin; never read by matching), `variants_checked_at` (incremental marker: only images where it is NULL are searched for twins) |
| `image_pages` | Image ↔ page | composite key |
| `matches` | Reference ↔ site image hits | `level` (phash/dhash/clip), `score`, `confidence` (haut/moyen/a_verifier) |
| `reference_locations` | Site images found to be a copy or crop of a reference by the `locate` job (replaced at each search) | `reference_id`, `site_image_id`, `tier` (same/review), `inliers`, `ref_coverage`, `site_coverage`; `reference_images.located_at` = last search |
| `labels` | A brand's labels: the four types of site image (logo, packshot, pictogramme, autre) and its content labels (carafe…) | `brand_id`, `kind` (type/content), `name` (unique per brand and kind) |
| `image_labels` | What a person decided about an image and a label (the examples the algorithm learns from) | `label_id`, `site_image_id` or `reference_id`, `positive` (false = not this); never read by matching |
| `reviews` | Decisions | `decision` ∈ {retenu = to remove, ecarte = false positive, traite = removed}, `reviewed_by` |
| `excluded_hashes` | Recurring false positives never matched again, per brand | `brand_id`, `hash`, `hash_type` (phash/dhash), `group_id` (one exclusion = both rows), `reason`, `thumb_path` |
| `match_meta` | Signature of a brand's last match pass | `brand_id` (key), thresholds + model; a change forces a full recompute |
| `jobs` | The work queue | `brand_id`, `kind`, `status` (queued/running/done/error/cancelled), `params` (a crawl lists `site_ids`), `progress`, `message`, `result`, `error`, `cancel_requested`, `heartbeat_at` |
| `crawl_runs` | Crawl history, one per address read | counters, `errors`, `job_id` |
| `reports` | Generated reports, per brand | `brand_id`, Storage paths of the three files, `stats` |
| `platform_staff` | Nyra team members who see the back office | `user_id` (service role only, no RLS policy) |

`crawl_runs.metrics` (jsonb) holds each crawl's timings and byte counts.
`site_images` keeps `byte_size` (weight on the site), `format` (the
original's: only a JPEG working copy is stored) and `stored_bytes`
(working copy + thumbnail); `reference_images.byte_size` is the uploaded
original's weight.

### The `insights` schema

Read-only views for the statistics pages and Grafana (see
`OBSERVABILITY.md`), one row per brand, run or job: `crawl_runs`
(measurements flattened, rates derived), `jobs` (wait and run time),
`site_files`, `site_images` and `site_image_formats` (weight, dimensions,
formats, counted once per distinct file), `library`, `matching`
(decisions and false-positive rate per confidence and level). Not exposed
through the Supabase API; grant a monitoring role access to this schema
only.

### The job queue

- `jobs_one_running_per_brand` (partial unique index on `brand_id` where
  `status = 'running'`) guarantees one running job per brand; two brands
  of one organization run side by side.
- Claiming is `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`.
- Enqueuing takes a per-brand advisory lock, refuses a second
  crawl/match/report of the same kind, and merges `index` requests.
- A running job whose `heartbeat_at` is older than 3 minutes is marked
  `error` by the next worker, and its crawl run too.

## Idempotency and incremental matching

- Writers upsert on natural keys, so re-running ingest or crawl updates in
  place.
- `compared_at` is reset when an image's hashes or embedding change; the
  next match pass compares new references against everything and older
  references against new site images only.
- A match pass writes all its results in one transaction at the end: a
  stop or a crash leaves the previous results intact.

## SQLite (CLI)

`reference_images`, `pages`, `site_images`, `image_pages`, `matches`,
`reviews`, `match_meta`, with integer ids and local file paths instead of
Storage paths. Inspect with `sqlite3 nyra.db`.
