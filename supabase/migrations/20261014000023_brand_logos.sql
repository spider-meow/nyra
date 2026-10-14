-- A brand's logo, shown at the top of the side column in place of its initials.
--
-- The API shrinks every upload to a 128 px PNG and stores it here as a data URI (a few KB): it travels
-- with the list of brands, so no storage bucket and no signed URL that would expire. A table of its own,
-- not a column of `brands`, because `brands` is read on every request and must stay light.
--
-- Additive: safe to apply before deploying the code that uses it. Existing brands keep their initials.

create table if not exists public.brand_logos (
    brand_id uuid primary key references public.brands(id) on delete cascade,
    data_uri text not null check (char_length(data_uri) <= 120000),
    updated_at timestamptz not null default now()
);

alter table public.brand_logos enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'brand_logos' and policyname = 'org members can read brand logos') then
        create policy "org members can read brand logos"
            on public.brand_logos for select
            using (brand_id in (select id from public.brands where org_id in (select public.current_org_ids())));
    end if;
end $$;
