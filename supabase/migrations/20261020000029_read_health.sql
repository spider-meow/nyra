-- Health of a read: figures worked out when a full read of a site ends, and whether it looks suspect (a read that kept
-- far fewer of the images already known, or met many errors). A suspect read never makes a page "gone" nor a visual
-- "replaced": see backend/nyra/cloud/presence.py and `crawl.suspect_reason`.
--
--   crawl_runs.pages_ok            pages read with success (HTTP 2xx, the page asked for, no consent wall)
--   crawl_runs.error_share         share of the pages read that failed (403, 429, 5xx, timeout, consent wall), 0 to 1
--   crawl_runs.images_seen         images of the site this read saw (new ones included)
--   crawl_runs.images_known_seen   among them, the images already known before the read
--   crawl_runs.suspect             the read looks incomplete (default false: older reads are not doubted)
--   crawl_runs.suspect_reason      why, in French, for the interface and the job
--
-- Additive: safe to apply before deploying the code that uses it. Older reads have no figures and are not suspect.

alter table public.crawl_runs
    add column if not exists pages_ok integer,
    add column if not exists error_share real,
    add column if not exists images_seen integer,
    add column if not exists images_known_seen integer,
    add column if not exists suspect boolean not null default false,
    add column if not exists suspect_reason text;
