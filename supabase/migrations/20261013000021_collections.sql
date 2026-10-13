-- Collections: folders of a brand's library (a shoot, a season: "Winter 26-27"), up to three levels deep
-- (the API enforces the depth). A visual sits in at most one collection; none = "Non classés".
-- Deleting a collection deletes its sub-collections and un-files their visuals (they are never deleted).
--
-- Additive: safe to apply before deploying the code that uses it. Existing references stay unfiled.

create table if not exists public.collections (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    brand_id uuid not null references public.brands(id) on delete cascade,
    parent_id uuid references public.collections(id) on delete cascade,
    name text not null check (char_length(name) between 1 and 80),
    created_at timestamptz not null default now()
);

-- Two siblings (same parent, or both at the top) never share a name.
create unique index if not exists collections_sibling_name_idx
    on public.collections (brand_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));
create index if not exists collections_org_id_idx on public.collections(org_id);
create index if not exists collections_parent_idx on public.collections(parent_id);

alter table public.collections enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'collections' and policyname = 'org members can read collections') then
        create policy "org members can read collections"
            on public.collections for select
            using (org_id in (select public.current_org_ids()));
    end if;
end $$;

alter table public.reference_images
    add column if not exists collection_id uuid references public.collections(id) on delete set null;

create index if not exists reference_images_collection_idx on public.reference_images(collection_id);
