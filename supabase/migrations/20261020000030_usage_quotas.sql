-- Quotas and usage counters per organization (AXE-532).
--
-- `usage_counters` holds what an organization used: pages read, new images and bytes downloaded by the worker
-- (one row per month, `period` = first day of the month) and invitation e-mails sent (one row per day,
-- `period` = the day, UTC). The worker and the API add to it; nothing is ever deleted by the code.
-- `organizations.quota_overrides` holds the limits the Nyra team changed for one organization (keys of
-- `QuotaConfig` in backend/nyra/config.py); empty = the defaults of config.yaml. Only the back office writes it.
--
-- Additive: safe to apply before deploying the code that uses it. Read and written by the backend only.

create table if not exists public.usage_counters (
    org_id uuid not null references public.organizations(id) on delete cascade,
    period date not null,
    kind text not null check (kind in ('pages_read', 'images_new', 'bytes_downloaded', 'emails')),
    amount bigint not null default 0 check (amount >= 0),
    primary key (org_id, period, kind)
);

alter table public.usage_counters enable row level security;

create policy "org members can read usage_counters"
    on public.usage_counters for select
    using (org_id in (select public.current_org_ids()));

alter table public.organizations add column if not exists quota_overrides jsonb not null default '{}'::jsonb;
