-- Reading only some pages: "check" jobs, and the moment a page's last read started.
--
--   jobs.kind                  accepts 'check' (read again the pages a person gives, 50 at most, nothing else)
--   pages.read_started_at      when the last read of the page started: the links it stamped (`image_pages.last_seen`) are
--                              at or after it, so "the image was on the page at its last read" can be told for one page
--                              read on its own, whatever the last full read of the site says
--
-- Additive: safe to apply before deploying the code that uses it. Pages already tracked get an approximate start (ten
-- minutes before their read ended); every read from now on records the real one.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
    check (kind in ('crawl', 'match', 'index', 'report', 'locate', 'check'));

alter table public.pages
    add column if not exists read_started_at timestamptz;

update public.pages
   set read_started_at = crawled_at - interval '10 minutes'
 where links_tracked and read_started_at is null and crawled_at is not null;
