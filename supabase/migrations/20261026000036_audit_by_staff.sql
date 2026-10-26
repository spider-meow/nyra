-- Journal d'audit : les actions de l'équipe Nyra (platform admins) ne sont lues que par l'équipe Nyra.
--
--   audit_log.by_staff   vrai quand l'auteur était un platform admin (`platform_staff`) sans être membre de l'organisation ; calculé par
--                        le backend à l'écriture (backend/nyra/cloud/audit.py). Un platform admin agit dans tout espace client
--                        sans en être membre (AXE-573) : sa trace reste côté équipe (`GET /api/staff/organizations/{id}/audit`),
--                        le journal du client (`GET /api/orgs/{id}/audit` et la lecture publique ci-dessous) ne la montre pas.
--                        Les lignes d'avant la migration restent à faux : elles ne sont pas réécrites.
--
-- Additive : sûre à appliquer avant de déployer le code qui s'en sert.

alter table public.audit_log add column if not exists by_staff boolean not null default false;

drop policy if exists "org admins can read audit_log" on public.audit_log;
create policy "org admins can read audit_log"
    on public.audit_log for select
    using (public.is_org_admin(org_id) and not by_staff);
