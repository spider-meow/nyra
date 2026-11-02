-- Lectures automatiques : la prochaine date à laquelle une marque est à regarder (AXE-709).
--
--   brand_schedules   next_due_at   la prochaine date de lecture de la marque, copie de ce que calcule
--                                   backend/nyra/cloud/schedule.py (`next_run`) : le worker ne lit plus que les
--                                   lignes dont la date est passée ou pas encore calculée (null), au lieu de
--                                   parcourir toutes les programmations actives (plafond de 2 000).
--
-- Le code reste la règle : la colonne n'en est qu'une copie indexée, remise à null à chaque changement
-- (activation, jour, lancement) et recalculée par le worker au passage suivant. Aucune valeur à remplir à la main :
-- les lignes existantes sont à null, donc recalculées d'elles-mêmes (une page de 100 par passage et par page lue).
--
-- Additive, inerte sans le code : sûre à appliquer avant OU après le déploiement. Ordre conseillé : appliquer
-- cette migration, puis déployer. Le worker sans la colonne l'écrit en un avertissement par heure
-- (« appliquez la migration 20261102000047 ») et continue avec l'ancien parcours : aucune lecture n'est perdue.

alter table public.brand_schedules add column if not exists next_due_at timestamptz;

-- Les marques à regarder : active, et date passée ou pas encore calculée.
create index if not exists brand_schedules_next_due_idx on public.brand_schedules(next_due_at) where enabled;
