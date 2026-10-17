-- Is an image still on a page? The read that sees it stamps the link; a page is "tracked" once a successful read has
-- stamped its links, so a page that was not read again, or failed, is never taken for "the image is gone".
--
--   image_pages.last_seen    the last read that saw the image on this page (null: not since this exists)
--   pages.links_tracked      the last read of the page succeeded (HTTP 2xx) and stamped its links
--
-- Additive: safe to apply before deploying the code that uses it. Until a page is read again it counts as "unknown".

alter table public.image_pages
    add column if not exists last_seen timestamptz;

alter table public.pages
    add column if not exists links_tracked boolean not null default false;
