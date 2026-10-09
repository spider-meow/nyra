-- A reference with unlimited rights ("libre d'usage") never expires. It is not the
-- same as an empty expiry date, which means "date not entered yet". Such a
-- reference stays recognised on the sites, but it is no longer an urgency, an
-- alert or a thing to treat once it is found with certainty.
--
-- An unlimited reference carries no date: the check keeps the two from
-- contradicting each other, and the statistics stay right without a second rule.
--
-- Additive: safe to apply before deploying the code that uses it. Existing
-- references are not unlimited.

alter table public.reference_images
    add column if not exists unlimited_rights boolean not null default false;

alter table public.reference_images
    drop constraint if exists reference_images_unlimited_without_date;
alter table public.reference_images
    add constraint reference_images_unlimited_without_date
    check (not unlimited_rights or expiry_date is null);

-- `without_expiry` is "date not entered": unlimited references are counted apart.
create or replace view insights.library as
select
    b.org_id,
    o.name as org_name,
    b.id as brand_id,
    b.name as brand_name,
    count(r.id) as references_total,
    count(r.embedding) as references_indexed,
    count(r.id) filter (where r.expiry_date < current_date) as expired,
    count(r.id) filter (where r.expiry_date >= current_date and r.expiry_date < current_date + 30) as expiring_30_days,
    count(r.id) filter (where r.expiry_date >= current_date and r.expiry_date < current_date + 90) as expiring_90_days,
    count(r.id) filter (where r.expiry_date is null and not r.unlimited_rights) as without_expiry,
    sum(r.byte_size) as total_bytes,
    avg(r.byte_size) as avg_bytes,
    avg(r.width::double precision * r.height) / 1e6 as avg_megapixels,
    count(r.id) filter (where r.unlimited_rights) as unlimited_rights
from public.brands b
join public.organizations o on o.id = b.org_id
left join public.reference_images r on r.brand_id = b.id
group by b.org_id, o.name, b.id, b.name;
