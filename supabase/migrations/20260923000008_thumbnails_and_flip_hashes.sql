-- Small JPEG thumbnails stored next to every image, so lists and reports
-- never load the full-size original. Mirror-image hashes on references,
-- so a horizontally flipped reuse is caught by level-1 matching.
-- Rows created before this migration get both filled by the worker's
-- `index` job (see backend/nyra/cloud/worker.py), no manual backfill.

alter table public.reference_images
    add column if not exists phash_flip text,
    add column if not exists dhash_flip text,
    add column if not exists thumb_path text;

alter table public.site_images
    add column if not exists thumb_path text;

-- The crawler reuses an already stored image when the same bytes show up
-- under another URL (CDN variants).
create index if not exists site_images_org_content_hash_idx
    on public.site_images(org_id, content_hash);

create index if not exists reviews_reference_id_idx on public.reviews(reference_id);
