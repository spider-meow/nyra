-- Batch 1 functional additions, mirrored from the SQLite schema in
-- backend/nyra/db.py: per-match review traceability (status/reviewed_at/
-- reviewed_note) and a per-org exclusion list for recurring false
-- positives (logos, generic assets). Additive only — no existing column
-- or table is touched.
--
-- Not wired into backend/nyra/cloud/ yet: this migration brings the
-- schema in parity with the local (SQLite) product so it's ready
-- whenever the cloud pipeline/API gets the same feature. Until then,
-- these columns/table just sit unused in cloud mode, same as the rest
-- of this schema (see supabase/README.md).

alter table public.matches
    add column status text not null default 'pending'
        check (status in ('pending', 'confirmed', 'rejected')),
    add column reviewed_at timestamptz,
    add column reviewed_by uuid references auth.users(id) on delete set null,
    add column reviewed_note text;

create index matches_org_status_idx on public.matches(org_id, status);

-- excluded_hashes: a site image whose phash/dhash lands within the usual
-- matching threshold of one of these rows is dropped from run_matching
-- before it's ever written to `matches` — the exact mechanism as
-- match.exclude_and_purge()/_excluded_site_ids() in the local pipeline.
create table public.excluded_hashes (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    hash text not null,
    hash_type text not null check (hash_type in ('phash', 'dhash')),
    reason text,
    created_at timestamptz not null default now(),
    unique (org_id, hash, hash_type)
);

create index excluded_hashes_org_id_idx on public.excluded_hashes(org_id);

alter table public.excluded_hashes enable row level security;

create policy "org members can read excluded_hashes"
    on public.excluded_hashes for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write excluded_hashes"
    on public.excluded_hashes for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));
