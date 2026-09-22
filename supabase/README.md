# RightsWatch — schéma Supabase

Migrations SQL pour faire tourner RightsWatch sur Supabase (Postgres +
Auth + Storage) au lieu du SQLite local actuel. **Rien n'a été touché sur
un projet Supabase réel** — ces fichiers sont prêts à être appliqués quand
le projet sera créé.

Testées de bout en bout sur un Postgres 16 + pgvector local (extensions,
tables, triggers, policies RLS avec de vrais scénarios multi-org/multi-rôle
— voir plus bas) avant d'être livrées ici.

## Contenu

```
supabase/migrations/
  20260922000001_extensions.sql                  pgcrypto, vector (pgvector)
  20260922000002_organizations_and_memberships.sql   organizations, memberships, RLS helpers
  20260922000003_pipeline_core.sql                sites, reference_images, pages, site_images,
                                                    image_pages, matches, reviews, match_meta
  20260922000004_crawl_runs_and_reports.sql       historique des crawls + rapports générés
  20260922000005_row_level_security.sql           policies RLS sur toutes les tables
  20260922000006_storage_buckets.sql              buckets refs/site-images/reports + policies
```

Numérotés comme des migrations Supabase CLI (`YYYYMMDDHHMMSS_nom.sql`) —
appliqués dans l'ordre du nom de fichier, un seul sens de lecture possible.

## Comment les appliquer

**Option A — Supabase CLI** (recommandé une fois le projet créé) :

```bash
supabase link --project-ref <ref-du-projet>
supabase db push
```

**Option B — SQL Editor du dashboard Supabase** : coller chaque fichier
dans l'ordre (0001 → 0006) et exécuter. Fonctionne tout aussi bien, plus
manuel.

Les deux extensions (`pgcrypto`, `vector`) sont sur la liste blanche de
Supabase et s'activent directement depuis le SQL, pas besoin de passer par
l'onglet Database > Extensions du dashboard avant.

## Comment j'ai validé ces fichiers

Sans accès à un vrai projet Supabase dans cette session, j'ai monté un
Postgres 16 + pgvector local, recréé des stubs minimaux de `auth.users`,
`auth.uid()` (lu depuis `request.jwt.claims`, comme le fait réellement
Supabase) et `storage.buckets`/`storage.objects`, puis :

1. Appliqué les 6 fichiers dans l'ordre — aucune erreur SQL.
2. Créé 2 organisations, 1 admin et 1 client dans l'organisation A, des
   lignes dans `reference_images`/`site_images`/`matches`/`reviews`, et
   vérifié en changeant de rôle Postgres (`set local role authenticated`
   + `set local request.jwt.claims`) que :
   - un membre ne voit que les lignes de sa propre organisation (testé sur
     `reference_images` et sur `storage.objects`),
   - un `client` peut écrire dans `reviews` mais pas dans
     `reference_images`/`site_images` (réservé aux `admin`),
   - un `client` ne peut pas écrire une review pointant vers une autre
     organisation,
   - un utilisateur sans session (`auth.uid()` null) ne voit rien,
   - un `admin` peut uploader dans le bucket `refs` de son organisation,
     un `client` non.

Ce n'est pas un test automatisé qui tourne en CI (pas de projet Supabase
disponible pour ça) — si vous voulez le rejouer, le script est dans
l'historique de cette conversation ; il vaut le coup de le refaire une
fois le vrai projet créé, au moins une fois.

## Modèle retenu (voir la conversation pour le détail des arbitrages)

- **Un seul projet Supabase partagé**, isolation par organisation via
  `org_id` + Row Level Security — pas un projet par client.
- **Deux rôles** : `admin` (équipe Axel — bibliothèque, crawl, seuils,
  gestion des membres) et `client` (lecture + revue des matches
  uniquement).
- **Le pipeline (Playwright + CLIP) reste un serveur dédié**, pas des
  Edge Functions — il se connecte à Postgres avec la clé service-role
  (qui bypasse RLS ; l'autorisation pour les actions du pipeline est
  vérifiée côté backend Python, pas par les policies). Les policies RLS
  sont le filet de sécurité pour tout accès direct à Postgres/Storage en
  dehors du backend.
- **pgvector** pour les embeddings CLIP (`vector(512)`, dimension de
  ViT-B-32) au lieu des bytes bruts stockés en SQLite — pas d'index ANN
  pour l'instant, le matching compare exhaustivement (voir
  `0003_pipeline_core.sql` pour le détail).
- **Tout dans Supabase Storage** : `refs/{org_id}/...`,
  `site-images/{org_id}/...`, `reports/{org_id}/{report_id}/...`.
- **On part propre** : pas de reprise des données locales existantes.

## Ce qui n'est PAS dans ces fichiers

- **La connexion réelle à un projet Supabase** — personne n'a créé de
  projet, ces migrations n'ont jamais touché une instance Supabase.
- **Le code applicatif** (`backend/rightswatch/db.py`, `fetch.py`,
  `refs.py`, `report.py`, `api.py`) parle encore à SQLite + au disque
  local. Faire tourner le produit sur ce schéma demande de réécrire la
  couche `db.py` (connexion Postgres, ids en `uuid`, `org_id` sur les
  fonctions), et de faire passer `fetch.py`/`refs.py`/`report.py` par
  l'API Storage au lieu de `Path.write_bytes`/`open()`. C'est le chantier
  suivant une fois le projet Supabase créé.
- **L'intégration Brandcenter** — explicitement mise de côté pour plus
  tard ; le connecteur `RefSource` (`backend/rightswatch/refs.py`) reste
  le point d'extension prévu pour ça, indépendant de ce schéma.
- **La première organisation/le premier admin** — se créent via la clé
  service-role au moment de l'onboarding d'un client (voir le commentaire
  dans `0005_row_level_security.sql` sur pourquoi ça ne peut pas se faire
  depuis une session utilisateur classique).
