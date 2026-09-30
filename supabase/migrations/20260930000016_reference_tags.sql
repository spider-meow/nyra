-- Free-form tags on library references (a product line, a shoot, a campaign),
-- so a brand can search its library by "miniature", "magnum"... Normalized
-- by the API (trimmed, lowercase, no duplicates); the database only stores
-- them. Tags say nothing to the matching: they are never read by the worker.
--
-- Additive: safe to apply before deploying the code that uses it. Existing
-- references get an empty list. Replacing a reference's image (uploading
-- the same file name again) keeps its tags, like its expiry and credit.

alter table public.reference_images
    add column if not exists tags text[] not null default '{}';

-- "Every reference of this brand carrying this tag" (tags && / @>).
create index if not exists reference_images_tags_idx
    on public.reference_images using gin (tags);
