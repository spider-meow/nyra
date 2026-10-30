-- Where a page that answered with another page ended (AXE-641). Until now a redirected page only said "redirected" (status 302): the
-- target was lost, so nothing could tell a barrier (login, age gate, country choice) from a real replacement.
--
--   pages.redirect_url     the address the browser ended on (null: the page did not redirect)
--   pages.redirect_hops    how many HTTP redirects led there (0: a script or a meta tag, no HTTP redirect)
--   pages.redirect_status  the status of the target (2xx, or -1 when a consent wall hid it)
--   pages.redirect_kind    `clear`, `temporary`, `script`, `home`, `other_section`, `other_site`, `barrier` or `error` (`nyra.crawl.redirect_kind`)
--
-- Additive and nullable: safe to apply before deploying the code, and the worker also runs without it (it logs one warning an hour and
-- keeps the old behaviour). A page is written again at each read, so a page that stops redirecting loses these columns.

alter table public.pages
    add column if not exists redirect_url text,
    add column if not exists redirect_hops integer,
    add column if not exists redirect_status integer,
    add column if not exists redirect_kind text;
