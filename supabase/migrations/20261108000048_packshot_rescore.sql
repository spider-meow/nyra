-- Packshots (AXE-679): the product descriptions of `nyra/packshots.py` changed (five more: a gift box, crystal glasses, a case...), so the product score kept on
-- each site image is out of date. The score is worked out once from the stored embedding and never again (`cloud/packshots.score_pending` only scores the rows
-- where it is null): putting it back to null has the next page of "Images en ligne" or the next `index` job score every image with the new descriptions, from
-- the embedding alone (no image is read).
--
--   site_images.packshot_score   back to null (not scored yet)
--
-- The feedback of the Nyra team keeps the measures it was given with (`packshot_feedback`): the report reads those, they are not touched.
--
-- Order: deploy the code (web and worker) FIRST, then apply this. Applied before, a web or worker process still running the old descriptions would score the
-- images again with them.
-- Until the images are scored again a brand that turned the Packshots tab on sees fewer packshots, never more: nothing is hidden or compared differently.

update public.site_images set packshot_score = null where packshot_score is not null;
