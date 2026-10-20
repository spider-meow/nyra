-- Journal d'audit : qui a fait quoi, dans quelle organisation.
--
--   audit_log            une ligne par action d'une personne qui modifie quelque chose (bibliothèque, décisions,
--                        exclusions, réglages, membres, tâches, marques). Écrit par le backend dans la même
--                        transaction que l'action ; lu par les administrateurs de l'organisation et l'équipe Nyra.
--                        Jamais de contenu client : des identifiants et des compteurs (voir backend/nyra/cloud/audit.py).
--                        `brand_id` n'a pas de clé étrangère : la ligne survit à la suppression de la marque.
--                        `user_id` passe à NULL si le compte Auth est supprimé (la personne n'est plus identifiable).
--                        Conservation 12 mois : `nyra cloud-purge-audit`, lancé aussi chaque jour par le worker.
--   deleted_organizations  trace minimale d'une suppression complète (`nyra cloud-delete-org`) : sans clé étrangère,
--                        elle survit à l'organisation. Ni contenu, ni e-mail : l'identifiant, le nom, la date, l'opérateur.
--
-- Additive : sûre à appliquer avant de déployer le code qui s'en sert.

create table if not exists public.audit_log (
    id bigint generated always as identity primary key,
    org_id uuid not null references public.organizations(id) on delete cascade,
    brand_id uuid,
    user_id uuid references auth.users(id) on delete set null,
    action text not null check (length(action) between 1 and 64),
    object_type text not null check (length(object_type) between 1 and 40),
    object_id text check (length(object_id) <= 80),
    detail jsonb not null default '{}'::jsonb check (pg_column_size(detail) <= 2048),
    created_at timestamptz not null default now()
);

create index if not exists audit_log_org_created_idx on public.audit_log (org_id, created_at desc);
-- La purge cherche les lignes plus vieilles que la durée de conservation, toutes organisations confondues.
create index if not exists audit_log_created_idx on public.audit_log (created_at);

alter table public.audit_log enable row level security;

-- Lecture : les administrateurs de l'organisation. Aucune politique d'écriture : seul le backend (rôle de service) écrit.
create policy "org admins can read audit_log"
    on public.audit_log for select
    using (public.is_org_admin(org_id));

create table if not exists public.deleted_organizations (
    org_id uuid primary key,
    name text not null,
    slug text not null,
    deleted_at timestamptz not null default now(),
    operator text not null,
    report jsonb not null default '{}'::jsonb
);

-- Aucune politique : ni les clients ni l'API publique n'y touchent, seul le backend (rôle de service).
alter table public.deleted_organizations enable row level security;
