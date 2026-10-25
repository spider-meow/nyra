-- E-mail mensuel : une ligne par organisation et par mois, pour qu'il ne parte jamais deux fois.
--
--   monthly_digests   month       premier jour du mois (UTC) ;
--                     status      'sending' : un worker l'a réclamé (ou a échoué, ou est mort en cours d'envoi) ;
--                                 'sent' : parti ; 'skipped' : rien à dire ce mois-ci, ou personne à qui l'écrire ;
--                     attempts    nombre de réclamations ce mois-ci (5 au plus, puis le worker abandonne et le journalise) ;
--                     sent_at     quand il est parti : le prochain e-mail ne parle que de ce qui s'est passé depuis ;
--                     updated_at  dernière réclamation : une réclamation « sending » se reprend après 30 minutes.
--
-- Lecture : les administrateurs de l'organisation. Aucune politique d'écriture : seul le backend (rôle de service) écrit.
-- Aucun contenu client ici : ni adresse, ni nom de fichier.
--
-- Additive et sans effet tant qu'aucune lecture mensuelle n'est activée : sûre à appliquer avant OU après le déploiement
-- du code (sans la table, le worker journalise un avertissement par heure et n'envoie rien). Ordre conseillé : appliquer
-- cette migration, puis déployer.

create table if not exists public.monthly_digests (
    org_id uuid not null references public.organizations(id) on delete cascade,
    month date not null,
    status text not null check (status in ('sending', 'sent', 'skipped')),
    attempts int not null default 0,
    sent_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (org_id, month)
);

alter table public.monthly_digests enable row level security;

drop policy if exists "org admins can read monthly_digests" on public.monthly_digests;
create policy "org admins can read monthly_digests"
    on public.monthly_digests for select
    using (public.is_org_admin(org_id));
