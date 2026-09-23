-- Organization slugs are lowercase ascii segments separated by single
-- hyphens: remy-martin, not "Rémy Martin" or remy--martin.
-- The application normalizes before insert (nyra.cloud.db.slugify);
-- this constraint rejects anything that bypasses it.

alter table public.organizations
    add constraint organizations_slug_format
    check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$' and char_length(slug) <= 63);
