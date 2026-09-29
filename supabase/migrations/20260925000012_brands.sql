-- Brands: an organization (a client) holds one or more brands, each with
-- its own reference library and one or more sites, typically one per
-- market (US, INT, UK, FR). A brand's references are only ever compared
-- with images crawled on that brand's sites; exclusions, reports, the
-- incremental-matching signature and the job queue are per brand too.
-- Settings (org_settings) stay per organization.
--
-- Applied in two steps so the running code keeps working in between:
--   012 (this file) is additive: brands, one default brand per
--       organization holding everything that exists today, nullable
--       brand_id columns, the per-brand unique keys next to the org-wide
--       ones, and a trigger that files rows written by the current code
--       under the organization's first brand.
--   013, once the brand-aware code is deployed: NOT NULL, drops the
--       org-wide unique keys and the trigger.

create table public.brands (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    name text not null,
    slug text not null check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' and char_length(slug) <= 63),
    created_at timestamptz not null default now(),
    unique (org_id, slug),
    -- Target of the composite foreign keys below: a row can't point at
    -- another organization's brand.
    unique (id, org_id)
);

create index brands_org_id_idx on public.brands(org_id);

alter table public.brands enable row level security;

create policy "org members can read brands"
    on public.brands for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write brands"
    on public.brands for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

-- One brand per existing organization, named after it.
insert into public.brands (org_id, name, slug)
select id, name, slug from public.organizations;

-- brand_id on every table whose rows belong to one brand. Pages, site
-- images, matches, reviews and crawl runs reach their brand through
-- sites or reference_images and don't get a column of their own.

alter table public.sites add column brand_id uuid;
alter table public.reference_images add column brand_id uuid;
alter table public.excluded_hashes add column brand_id uuid;
alter table public.reports add column brand_id uuid;
alter table public.jobs add column brand_id uuid;
alter table public.match_meta add column brand_id uuid;

alter table public.sites
    add constraint sites_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;
alter table public.reference_images
    add constraint reference_images_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;
alter table public.excluded_hashes
    add constraint excluded_hashes_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;
alter table public.reports
    add constraint reports_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;
alter table public.jobs
    add constraint jobs_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;
alter table public.match_meta
    add constraint match_meta_brand_fk foreign key (brand_id, org_id)
    references public.brands(id, org_id) on delete cascade;

-- Everything that exists goes to the organization's only brand.
update public.sites t set brand_id = b.id from public.brands b where b.org_id = t.org_id;
update public.reference_images t set brand_id = b.id from public.brands b where b.org_id = t.org_id;
update public.excluded_hashes t set brand_id = b.id from public.brands b where b.org_id = t.org_id;
update public.reports t set brand_id = b.id from public.brands b where b.org_id = t.org_id;
update public.jobs t set brand_id = b.id from public.brands b where b.org_id = t.org_id;
update public.match_meta t set brand_id = b.id from public.brands b where b.org_id = t.org_id;

-- Until 013: rows inserted by code that doesn't know about brands yet go
-- to the organization's first brand.
create or replace function public.fill_default_brand()
returns trigger
language plpgsql
as $$
begin
    if new.brand_id is null then
        select id into new.brand_id
        from public.brands
        where org_id = new.org_id
        order by created_at, id
        limit 1;
    end if;
    return new;
end;
$$;

create trigger sites_fill_default_brand before insert on public.sites
    for each row execute function public.fill_default_brand();
create trigger reference_images_fill_default_brand before insert on public.reference_images
    for each row execute function public.fill_default_brand();
create trigger excluded_hashes_fill_default_brand before insert on public.excluded_hashes
    for each row execute function public.fill_default_brand();
create trigger reports_fill_default_brand before insert on public.reports
    for each row execute function public.fill_default_brand();
create trigger jobs_fill_default_brand before insert on public.jobs
    for each row execute function public.fill_default_brand();
create trigger match_meta_fill_default_brand before insert on public.match_meta
    for each row execute function public.fill_default_brand();

-- Per-brand keys, next to the org-wide ones for now. The org-wide ones
-- are stricter while every organization has a single brand, so both
-- hold; 013 drops the org-wide ones.
alter table public.reference_images
    add constraint reference_images_brand_filename_key unique (brand_id, filename);
alter table public.excluded_hashes
    add constraint excluded_hashes_brand_hash_key unique (brand_id, hash, hash_type);
alter table public.match_meta
    add constraint match_meta_brand_id_key unique (brand_id);

-- One running job per brand: two brands of the same organization can
-- crawl or compare at the same time.
create unique index jobs_one_running_per_brand on public.jobs(brand_id) where status = 'running';

create index sites_brand_id_idx on public.sites(brand_id);
create index reference_images_brand_id_idx on public.reference_images(brand_id);
create index excluded_hashes_brand_id_idx on public.excluded_hashes(brand_id);
create index reports_brand_id_idx on public.reports(brand_id);
create index jobs_brand_created_idx on public.jobs(brand_id, created_at desc);
