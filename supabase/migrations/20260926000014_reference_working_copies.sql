-- A working copy next to each reference original: a JPEG of at most 1024 px
-- on the long side (~100-200 KB) that indexing, the keypoint check and the
-- comparison screen read instead of the original (which can weigh tens of
-- MB). The original stays untouched in Storage.
--
-- Crawled images need no column: from now on their `storage_path` *is* the
-- working copy (the original is on the site). Rows crawled before keep their
-- original there.
--
-- Additive: safe to apply before deploying the code that uses it. NULL means
-- "not made yet"; the worker's index job makes it for older references.

alter table public.reference_images
    add column if not exists work_path text;
