-- What each worker container uses (CPU, memory), published every 15 s for the back office's "Serveur" page.
-- One row per container, replaced at each reading; rows older than a day are deleted by the next reading.
--
-- Additive: safe to apply before deploying the code that uses it. Read and written by the backend only.

create table if not exists public.service_metrics (
    instance text primary key,
    role text not null,
    sampled_at timestamptz not null default now(),
    reading jsonb not null
);

alter table public.service_metrics enable row level security;
