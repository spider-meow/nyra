-- Ordre choisi des collections et sous-collections (AXE-629) : `position` range les enfants d'un même parent (ou du premier niveau).
-- Jusqu'ici l'ordre était toujours alphabétique ; la lecture trie maintenant par `position`, puis par nom pour départager.
--
-- Les collections existantes gardent leur ordre alphabétique d'avant (rang dans leur parent) : rien ne change à l'écran à
-- l'application. Les nouvelles arrivent à la fin de leur niveau. Le rangement n'est fait qu'à l'ajout de la colonne : rejouer
-- la migration ne remet pas l'ordre choisi à l'alphabet.
--
-- Additive et idempotente : sans risque à appliquer avant le déploiement du code qui l'utilise (l'ancien code ignore la colonne).

do $$
begin
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'collections' and column_name = 'position') then
        alter table public.collections add column position integer not null default 0;
        update public.collections c set position = ranked.rank
        from (select id, row_number() over (partition by brand_id, parent_id order by lower(name)) - 1 as rank
              from public.collections) ranked
        where ranked.id = c.id;
    end if;
end $$;

create index if not exists collections_order_idx on public.collections (brand_id, parent_id, position);
