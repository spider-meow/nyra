-- Pages a person marked as replaced: for one visual of the library, a page where the visual was changed by hand.
-- It tells the person where they are, whatever the last read of the site says; a read (or a check of the page) then
-- confirms or not. A page is marked for one visual: another visual shown on the same page is its own mark.
--
-- Additive: safe to apply before deploying the code that uses it. Nothing is marked until a person does it.

create table if not exists public.page_replacements (
    reference_id uuid not null references public.reference_images(id) on delete cascade,
    page_id uuid not null references public.pages(id) on delete cascade,
    org_id uuid not null references public.organizations(id) on delete cascade,
    marked_by uuid references auth.users(id) on delete set null,
    marked_at timestamptz not null default now(),
    primary key (reference_id, page_id)
);

create index if not exists page_replacements_org_id_idx on public.page_replacements(org_id);
create index if not exists page_replacements_page_id_idx on public.page_replacements(page_id);

alter table public.page_replacements enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'page_replacements' and policyname = 'org members can read page_replacements') then
        create policy "org members can read page_replacements"
            on public.page_replacements for select
            using (org_id in (select public.current_org_ids()));
    end if;
end $$;
