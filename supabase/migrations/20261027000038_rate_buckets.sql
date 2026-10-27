-- Limitation de débit partagée entre les processus web (AXE-586, suite d'AXE-585) : les seaux à jetons de
-- backend/nyra/cloud/ratelimit.py, dans Postgres au lieu de la mémoire d'un processus.
--
--   rate_buckets   identity   la personne (l'identifiant de son compte, jamais une adresse e-mail ni un jeton) ;
--                  kind       'read' ou 'write' (un seau chacun) ;
--                  tokens     jetons restants au moment `seen` ;
--                  seen       dernier passage ;
--                  granted    la dernière demande a-t-elle obtenu un jeton ?
--
-- `public.take_rate_token(identity, kind, capacity, now)` prend un jeton ATOMIQUEMENT : une seule instruction
-- INSERT ... ON CONFLICT DO UPDATE, donc un verrou de ligne le temps de l'instruction et pas d'aller-retour à deux temps.
-- Même règle que le seau en mémoire : `capacity` jetons par minute, rafale de `capacity`, un refus ne retire rien.
-- Elle rend 0 si la requête passe, sinon le nombre de secondes avant le prochain jeton. `now` n'est donné que par les
-- tests (horloge simulée) ; par défaut c'est l'horloge de la base, la même pour tous les processus web.
--
-- `public.purge_rate_buckets()` supprime les seaux au repos depuis plus de 2 minutes (un seau au repos depuis une minute est
-- plein : l'oublier ne change rien) et rend le nombre de lignes supprimées. La table reste donc petite : au plus deux lignes
-- par personne active.
--
-- UNLOGGED : pas d'écriture dans le journal de Postgres (un compteur par requête), vidée après un arrêt brutal, ce qui
-- remet tous les seaux pleins comme un redémarrage du processus le faisait. Aucune donnée client, aucune organisation :
-- hors de l'effacement d'une organisation (offboarding.GLOBAL). RLS activée sans politique et droits retirés à l'API
-- publique : seul le backend (rôle de service) y touche.
--
-- Additive et idempotente. Sans effet tant que NYRA_RATE_BACKEND n'est pas `database` (AXE-587).

create unlogged table if not exists public.rate_buckets (
    identity text not null,
    kind text not null check (kind in ('read', 'write')),
    tokens double precision not null,
    seen timestamptz not null,
    granted boolean not null,
    primary key (identity, kind)
);

alter table public.rate_buckets enable row level security;

create or replace function public.take_rate_token(p_identity text, p_kind text, p_capacity integer, p_now timestamptz default clock_timestamp())
returns double precision
language plpgsql
set search_path = ''
as $$
declare
    v_tokens double precision;
    v_granted boolean;
begin
    -- every expression of DO UPDATE reads the row as it was before the update: the refill is written out where it is used
    insert into public.rate_buckets as b (identity, kind, tokens, seen, granted)
    values (p_identity, p_kind, p_capacity - 1, p_now, true)
    on conflict (identity, kind) do update set
        granted = least(p_capacity, b.tokens + greatest(0, extract(epoch from p_now - b.seen)) * p_capacity / 60.0) >= 1,
        tokens = case
            when least(p_capacity, b.tokens + greatest(0, extract(epoch from p_now - b.seen)) * p_capacity / 60.0) >= 1
            then least(p_capacity, b.tokens + greatest(0, extract(epoch from p_now - b.seen)) * p_capacity / 60.0) - 1
            else least(p_capacity, b.tokens + greatest(0, extract(epoch from p_now - b.seen)) * p_capacity / 60.0)
        end,
        seen = greatest(b.seen, p_now)
    returning b.tokens, b.granted into v_tokens, v_granted;
    if v_granted then
        return 0;
    end if;
    return (1 - v_tokens) / (p_capacity / 60.0);
end;
$$;

create or replace function public.purge_rate_buckets()
returns bigint
language plpgsql
set search_path = ''
as $$
declare
    v_deleted bigint;
begin
    delete from public.rate_buckets where seen < clock_timestamp() - interval '2 minutes';
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

do $$
begin
    revoke all on public.rate_buckets from public;
    revoke all on function public.take_rate_token(text, text, integer, timestamptz) from public;
    revoke all on function public.purge_rate_buckets() from public;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on public.rate_buckets from anon;
        revoke all on function public.take_rate_token(text, text, integer, timestamptz) from anon;
        revoke all on function public.purge_rate_buckets() from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on public.rate_buckets from authenticated;
        revoke all on function public.take_rate_token(text, text, integer, timestamptz) from authenticated;
        revoke all on function public.purge_rate_buckets() from authenticated;
    end if;
end $$;
