-- Logos set aside: a per-brand setting (off by default) that keeps the site images recognised as logos out of the
-- comparison and out of "Droits non vérifiés".
--
--   brands.set_aside_logos   the setting
--   site_images.logo_score   probability (0 to 1) that the whole image is a logo, from its CLIP embedding
--                            (nyra/logos.py); null until it has been scored
--   site_images.logo_kept    a person put this image back into the comparison although it looks like a logo
--
-- Additive: safe to apply before deploying the code that uses it. Nothing changes until a brand turns the setting on.

alter table public.brands
    add column if not exists set_aside_logos boolean not null default false;

alter table public.site_images
    add column if not exists logo_score real,
    add column if not exists logo_kept boolean not null default false;
