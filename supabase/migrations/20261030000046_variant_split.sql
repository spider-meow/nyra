-- Scinder un groupe à la main (AXE-521) : une personne dit « cette version n'est pas la même photo que le groupe ».
--
-- Le regroupement des recadrages (`variant_group`) est automatique et peut se tromper (deux photos qui partagent un bandeau ou un gabarit). Une image
-- séparée à la main reçoit `variants_split_at` : elle sort de son groupe et le regroupement automatique ne la rattache plus à aucun (il ne la cherche pas
-- et ne la propose à personne). Séparer ne cache rien : l'image a sa propre carte, il y a seulement plus de cartes.
--
-- Additive et nulle par défaut : sans effet tant que le code ne l'utilise pas, et le code tourne aussi sans elle (la route répond 409, le regroupement
-- automatique est inchangé).

alter table public.site_images
    add column if not exists variants_split_at timestamptz;
