-- "Où est-elle utilisée ?": every crawled site image that is the same photo as
-- a library reference, cropped, resized or set in a banner. Filled by the
-- `locate` job, which checks the reference against every image the brand's
-- sites gave (not only the closest ones), and replaced whole at each run.
--
-- `tier`: 'same' (confirmed) or 'review' (for a person to check), like matches.
-- `ref_coverage` / `site_coverage`: share of each image covered by the keypoints
-- that agree; a crop of the reference has a high site coverage and a low
-- reference coverage. `located_at` on the reference: when its last search ended
-- (NULL: never searched), so "nothing found" differs from "not searched".
--
-- Additive: safe to apply before deploying the code that uses it. The jobs
-- check is replaced to accept the new kind.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
    check (kind in ('crawl', 'match', 'index', 'report', 'locate'));

alter table public.reference_images add column if not exists located_at timestamptz;

create table if not exists public.reference_locations (
    reference_id uuid not null references public.reference_images(id) on delete cascade,
    site_image_id uuid not null references public.site_images(id) on delete cascade,
    org_id uuid not null references public.organizations(id) on delete cascade,
    tier text not null check (tier in ('same', 'review')),
    inliers integer not null,
    ref_coverage real not null,
    site_coverage real not null,
    found_at timestamptz not null default now(),
    primary key (reference_id, site_image_id)
);

create index if not exists reference_locations_site_image_idx on public.reference_locations(site_image_id);
create index if not exists reference_locations_org_id_idx on public.reference_locations(org_id);

alter table public.reference_locations enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'reference_locations' and policyname = 'org members can read reference_locations') then
        create policy "org members can read reference_locations"
            on public.reference_locations for select
            using (org_id in (select public.current_org_ids()));
    end if;
end $$;
