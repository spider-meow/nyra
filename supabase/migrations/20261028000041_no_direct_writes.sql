-- Plus aucune écriture directe pour les rôles de l'API publique (AXE-625, qui ferme AXE-622, AXE-623 et AXE-624).
--
-- L'interface n'écrit jamais dans Postgres ni dans le stockage (`frontend/src` n'a aucun `supabase.from`, `rpc` ni `storage` :
-- la session Supabase ne sert qu'à se connecter, tout passe par `/api`), et le backend écrit avec le rôle de service ou le
-- propriétaire, qui contournent RLS. Les politiques d'écriture ne servaient donc qu'à quelqu'un qui appelle PostgREST ou le
-- stockage à la main avec sa session : chemins de fichiers d'une autre organisation, quotas contournés, correspondances et
-- décisions inventées (voir `docs/DATABASE_SCHEMA.md`, « Write policies »).
--
-- 1. supprime les politiques `FOR ALL` d'écriture des tables et de `storage.objects` (dossier `refs`) ;
-- 2. retire `INSERT`, `UPDATE` et `DELETE` au rôle `authenticated` sur toutes les tables de `public`, et pour les tables à venir :
--    la protection ne tient plus à une seule politique ;
-- 3. garde les lectures (« org members can read … ») : la migration s'arrête si une table perdait ainsi sa seule politique de lecture.
--
-- Idempotente. À appliquer sur le vrai projet Supabase par une personne qui y a accès : les droits par défaut y sont ceux de Supabase
-- (ils n'existent pas dans la base des tests, qui n'a pas ces rôles).

do $$
declare
    doomed record;
begin
    for doomed in
        select schemaname, tablename, policyname from pg_policies
        where (schemaname = 'public' and cmd = 'ALL' and policyname in (
                'org admins can write brands', 'org admins can write sites', 'org admins can write reference_images',
                'org admins can write pages', 'org admins can write site_images', 'org admins can write image_pages',
                'org admins can write matches', 'org members can write reviews', 'org admins can write match_meta',
                'org admins can write crawl_runs', 'org admins can write reports', 'org admins can write org_settings',
                'org admins can write excluded_hashes', 'org admins manage memberships'))
           or (schemaname = 'storage' and tablename = 'objects' and policyname = 'org admins can write ref images')
    loop
        if not exists (select 1 from pg_policies p where p.schemaname = doomed.schemaname and p.tablename = doomed.tablename
                       and p.cmd = 'SELECT') then
            raise exception 'La table %.% perdrait sa lecture : aucune politique SELECT à côté de %',
                doomed.schemaname, doomed.tablename, doomed.policyname;
        end if;
        execute format('drop policy %I on %I.%I', doomed.policyname, doomed.schemaname, doomed.tablename);
    end loop;

    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke insert, update, delete on all tables in schema public from authenticated;
        alter default privileges in schema public revoke insert, update, delete on tables from authenticated;
    end if;
end $$;
