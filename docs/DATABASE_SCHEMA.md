# Database schema

The hosted product uses Postgres (Supabase); the source of truth is
`supabase/migrations/`. The CLI uses a SQLite file with the same core
tables (`backend/nyra/db.py::SCHEMA`) minus the multi-tenant ones.

## Tenancy

Every table carries `org_id`. `memberships` ties Supabase Auth users to
organizations with a role (`admin` or `client`). The backend connects
with a privileged role and filters on `org_id` in every query; Row Level
Security policies (`..._row_level_security.sql`) guard any other access
path. Storage objects live under `{org_id}/...` in three private buckets
(`refs`, `site-images`, `reports`), thumbnails under `{org_id}/thumbs/`.

## Tables

| Table | Purpose | Key columns |
|---|---|---|
| `organizations` | Clients | `slug` (lowercase, hyphens, unique) |
| `memberships` | User ↔ organization | `role` ∈ {admin, client} |
| `org_settings` | Per-organization overrides of `config.yaml` | `overrides` jsonb (whitelisted keys only) |
| `sites` | Sites an organization has crawled | `url` (canonical) |
| `reference_images` | The library | `filename` (unique per org), `expiry_date`, `phash`, `dhash`, `phash_flip`, `dhash_flip`, `embedding vector(512)`, `storage_path`, `thumb_path`, `compared_at` |
| `pages` | Pages discovered | `url` (canonical, unique per site), `status` pending/done |
| `site_images` | Images found | `url` (unique per site), `content_hash` (indexed; same bytes are reused), hashes, `embedding`, `storage_path`, `thumb_path`, `compared_at` |
| `image_pages` | Image ↔ page | composite key |
| `matches` | Reference ↔ site image hits | `level` (phash/dhash/clip), `score`, `confidence` (haut/moyen/a_verifier) |
| `reviews` | Decisions | `decision` ∈ {retenu = to remove, ecarte = false positive, traite = removed}, `reviewed_by` |
| `match_meta` | Signature of the last match pass | thresholds + model; a change forces a full recompute |
| `jobs` | The work queue | `kind`, `status` (queued/running/done/error/cancelled), `params`, `progress`, `message`, `result`, `error`, `cancel_requested`, `heartbeat_at` |
| `crawl_runs` | Crawl history | counters, `errors`, `job_id` |
| `reports` | Generated reports | Storage paths of the three files, `stats` |

### The job queue

- `jobs_one_running_per_org` (partial unique index on `org_id` where
  `status = 'running'`) guarantees one running job per organization.
- Claiming is `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`.
- Enqueuing takes a per-organization advisory lock, refuses a second
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
