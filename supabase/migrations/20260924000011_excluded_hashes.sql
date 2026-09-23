-- Recurring false positives (a logo, a generic visual reused on every
-- page): excluding one site image stores its pHash and dHash here, and
-- the matcher then ignores every site image within the usual Hamming
-- threshold of them. The exclusion list is part of the match signature,
-- so adding or removing one triggers a full recompute on the next pass.
--
-- Written to be safe on a project where an earlier draft
-- (migration_008_review_status_and_exclusions.sql) already created this
-- table: same columns, plus the ones below.

create table if not exists public.excluded_hashes (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    hash text not null,
    hash_type text not null check (hash_type in ('phash', 'dhash')),
    reason text,
    created_at timestamptz not null default now(),
    unique (org_id, hash, hash_type)
);

-- One exclusion = one pHash row + one dHash row sharing a group id, plus
-- what the interface needs to show it.
alter table public.excluded_hashes
    add column if not exists group_id uuid,
    add column if not exists thumb_path text,
    add column if not exists site_url text,
    add column if not exists created_by uuid references auth.users(id) on delete set null;

create index if not exists excluded_hashes_org_id_idx on public.excluded_hashes(org_id);
create index if not exists excluded_hashes_group_id_idx on public.excluded_hashes(group_id);

alter table public.excluded_hashes enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'excluded_hashes' and policyname = 'org members can read excluded_hashes') then
        create policy "org members can read excluded_hashes"
            on public.excluded_hashes for select
            using (org_id in (select public.current_org_ids()));
    end if;
    if not exists (select 1 from pg_policies where tablename = 'excluded_hashes' and policyname = 'org admins can write excluded_hashes') then
        create policy "org admins can write excluded_hashes"
            on public.excluded_hashes for all
            using (public.is_org_admin(org_id))
            with check (public.is_org_admin(org_id));
    end if;
end $$;
