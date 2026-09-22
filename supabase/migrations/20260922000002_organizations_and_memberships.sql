-- Multi-tenancy: one Supabase project shared across every RightsWatch
-- client, isolated by organization + Row Level Security (see
-- 20260922000005_row_level_security.sql).
--
-- `memberships` is a join table rather than a single org_id on a profile
-- so an Axel admin can belong to several client organizations (one row
-- per org they manage) while a client user typically has exactly one.

create table public.organizations (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    slug text not null unique,
    created_at timestamptz not null default now()
);

create table public.memberships (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    org_id uuid not null references public.organizations(id) on delete cascade,
    role text not null check (role in ('admin', 'client')),
    created_at timestamptz not null default now(),
    unique (user_id, org_id)
);

create index memberships_org_id_idx on public.memberships(org_id);
create index memberships_user_id_idx on public.memberships(user_id);

-- Helper functions used by every RLS policy from here on. SECURITY DEFINER
-- so they can read `memberships` regardless of the caller's own RLS
-- visibility into that table (avoids the classic self-referencing-policy
-- recursion), with `search_path` pinned per Supabase's guidance for
-- SECURITY DEFINER functions.

create or replace function public.current_org_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
    select org_id from public.memberships where user_id = auth.uid();
$$;

create or replace function public.is_org_admin(target_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1 from public.memberships
        where user_id = auth.uid()
          and org_id = target_org
          and role = 'admin'
    );
$$;
