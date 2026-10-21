-- One running job per ORGANIZATION: the brands of an organization take turns,
-- organizations run in parallel. Replaces the per-brand rule of 013 (which had
-- replaced this very rule of 009 when brands appeared). Apply when no job runs:
-- the index cannot be built while two jobs of one organization are `running`,
-- and the migration then stops with the message below, changing nothing.

do $$
declare
    busy int;
begin
    select count(*) into busy from (
        select org_id from public.jobs where status = 'running' group by org_id having count(*) > 1
    ) t;
    if busy > 0 then
        raise exception 'jobs_one_running_per_org: % organization(s) have several running jobs; wait for them to end (or stop the worker and requeue them) and apply again', busy;
    end if;
end $$;

drop index if exists public.jobs_one_running_per_brand;
create unique index jobs_one_running_per_org on public.jobs(org_id) where status = 'running';
