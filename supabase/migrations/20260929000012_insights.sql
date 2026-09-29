-- Insights: what the pipeline measures about itself, and who at Nyra may
-- see every organization's numbers.
--
-- 1. Measurements. `crawl_runs.metrics` holds the crawler's timings and
--    byte counts (see `CrawlStats.metrics()` in backend/nyra/crawl.py);
--    `byte_size` records the weight of every stored image. Rows created
--    before this migration have no measurements: the views below leave
--    them out of averages rather than counting them as zero.
--
-- 2. Platform staff. Organization roles (admin/client) only reach one
--    organization. `platform_staff` lists the Nyra team members who see
--    the cross-organization back office (`/interne`). Only the backend
--    (service role) reads it: RLS is on and no policy is defined.
--
-- 3. The `insights` schema. Read-only views, one per question, used by the
--    API's statistics pages and meant for Grafana. Grant a monitoring role
--    USAGE on this schema and SELECT on its views, nothing else (see
--    docs/OBSERVABILITY.md): it never sees embeddings, credentials or
--    reference files. The schema is not exposed through the Supabase API,
--    and anon/authenticated get no access to it.

alter table public.crawl_runs
    add column if not exists metrics jsonb not null default '{}'::jsonb;

alter table public.site_images
    add column if not exists byte_size bigint;

alter table public.reference_images
    add column if not exists byte_size bigint;

create table if not exists public.platform_staff (
    user_id uuid primary key references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);

alter table public.platform_staff enable row level security;

create schema if not exists insights;
revoke all on schema insights from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on schema insights from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on schema insights from authenticated';
    end if;
end $$;

-- One row per crawl, measurements flattened, rates derived. Rates are NULL
-- when the run has no measurement to derive them from.
create or replace view insights.crawl_runs as
select
    c.id,
    c.org_id,
    o.name as org_name,
    c.site_id,
    s.url as site_url,
    c.status,
    c.started_at,
    c.finished_at,
    coalesce(
        nullif((c.metrics ->> 'duration_seconds')::double precision, 0),
        extract(epoch from (c.finished_at - c.started_at))
    ) as duration_seconds,
    c.pages_visited,
    c.images_found,
    c.images_stored,
    c.images_new,
    c.blocked_by_robots,
    jsonb_array_length(c.errors) as error_count,
    (c.metrics ->> 'sitemap_urls')::integer as sitemap_urls,
    (c.metrics ->> 'pages_failed')::integer as pages_failed,
    (c.metrics ->> 'images_known')::integer as images_known,
    (c.metrics ->> 'images_duplicate')::integer as images_duplicate,
    (c.metrics ->> 'images_rejected')::integer as images_rejected,
    (c.metrics ->> 'downloads')::integer as downloads,
    (c.metrics ->> 'downloads_failed')::integer as downloads_failed,
    (c.metrics ->> 'bytes_downloaded')::bigint as bytes_downloaded,
    (c.metrics ->> 'bytes_new')::bigint as bytes_new,
    (c.metrics ->> 'thumb_bytes_new')::bigint as thumb_bytes_new,
    (c.metrics ->> 'pixels_new')::bigint as pixels_new,
    (c.metrics ->> 'discover_seconds')::double precision as discover_seconds,
    (c.metrics ->> 'render_seconds')::double precision as render_seconds,
    (c.metrics ->> 'download_seconds')::double precision as download_seconds,
    (c.metrics ->> 'process_seconds')::double precision as process_seconds,
    (c.metrics ->> 'embed_seconds')::double precision as embed_seconds,
    (c.metrics ->> 'embedded')::integer as embedded,
    (c.metrics ->> 'store_seconds')::double precision as store_seconds,
    c.metrics -> 'http_statuses' as http_statuses,
    c.metrics -> 'formats_new' as formats_new,
    -- Derived rates.
    c.pages_visited / nullif(coalesce(
        nullif((c.metrics ->> 'duration_seconds')::double precision, 0),
        extract(epoch from (c.finished_at - c.started_at))
    ), 0) * 60 as pages_per_minute,
    c.images_found / nullif(coalesce(
        nullif((c.metrics ->> 'duration_seconds')::double precision, 0),
        extract(epoch from (c.finished_at - c.started_at))
    ), 0) as images_scanned_per_second,
    (c.metrics ->> 'embedded')::double precision
        / nullif((c.metrics ->> 'embed_seconds')::double precision, 0) as clip_images_per_second,
    (c.metrics ->> 'render_seconds')::double precision / nullif(c.pages_visited, 0) as seconds_per_page,
    (c.metrics ->> 'bytes_new')::double precision / nullif(c.images_new, 0) as avg_new_image_bytes,
    c.images_found::double precision / nullif(c.pages_visited, 0) as images_per_page
from public.crawl_runs c
join public.organizations o on o.id = c.org_id
join public.sites s on s.id = c.site_id;

-- One row per job: time waiting in the queue, time running.
create or replace view insights.jobs as
select
    j.id,
    j.org_id,
    o.name as org_name,
    j.kind,
    j.status,
    j.created_at,
    j.started_at,
    j.finished_at,
    extract(epoch from (j.started_at - j.created_at)) as wait_seconds,
    extract(epoch from (j.finished_at - j.started_at)) as run_seconds,
    j.error,
    j.result -> 'match_metrics' as match_metrics,
    j.result -> 'metrics' as metrics
from public.jobs j
join public.organizations o on o.id = j.org_id;

-- What the site images look like, per organization. Bytes and pixels are
-- counted once per distinct file (the same bytes under several URLs are
-- stored once).
create or replace view insights.site_images as
with files as (
    select distinct on (org_id, coalesce(content_hash, id::text))
        org_id, byte_size, width, height,
        lower(coalesce(nullif(substring(storage_path from '\.([A-Za-z0-9]+)$'), ''), 'autre')) as format
    from public.site_images
    where storage_path is not null
    order by org_id, coalesce(content_hash, id::text), byte_size desc nulls last
)
select
    o.id as org_id,
    o.name as org_name,
    (select count(*) from public.site_images i where i.org_id = o.id) as image_urls,
    count(f.org_id) as distinct_files,
    count(f.byte_size) as files_with_size,
    sum(f.byte_size) as total_bytes,
    avg(f.byte_size) as avg_bytes,
    percentile_cont(0.5) within group (order by f.byte_size) as median_bytes,
    max(f.byte_size) as max_bytes,
    avg(f.width) as avg_width,
    avg(f.height) as avg_height,
    avg(f.width::double precision * f.height) / 1e6 as avg_megapixels,
    (select count(*) from public.pages p where p.org_id = o.id and p.status = 'done') as pages_read,
    (select count(*) from public.image_pages ip join public.pages p on p.id = ip.page_id where p.org_id = o.id)
        as image_page_links
from public.organizations o
left join files f on f.org_id = o.id
group by o.id, o.name;

create or replace view insights.site_image_formats as
with files as (
    select distinct on (org_id, coalesce(content_hash, id::text))
        org_id, byte_size,
        lower(coalesce(nullif(substring(storage_path from '\.([A-Za-z0-9]+)$'), ''), 'autre')) as format
    from public.site_images
    where storage_path is not null
    order by org_id, coalesce(content_hash, id::text), byte_size desc nulls last
)
select org_id, format, count(*) as files, sum(byte_size) as total_bytes, avg(byte_size) as avg_bytes
from files
group by org_id, format;

-- The reference library, per organization.
create or replace view insights.library as
select
    o.id as org_id,
    o.name as org_name,
    count(r.id) as references_total,
    count(r.embedding) as references_indexed,
    count(r.id) filter (where r.expiry_date < current_date) as expired,
    count(r.id) filter (where r.expiry_date >= current_date and r.expiry_date < current_date + 30) as expiring_30_days,
    count(r.id) filter (where r.expiry_date >= current_date and r.expiry_date < current_date + 90) as expiring_90_days,
    count(r.id) filter (where r.expiry_date is null) as without_expiry,
    sum(r.byte_size) as total_bytes,
    avg(r.byte_size) as avg_bytes,
    avg(r.width::double precision * r.height) / 1e6 as avg_megapixels
from public.organizations o
left join public.reference_images r on r.org_id = o.id
group by o.id, o.name;

-- Matches and the decisions people took on them, per organization,
-- confidence band and level. `false_positive_rate` is the share of
-- reviewed matches marked "écarté": a precision proxy per band.
create or replace view insights.matching as
select
    m.org_id,
    m.confidence,
    m.level,
    count(*) as matches,
    count(r.decision) as reviewed,
    count(*) filter (where r.decision = 'retenu') as to_remove,
    count(*) filter (where r.decision = 'traite') as removed,
    count(*) filter (where r.decision = 'ecarte') as false_positives,
    count(*) filter (where r.decision = 'ecarte')::double precision / nullif(count(r.decision), 0)
        as false_positive_rate,
    avg(m.score) as avg_score
from public.matches m
left join public.reviews r on r.reference_id = m.reference_id and r.site_image_id = m.site_image_id
group by m.org_id, m.confidence, m.level;
