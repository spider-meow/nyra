-- Second step of 012: apply only once the brand-aware code is deployed
-- (every insert sets brand_id itself, every upsert conflicts on the
-- per-brand keys, the worker claims per brand).

drop trigger if exists sites_fill_default_brand on public.sites;
drop trigger if exists reference_images_fill_default_brand on public.reference_images;
drop trigger if exists excluded_hashes_fill_default_brand on public.excluded_hashes;
drop trigger if exists reports_fill_default_brand on public.reports;
drop trigger if exists jobs_fill_default_brand on public.jobs;
drop trigger if exists match_meta_fill_default_brand on public.match_meta;
drop function if exists public.fill_default_brand();

alter table public.sites alter column brand_id set not null;
alter table public.reference_images alter column brand_id set not null;
alter table public.excluded_hashes alter column brand_id set not null;
alter table public.reports alter column brand_id set not null;
alter table public.jobs alter column brand_id set not null;
alter table public.match_meta alter column brand_id set not null;

-- A filename or an excluded hash is unique within a brand, no longer
-- within the organization. (Default constraint names from 003 and 011;
-- on a project where excluded_hashes came from the earlier draft, check
-- the name with \d public.excluded_hashes.)
alter table public.reference_images drop constraint if exists reference_images_org_id_filename_key;
alter table public.excluded_hashes drop constraint if exists excluded_hashes_org_id_hash_hash_type_key;

-- One match signature per brand.
alter table public.match_meta drop constraint match_meta_pkey;
alter table public.match_meta drop constraint match_meta_brand_id_key;
alter table public.match_meta add primary key (brand_id);
create index match_meta_org_id_idx on public.match_meta(org_id);

-- One running job per brand replaces one per organization.
drop index if exists public.jobs_one_running_per_org;
