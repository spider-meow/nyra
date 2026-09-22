-- Storage buckets replacing local disk (data/library/images, data/site_images,
-- out/ in the current backend). Every object path starts with the
-- organization id: "{org_id}/{filename}" — that's what lets the policies
-- below scope access without a separate metadata table.
--
--   refs/{org_id}/{filename}                     reference image originals
--   site-images/{org_id}/{sha256}.<ext>           crawled image cache
--   reports/{org_id}/{report_id}/<report file>    generated report snapshots
--
-- All three are private (public = false): every read goes through a
-- signed URL issued by the backend after it has checked the caller's
-- membership, or through an authenticated Storage request that RLS on
-- storage.objects then re-checks.

insert into storage.buckets (id, name, public)
values
    ('refs', 'refs', false),
    ('site-images', 'site-images', false),
    ('reports', 'reports', false)
on conflict (id) do nothing;

alter table storage.objects enable row level security;

-- refs: uploaded and edited by admins through the Library screen; clients
-- only ever need to view the thumbnails.
create policy "org members can read ref images"
    on storage.objects for select
    using (
        bucket_id = 'refs'
        and (storage.foldername(name))[1] in (select public.current_org_ids()::text)
    );
create policy "org admins can write ref images"
    on storage.objects for all
    using (
        bucket_id = 'refs'
        and public.is_org_admin(((storage.foldername(name))[1])::uuid)
    )
    with check (
        bucket_id = 'refs'
        and public.is_org_admin(((storage.foldername(name))[1])::uuid)
    );

-- site-images and reports are only ever written by the backend's crawl/
-- match/report pipeline (service role, bypasses RLS) — org members only
-- need read access, so no write policy is defined for the `authenticated`
-- role on these two buckets.
create policy "org members can read site images"
    on storage.objects for select
    using (
        bucket_id = 'site-images'
        and (storage.foldername(name))[1] in (select public.current_org_ids()::text)
    );

create policy "org members can read reports"
    on storage.objects for select
    using (
        bucket_id = 'reports'
        and (storage.foldername(name))[1] in (select public.current_org_ids()::text)
    );
