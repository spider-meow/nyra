-- One running job per ORGANIZATION: the brands of an organization take turns,
-- organizations run in parallel. Replaces the per-brand rule of 013 (which had
-- replaced this very rule of 009 when brands appeared).
--
-- Order of a deployment (see docs/DEPLOYMENT.md): stop the workers, check that
-- no job runs, apply this migration, deploy the web process, start the new
-- workers. An old worker would retry the same queued job forever against the
-- new index; a new worker against the old index could start two jobs of one
-- organization at once and make this migration fail.
--
-- The migration stops with the message below, changing nothing, if an
-- organization has two running jobs. The table is locked first so that no job
-- can start between that check and the index.

lock table public.jobs in share row exclusive mode;

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
-- `jobs/current` asks for the active jobs of an organization every 2 s per open tab.
create index if not exists jobs_org_active_idx on public.jobs(org_id) where status in ('queued', 'running');
