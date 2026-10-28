-- Historique de l'onglet « Serveur » du back office (AXE-612) : un relevé par minute et par conteneur (web et worker),
-- pour tracer l'usage dans le temps. `service_metrics` ne garde que le dernier relevé de chaque conteneur.
--
-- La clé est (instance, minute) : le worker publie toutes les 15 s, `INSERT ... ON CONFLICT DO NOTHING` n'en garde qu'un
-- par minute. Les lignes de plus de 30 jours sont supprimées par le relevé suivant (l'index sur `sampled_at` sert cette purge
-- et la lecture par période). Environ 1 440 lignes par jour et par conteneur.
--
-- Aucune donnée client ni organisation : hors de l'effacement d'une organisation (offboarding.GLOBAL). RLS activée sans
-- politique : seul le backend (rôle de service) y touche, comme `service_metrics`.
--
-- Additive et idempotente : sans risque à appliquer avant le déploiement du code qui l'utilise.

create table if not exists public.service_metrics_history (
    instance text not null,
    role text not null,
    sampled_at timestamptz not null,
    cpu_percent double precision,
    cpu_limit double precision,
    memory bigint not null,
    memory_limit bigint,
    load1 double precision not null,
    disk_ratio double precision not null,
    primary key (instance, sampled_at)
);

create index if not exists service_metrics_history_sampled_idx on public.service_metrics_history (sampled_at);

alter table public.service_metrics_history enable row level security;
