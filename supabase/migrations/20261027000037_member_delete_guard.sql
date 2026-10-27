-- Suite de la migration 20261025000035 (AXE-578) : le déclencheur `guard_member_notifications` couvre aussi DELETE.
--
-- Trou fermé : un administrateur d'une organisation (rôle `authenticated`, API publique de Supabase) pouvait supprimer la ligne
-- d'un co-administrateur bloqué par l'équipe Nyra (`monthly_email_allowed = false`), puis la recréer avec les valeurs par défaut :
-- le blocage était levé. Désormais, pour les rôles `anon` et `authenticated`, la suppression d'une ligne bloquée est refusée
-- (42501). Une ligne non bloquée se supprime comme avant. Le rôle de service et le propriétaire ne sont pas touchés : le
-- backend retire toujours un membre bloqué (`DELETE /api/orgs/{org_id}/members/{user_id}`), tout comme les suppressions en
-- cascade (organisation, compte).
--
-- Reste hors de portée (voir docs/ARCHITECTURE.md) : TRUNCATE, qu'aucune route de l'API publique n'expose.
--
-- Additive et idempotente (CREATE OR REPLACE, DROP TRIGGER IF EXISTS). À appliquer après la 35.

create or replace function public.guard_member_notifications()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if current_user in ('anon', 'authenticated') then
        if tg_op = 'DELETE' then
            if not old.monthly_email_allowed then
                raise exception 'a membership blocked by the Nyra team is removed by the backend only' using errcode = '42501';
            end if;
        elsif tg_op = 'INSERT' then
            if not (new.monthly_email_allowed and new.monthly_email_wanted) then
                raise exception 'a new membership starts with both e-mail switches on' using errcode = '42501';
            end if;
        else
            if new.monthly_email_allowed is distinct from old.monthly_email_allowed then
                raise exception 'monthly_email_allowed is changed by the Nyra team only' using errcode = '42501';
            end if;
            if new.monthly_email_wanted is distinct from old.monthly_email_wanted and old.user_id is distinct from auth.uid() then
                raise exception 'monthly_email_wanted is changed by the person only' using errcode = '42501';
            end if;
        end if;
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

drop trigger if exists guard_member_notifications on public.memberships;
create trigger guard_member_notifications
    before insert or update or delete on public.memberships
    for each row execute function public.guard_member_notifications();
