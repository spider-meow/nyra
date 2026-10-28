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
signature are per brand (jobs carry a brand, but only one runs per organization); settings and memberships stay
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
| `organizations` | Clients | `slug` (lowercase, hyphens, unique), `quota_overrides` (jsonb: the limits the Nyra team changed for this organization, keys of `QuotaConfig`; empty = the defaults of `config.yaml`; written by the back office only) |
| `memberships` | User ↔ organization | `role` ∈ {admin, client}; `monthly_email_allowed` (the Nyra team's switch) and `monthly_email_wanted` (the person's), both `true` by default: the monthly e-mail goes to an administrator with both on; a trigger keeps the public API from changing them |
| `brands` | Brands of an organization | `slug` (unique per org), `created_by` (the platform admin who created it: the billing record), `set_aside_logos` (setting, off by default: the site images recognised as logos stay out of the comparison and out of "Droits non vérifiés") |
| `brand_logos` | A brand's logo, shrunk by the API to a 128 px PNG and kept as a data URI (it travels with the list of brands) | `brand_id` (primary key, deleted with the brand), `data_uri` |
| `usage_counters` | What an organization used: `kind` ∈ {pages_read, images_new, bytes_downloaded} per month (`period` = first day of the month) and `emails` (invitations) per day; added to by the worker (crawls and page checks, at most a few seconds late) and the API, read by the cap, the client's Usage page and the back office | `org_id` + `period` + `kind` (key), `amount` |
| `org_settings` | Per-organization overrides of `config.yaml`, shared by its brands | `overrides` jsonb (whitelisted keys only) |
| `sites` | A brand's addresses | `brand_id`, `url` (canonical, unique per org: an address belongs to one brand), `label` (US, FR...) |
| `reference_images` | A brand's library | `brand_id`, `filename` (unique per brand), `expiry_date`, `unlimited_rights` (never expires; excludes a date: a check forbids both), `collection_id` (its folder, null = unfiled), `tags text[]` (free labels, normalized by the API, GIN-indexed; never read by matching), `phash`, `dhash`, `phash_flip`, `dhash_flip`, `embedding vector(512)`, `storage_path`, `thumb_path`, `compared_at` |
| `collections` | Folders of a brand's library (a shoot, a season), 3 levels at most (the API checks) | `brand_id`, `parent_id` (null = top level), `name` (unique among siblings, case-insensitive). Deleting one deletes its sub-collections and un-files their references |
| `pages` | Pages discovered | `url` (canonical, unique per site), `status` pending/done, `crawled_at`, `links_tracked` (the last read succeeded, HTTP 2xx, and stamped the page's links), `read_started_at` (when that read started: the links it stamped are at or after it) |
| `site_images` | Images found | `url` (unique per site), `content_hash` (indexed; same bytes are reused), hashes, `embedding`, `storage_path`, `thumb_path`, `compared_at`, `variant_group` (shared by the crops and resizes of one photo, set by the worker after each comparison; NULL = no twin; never read by matching), `variants_checked_at` (incremental marker: only images where it is NULL are searched for twins), `logo_score` (probability that the whole image is a logo, from its embedding; NULL until scored), `logo_kept` (a person put it back into the comparison) |
| `image_pages` | Image ↔ page | composite key, `last_seen` (the last read that saw the image on the page) |
| `page_replacements` | Pages a person marked as replaced, for one visual | `reference_id` + `page_id` (key), `org_id`, `marked_by`, `marked_at` |
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
| `brand_schedules` | The monthly read of a brand (off by default) | `brand_id` (key; composite foreign key `(brand_id, org_id)` to `brands`, deleted with the brand), `org_id`, `enabled`, `day_of_month` (1 to 28), `last_run_at` (when the worker last queued the read: once a month at most), `created_at`, `updated_at`. RLS: read by the organization's members; written by the backend (service role) |
| `monthly_digests` | The monthly e-mail of an organization: one row per organization and month, so it leaves once | `org_id` (deleted with the organization), `month` (first day; key with `org_id`), `status` (`sending`, `sent`, `skipped`), `attempts` (claims this month, 5 at most), `sent_at`, `created_at`, `updated_at` (last claim: a `sending` one is taken again after 30 minutes). No address, no file name. RLS: read by the organization's administrators; written by the backend (service role) |
| `rate_buckets` | The rate limit's token buckets when `NYRA_RATE_BACKEND=database` (AXE-586/587): one row per person and kind, shared by every web process | `identity` (account id, never an e-mail or a token), `kind` (`read`/`write`; key with `identity`), `tokens`, `seen`, `granted`. UNLOGGED, RLS on with no policy, no access for `anon`/`authenticated`; `take_rate_token(identity, kind, capacity, now)` takes a token in one statement, `purge_rate_buckets()` drops buckets idle for 2 minutes |
| `audit_log` | What people did, per organization: one row per action that changes something | `org_id` (deleted with the organization; null for the Nyra team's own `staff.add` / `staff.remove`), `brand_id` (no foreign key: survives the brand), `user_id` (set to null if the Auth account is deleted), `action` (stable code: `library.delete`, `exclusion.add`, `member.remove`, `job.crawl`…), `object_type` + `object_id`, `detail` jsonb (counters and codes only, never content; 2 KB at most), `created_at`, `by_staff` (true when the author was a platform admin who is not a member of the organization: the organization's log and the read policy leave the row out, the back office shows it). Indexes `(org_id, created_at desc)` and `(created_at)` (the purge). RLS: read by the organization's admins, rows `by_staff` excepted; written by the backend, in the transaction of the change it traces. Kept 12 months (`audit.purge`) |
| `deleted_organizations` | The trace of a complete erasure (`nyra cloud-delete-org`); outlives the organization | `org_id` (no foreign key), `name`, `slug`, `deleted_at`, `operator`, `report` jsonb (row and file counts: no content, no e-mail; ids of the accounts to delete). Service role only, no RLS policy. Retention: to be decided by the team (nothing purges it) |

`crawl_runs.metrics` (jsonb) holds each crawl's timings and byte counts.
`crawl_runs` also keeps the health of a full read (`pages_ok`, `error_share`,
`images_seen`, `images_known_seen`) and `suspect` /
`suspect_reason`: set when it ends if the read kept under 70 % of the images
known before, or met too many errors (see `MATCHING.md`). Reads made before
the migration have no figures and are not suspect. A page whose
`pages.http_status` is -1 met a consent wall that did not go away: it is never
"gone". (The monthly summary, AXE-523, will have to say when a read is suspect.)
`site_images` keeps `byte_size` (weight on the site), `format` (the
original's: only a JPEG working copy is stored) and `stored_bytes`
(working copy + thumbnail); `reference_images.byte_size` is the uploaded
original's weight.

### Erasing an organization

Every table holding an organization's data reaches `organizations` through `on delete cascade`: directly by an
`org_id` column, or through its parent (`image_pages` through `pages`, `brand_logos` through `brands`). So
`delete from organizations where id = …` removes every row, which `nyra cloud-delete-org` does after the Storage
files. The tables that are not an organization's are `platform_staff`, `service_metrics`, `service_metrics_history`, `rate_buckets` and
`deleted_organizations`. `backend/tests/test_offboarding.py` reads the catalog: a new table must carry an
`org_id` with the cascade (or be listed in `offboarding.INDIRECT` / `GLOBAL`), and no foreign key may be
`no action`/`restrict`, or the test fails. Foreign keys to `auth.users` are `set null` (or `cascade` for
`memberships` and `platform_staff`): deleting an account never blocks.

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

- `jobs_one_running_per_org` (partial unique index on `org_id` where
  `status = 'running'`, migration 032) guarantees one running job per
  organization: the brands of an organization take turns (the per-brand
  index of 013 is dropped). The migration stops with a clear message if an
  organization has two running jobs when it is applied: apply it when no
  job runs (see DEPLOYMENT.md for the order). `jobs_org_active_idx` (partial,
  `org_id` where queued or running) serves `jobs/current`.
- Claiming is `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`.
- Enqueuing takes a per-organization advisory lock. A job started by hand is
  refused while another job but an `index` waits or runs in the organization;
  what follows an action only refuses a second job of the same kind waiting
  in the brand. `index` requests merge.
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
