-- Labels a brand puts on its images, learned from examples.
--
-- `kind = 'type'`: what an image of a site IS (logo, packshot, pictogramme, autre),
-- one per image; it sorts "Droits non vérifiés". `kind = 'content'`: what it SHOWS
-- (a decanter, crystal glasses), several per image, on the library and the sites.
-- The four types are created for every brand by the API.
--
-- `image_labels` holds only what a person decided: `positive` true is an example
-- the algorithm learns from, false is "not this". What the algorithm proposes for
-- the other images is computed from the CLIP embeddings when asked (nothing
-- stored, so a new example changes the proposals at once). The matching never
-- reads labels.
--
-- Additive: safe to apply before deploying the code that uses it.

create table if not exists public.labels (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    brand_id uuid not null references public.brands(id) on delete cascade,
    kind text not null check (kind in ('type', 'content')),
    name text not null check (char_length(name) between 1 and 40),
    created_at timestamptz not null default now(),
    unique (brand_id, kind, name)
);

create table if not exists public.image_labels (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    label_id uuid not null references public.labels(id) on delete cascade,
    site_image_id uuid references public.site_images(id) on delete cascade,
    reference_id uuid references public.reference_images(id) on delete cascade,
    positive boolean not null,
    created_by uuid,
    created_at timestamptz not null default now(),
    check ((site_image_id is null) <> (reference_id is null)),
    unique (label_id, site_image_id),
    unique (label_id, reference_id)
);

create index if not exists labels_org_id_idx on public.labels(org_id);
create index if not exists image_labels_org_id_idx on public.image_labels(org_id);
create index if not exists image_labels_site_image_idx on public.image_labels(site_image_id);
create index if not exists image_labels_reference_idx on public.image_labels(reference_id);

alter table public.labels enable row level security;
alter table public.image_labels enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'labels' and policyname = 'org members can read labels') then
        create policy "org members can read labels"
            on public.labels for select
            using (org_id in (select public.current_org_ids()));
    end if;
    if not exists (select 1 from pg_policies where tablename = 'image_labels' and policyname = 'org members can read image_labels') then
        create policy "org members can read image_labels"
            on public.image_labels for select
            using (org_id in (select public.current_org_ids()));
    end if;
end $$;
