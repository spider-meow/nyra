# Images en ligne : mesures de performance (AXE-743)

Base de test locale (Postgres 16 + pgvector), une marque de **30 000 images** lues sur un site, 3 000 pages, aucune exclusion,
aucune correspondance dans la bibliothèque. Une seule mesure par ligne, sur une machine de développement : à lire comme un ordre de grandeur.

| Étape | Durée |
|---|---|
| `online_images`, première fois (lecture + regroupement de toutes les images) | 0,7 à 0,85 s |
| Empreinte de l'ancien cache du compteur (`unreferenced_signature`) | 0,06 s |
| Nouvelle empreinte (`site_images_signature`, couvre aussi taille, vignette, comparaison, score de logo, pages) | 0,18 s |
| `online_images`, empreinte inchangée (réponse gardée en mémoire) | 0,18 s |

Ce qui est fait : la réponse de `online_images` est gardée par marque (4 marques au plus) tant que l'empreinte calculée dans la base
ne change pas ; les demandes suivantes (autre page, autre filtre de type, compteurs) passent de ~0,8 s à ~0,2 s.

Ce qui n'est pas fait (à ne pas présenter comme tel) :
- la **première** demande après un changement lit et regroupe encore toutes les images (~0,8 s à 30 000) ;
- les comptes et le regroupement ne sont **pas** faits en SQL : l'exclusion se calcule en Python sur des distances de hash
  (`match.excluded_site_ids`), et le regroupement par photo (`group_rows`) aussi. Les passer en SQL demande de refaire ces deux règles
  en base, avec des tests de comparaison ancien / nouveau chemin sur les mêmes données ;
- la mémoire d'un processus `web` grandit avec le nombre d'images des 4 marques gardées.
