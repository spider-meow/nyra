-- Lecture mensuelle automatique : une ligne par marque dont la planification a été réglée.
--
--   brand_schedules   enabled       la marque est lue chaque mois (inactive par défaut : la table est vide au départ) ;
--                     day_of_month  jour choisi, de 1 à 28 (tous les mois ont ces jours) ;
--                     last_run_at   dernière mise en file par le worker : une lecture par mois au plus.
--                     L'heure (créneau entre 02:00 et 05:00 UTC) se déduit de l'identifiant de la marque :
--                     voir backend/nyra/cloud/schedule.py.
--
-- Lecture : les membres de l'organisation. Aucune politique d'écriture : seul le backend (rôle de service) écrit.
--
-- Additive et sans effet tant qu'aucune ligne n'est activée : sûre à appliquer avant OU après le déploiement du code
-- (le code sans la table ne planifie rien et le journalise ; la table sans le code reste inerte). Ordre conseillé :
-- appliquer cette migration, puis déployer.

create table if not exists public.brand_schedules (
    brand_id uuid primary key,
    org_id uuid not null references public.organizations(id) on delete cascade,
    enabled boolean not null default false,
    day_of_month int not null check (day_of_month between 1 and 28),
    last_run_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint brand_schedules_brand_fk foreign key (brand_id, org_id)
        references public.brands(id, org_id) on delete cascade
);

-- Le worker cherche les marques actives pas encore lues ce mois-ci.
create index if not exists brand_schedules_enabled_idx on public.brand_schedules(day_of_month) where enabled;
create index if not exists brand_schedules_org_id_idx on public.brand_schedules(org_id);

alter table public.brand_schedules enable row level security;

drop policy if exists "org members can read brand_schedules" on public.brand_schedules;
create policy "org members can read brand_schedules"
    on public.brand_schedules for select
    using (org_id in (select public.current_org_ids()));
