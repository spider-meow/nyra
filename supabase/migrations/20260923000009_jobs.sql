-- Durable job queue. The web process only enqueues; a separate worker
-- process (`nyra worker`) claims jobs with FOR UPDATE SKIP LOCKED, runs
-- them, and reports progress through `heartbeat_at`/`progress`. A job
-- whose heartbeat stops (worker crash, redeploy) is marked as an error by
-- the next worker that looks, instead of staying "running" forever.

create table public.jobs (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    kind text not null check (kind in ('crawl', 'match', 'index', 'report')),
    params jsonb not null default '{}'::jsonb,
    status text not null default 'queued' check (status in ('queued', 'running', 'done', 'error', 'cancelled')),
    message text not null default '',
    progress jsonb not null default '{}'::jsonb,
    result jsonb,
    error text,
    cancel_requested boolean not null default false,
    created_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    heartbeat_at timestamptz
);

-- At most one running job per organization; the claim query relies on it.
create unique index jobs_one_running_per_org on public.jobs(org_id) where status = 'running';
create index jobs_queued_idx on public.jobs(created_at) where status = 'queued';
create index jobs_org_created_idx on public.jobs(org_id, created_at desc);

alter table public.jobs enable row level security;

create policy "org members can read jobs"
    on public.jobs for select
    using (org_id in (select public.current_org_ids()));

alter table public.crawl_runs
    add column if not exists job_id uuid references public.jobs(id) on delete set null;
