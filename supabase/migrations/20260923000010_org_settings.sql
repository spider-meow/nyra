-- Per-organization overrides of config.yaml (thresholds, crawl pacing,
-- age-gate selectors). Only the keys whitelisted in
-- backend/nyra/config.py (ORG_OVERRIDABLE) are ever read from here.

create table public.org_settings (
    org_id uuid primary key references public.organizations(id) on delete cascade,
    overrides jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id) on delete set null
);

alter table public.org_settings enable row level security;

create policy "org members can read org_settings"
    on public.org_settings for select
    using (org_id in (select public.current_org_ids()));

create policy "org admins can write org_settings"
    on public.org_settings for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));
