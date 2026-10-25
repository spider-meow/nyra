-- E-mails de Nyra : deux interrupteurs par membre et par organisation (AXE-523). Seul e-mail concerné aujourd'hui : le résumé
-- mensuel. Les e-mails de service (invitation, nouveau mot de passe) sont ceux de Supabase Auth : ils ne passent pas par ici.
--
--   memberships.monthly_email_allowed   décidé par l'équipe Nyra (platform admins), vrai par défaut : faux = rien n'est envoyé à
--                                       cette personne, quoi qu'elle ait choisi ;
--   memberships.monthly_email_wanted    décidé par la personne elle-même (Réglages > Notifications), vrai par défaut.
--
-- Règle de réception (backend/nyra/cloud/digest.py, `receives_monthly`) : administrateur ET autorisé ET souhaité.
-- Les lignes existantes prennent « vrai » aux deux : rien ne change tant que personne n'y touche.
--
-- Écritures : le backend écrit avec le rôle de service. Mais la politique « org admins manage memberships » (FOR ALL) laisse
-- un administrateur modifier n'importe quelle ligne de son organisation par l'API publique de Supabase, y compris sa propre
-- autorisation, ce que l'équipe Nyra a bloqué. Le déclencheur ci-dessous le refuse pour les rôles de l'API publique (`anon`,
-- `authenticated`) : l'autorisation n'est modifiable que par le backend ; le souhait d'une personne que par elle-même ; une
-- ligne créée par l'API publique commence avec les deux à « vrai ». Le rôle de service et le propriétaire ne sont pas touchés.
--
-- Additive et idempotente. À appliquer AVANT le déploiement du code : sans ces colonnes, l'envoi de l'e-mail mensuel s'arrête
-- (un avertissement par heure dans le journal du worker, rien n'est marqué ni envoyé) et la page des comptes du back office
-- n'affiche rien ; les routes de réglages des notifications répondent 500.

alter table public.memberships add column if not exists monthly_email_allowed boolean not null default true;
alter table public.memberships add column if not exists monthly_email_wanted boolean not null default true;

comment on column public.memberships.monthly_email_allowed is
    'Décidé par l''équipe Nyra : faux = aucun e-mail mensuel pour cette personne dans cette organisation. Vrai par défaut.';
comment on column public.memberships.monthly_email_wanted is
    'Choix de la personne (Réglages > Notifications) : faux = elle ne veut plus recevoir l''e-mail mensuel. Vrai par défaut.';

create or replace function public.guard_member_notifications()
returns trigger
language plpgsql
as $$
begin
    if current_user in ('anon', 'authenticated') then
        if tg_op = 'INSERT' then
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
    return new;
end;
$$;

drop trigger if exists guard_member_notifications on public.memberships;
create trigger guard_member_notifications
    before insert or update on public.memberships
    for each row execute function public.guard_member_notifications();
