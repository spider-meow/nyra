-- Row Level Security for every table above.
--
-- The backend (FastAPI) is the only thing that writes most of this data
-- today, using Supabase's service-role key, which bypasses RLS entirely —
-- authorization for the pipeline (who can launch a crawl, edit the
-- library, etc.) is enforced in Python after checking the caller's
-- membership/role. These policies are the safety net for everything else:
-- anyone who ever queries Postgres or Storage directly (a future
-- dashboard, PostgREST, a bug in the backend) still can't cross an
-- organization boundary or write somewhere their role shouldn't.
--
-- Reviews are the one table clients write directly through the app
-- (retenir/ecarter/traite), so both roles get write access there;
-- everything else is admin-write / member-read.

alter table public.organizations enable row level security;
alter table public.memberships enable row level security;
alter table public.sites enable row level security;
alter table public.reference_images enable row level security;
alter table public.pages enable row level security;
alter table public.site_images enable row level security;
alter table public.image_pages enable row level security;
alter table public.matches enable row level security;
alter table public.reviews enable row level security;
alter table public.match_meta enable row level security;
alter table public.crawl_runs enable row level security;
alter table public.reports enable row level security;

-- organizations: members can see their own org's row. Creating an
-- organization is an onboarding action done via the service role (or a
-- dedicated admin tool), not a policy an end user needs — no insert/
-- update/delete policy means those are denied by default for the
-- `authenticated` role.
create policy "members can read their organization"
    on public.organizations for select
    using (id in (select public.current_org_ids()));

-- memberships: a user always sees their own membership rows; org admins
-- see (and manage) every membership in orgs they administer. The very
-- first admin membership of a brand-new org is created via the service
-- role during client onboarding — bootstrapping that from an
-- unprivileged session would require an admin membership to already
-- exist, which is exactly the chicken-and-egg RLS is supposed to prevent.
create policy "members can read relevant memberships"
    on public.memberships for select
    using (user_id = auth.uid() or public.is_org_admin(org_id));

create policy "org admins manage memberships"
    on public.memberships for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

-- Shared shape for every pipeline table: org members read, org admins
-- write. Written out per table (Postgres has no policy templates) but
-- identical in structure.

create policy "org members can read sites"
    on public.sites for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write sites"
    on public.sites for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

create policy "org members can read reference_images"
    on public.reference_images for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write reference_images"
    on public.reference_images for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

create policy "org members can read pages"
    on public.pages for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write pages"
    on public.pages for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

create policy "org members can read site_images"
    on public.site_images for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write site_images"
    on public.site_images for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

-- image_pages has no org_id of its own; derive it through site_images.
create policy "org members can read image_pages"
    on public.image_pages for select
    using (
        exists (
            select 1 from public.site_images si
            where si.id = image_pages.image_id
              and si.org_id in (select public.current_org_ids())
        )
    );
create policy "org admins can write image_pages"
    on public.image_pages for all
    using (
        exists (
            select 1 from public.site_images si
            where si.id = image_pages.image_id
              and public.is_org_admin(si.org_id)
        )
    )
    with check (
        exists (
            select 1 from public.site_images si
            where si.id = image_pages.image_id
              and public.is_org_admin(si.org_id)
        )
    );

create policy "org members can read matches"
    on public.matches for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write matches"
    on public.matches for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

-- reviews: the one table both roles write directly (retenir/ecarter/traite).
create policy "org members can read reviews"
    on public.reviews for select
    using (org_id in (select public.current_org_ids()));
create policy "org members can write reviews"
    on public.reviews for all
    using (org_id in (select public.current_org_ids()))
    with check (org_id in (select public.current_org_ids()));

create policy "org members can read match_meta"
    on public.match_meta for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write match_meta"
    on public.match_meta for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

create policy "org members can read crawl_runs"
    on public.crawl_runs for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write crawl_runs"
    on public.crawl_runs for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));

create policy "org members can read reports"
    on public.reports for select
    using (org_id in (select public.current_org_ids()));
create policy "org admins can write reports"
    on public.reports for all
    using (public.is_org_admin(org_id))
    with check (public.is_org_admin(org_id));
