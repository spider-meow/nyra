-- History tables that don't exist in the SQLite/local version, where job
-- state lived only in the backend's in-memory JobRunner (lost on restart,
-- not shared across instances) and reports were only ever "the current
-- state, regenerated on demand".
--
-- crawl_runs persists what CrawlStats (backend/nyra/crawl.py)
-- already tracks in memory, so a job survives a server restart and its
-- history is queryable. reports snapshots each generated report so a
-- client can look back at a past run rather than only ever seeing live
-- state.

create table public.crawl_runs (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    site_id uuid not null references public.sites(id) on delete cascade,
    status text not null default 'running' check (status in ('running', 'done', 'error', 'cancelled')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    pages_visited integer not null default 0,
    images_found integer not null default 0,
    images_stored integer not null default 0,
    images_new integer not null default 0,
    blocked_by_robots integer not null default 0,
    errors jsonb not null default '[]'::jsonb,
    triggered_by uuid references auth.users(id) on delete set null
);

create index crawl_runs_org_id_idx on public.crawl_runs(org_id);
create index crawl_runs_site_id_idx on public.crawl_runs(site_id);

create table public.reports (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    within_days integer not null,
    storage_path_html text not null,
    storage_path_csv text not null,
    storage_path_not_found_csv text not null,
    stats jsonb not null default '{}'::jsonb,
    generated_at timestamptz not null default now(),
    generated_by uuid references auth.users(id) on delete set null
);

create index reports_org_id_idx on public.reports(org_id);
