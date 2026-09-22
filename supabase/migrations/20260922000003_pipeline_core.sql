-- Core pipeline tables: same shape as the SQLite schema in
-- backend/nyra/db.py, translated to Postgres:
--   - autoincrement int ids -> uuid (standard for multi-tenant Supabase)
--   - every table gets org_id (see 0002 for the multi-tenancy model)
--   - a new `sites` table: the SQLite version inferred "the site" from
--     whatever was in `pages`; a real table is needed once an org can
--     have more than one site and crawl history matters (see crawl_runs
--     in 0004)
--   - `embedding` is `vector(512)` (pgvector) instead of raw float32
--     bytes, matching open_clip's ViT-B-32 output dimension configured
--     in config.yaml. No ANN index is created here on purpose: the
--     matching pipeline does an exhaustive compare (every reference
--     against every site image, vectorized in Python/numpy), not a
--     nearest-neighbor search, so an index would add write overhead
--     for no read benefit today. Add one later (e.g. hnsw) only if a
--     "find visually similar" search feature is built directly in SQL.

create table public.sites (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    url text not null,
    label text,
    created_at timestamptz not null default now(),
    unique (org_id, url)
);

create index sites_org_id_idx on public.sites(org_id);

create table public.reference_images (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    filename text not null,
    storage_path text not null,
    expiry_date date,
    credit text,
    notes text,
    phash text,
    dhash text,
    embedding vector(512),
    width integer,
    height integer,
    compared_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (org_id, filename)
);

create index reference_images_org_id_idx on public.reference_images(org_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger reference_images_set_updated_at
before update on public.reference_images
for each row
execute function public.set_updated_at();

create table public.pages (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    site_id uuid not null references public.sites(id) on delete cascade,
    url text not null,
    status text not null default 'pending',
    http_status integer,
    crawled_at timestamptz,
    unique (site_id, url)
);

create index pages_org_id_idx on public.pages(org_id);
create index pages_site_id_status_idx on public.pages(site_id, status);

create table public.site_images (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    site_id uuid not null references public.sites(id) on delete cascade,
    url text not null,
    storage_path text,
    content_hash text,
    width integer,
    height integer,
    phash text,
    dhash text,
    embedding vector(512),
    compared_at timestamptz,
    first_seen timestamptz not null default now(),
    last_seen timestamptz not null default now(),
    unique (site_id, url)
);

create index site_images_org_id_idx on public.site_images(org_id);
create index site_images_site_id_idx on public.site_images(site_id);

create table public.image_pages (
    image_id uuid not null references public.site_images(id) on delete cascade,
    page_id uuid not null references public.pages(id) on delete cascade,
    primary key (image_id, page_id)
);

create index image_pages_page_id_idx on public.image_pages(page_id);

create table public.matches (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    reference_id uuid not null references public.reference_images(id) on delete cascade,
    site_image_id uuid not null references public.site_images(id) on delete cascade,
    level text not null,
    score real not null,
    confidence text not null,
    created_at timestamptz not null default now(),
    unique (reference_id, site_image_id)
);

create index matches_org_id_idx on public.matches(org_id);
create index matches_reference_id_idx on public.matches(reference_id);
create index matches_site_image_id_idx on public.matches(site_image_id);

create table public.reviews (
    reference_id uuid not null references public.reference_images(id) on delete cascade,
    site_image_id uuid not null references public.site_images(id) on delete cascade,
    org_id uuid not null references public.organizations(id) on delete cascade,
    decision text not null check (decision in ('retenu', 'ecarte', 'traite')),
    reviewed_by uuid references auth.users(id) on delete set null,
    updated_at timestamptz not null default now(),
    primary key (reference_id, site_image_id)
);

create index reviews_org_id_idx on public.reviews(org_id);

-- Incremental-matching cache: run_matching() only recompares references/
-- site images not yet stamped `compared_at` unless the threshold
-- signature changed, in which case it does a full recompute. One row per
-- organization (matches whole library vs. every site's images together).
create table public.match_meta (
    org_id uuid primary key references public.organizations(id) on delete cascade,
    signature text not null,
    finished_at timestamptz not null
);
