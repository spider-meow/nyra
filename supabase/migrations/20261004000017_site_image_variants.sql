-- Crops, resizes and re-encodings of one photo found on a brand's sites share a
-- `variant_group`, so "Droits non vérifiés" shows the photo once and lists
-- every occurrence behind it. Filled by the worker after each comparison
-- (CLIP picks the twins, the keypoint check confirms them); never read by the
-- matching itself. NULL: no twin found.
--
-- `variants_checked_at` is the incremental marker, like `compared_at`: only
-- images where it is NULL are looked up against the others.
--
-- Additive: safe to apply before deploying the code that uses it. Existing
-- images start unchecked, so the next comparison groups them all.

alter table public.site_images
    add column if not exists variant_group uuid,
    add column if not exists variants_checked_at timestamptz;
