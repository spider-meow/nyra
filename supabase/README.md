# Nyra — schéma Supabase

Migrations SQL du produit hébergé (Postgres + Auth + Storage). Le détail
des tables est dans `docs/DATABASE_SCHEMA.md`.

```
supabase/migrations/
  20260920000001_extensions.sql                  pgcrypto, vector (pgvector)
  20260920000002_organizations_and_memberships.sql
  20260920000003_pipeline_core.sql               sites, références, pages, images, matches, reviews
  20260920000004_crawl_runs_and_reports.sql
  20260920000005_row_level_security.sql
  20260920000006_storage_buckets.sql             buckets refs / site-images / reports + policies
  20260920000007_organization_slug.sql
  20260923000008_thumbnails_and_flip_hashes.sql  vignettes, hash miroir, index content_hash
  20260923000009_jobs.sql                        file de tâches (web → worker)
  20260923000010_org_settings.sql                réglages par organisation
```

Les noms suivent le format attendu par le CLI Supabase
(`YYYYMMDDHHMMSS_nom.sql`).

## Appliquer les migrations

**Projet neuf** : `supabase link --project-ref <ref>` puis
`supabase db push`, ou coller les fichiers dans l'ordre dans le SQL Editor.

**Projet où 001 à 007 ont déjà été collées à la main** (avant le passage
au format horodaté) : le CLI ne sait pas qu'elles sont appliquées.
Marquez-les comme telles, puis poussez les suivantes :

```bash
supabase migration repair --status applied 20260920000001 20260920000002 20260920000003 \
  20260920000004 20260920000005 20260920000006 20260920000007
supabase db push
```

Ou collez simplement 008, 009 et 010 dans le SQL Editor.

Après 008, les références et images existantes n'ont ni vignette ni hash
miroir : le worker les complète via une tâche `index` (voir
`docs/DEPLOYMENT.md`).

## À régler dans le dashboard

- **Authentication > Providers > Email** : désactiver *Allow new users to
  sign up*. L'accès se fait sur invitation (`nyra cloud-invite`).
- **Authentication > URL Configuration** : *Site URL* et redirections
  `<site>/connexion`, `<site>/mot-de-passe`.

## Modèle

- Un seul projet Supabase, isolation par `org_id` + Row Level Security.
- Deux rôles : `admin` (bibliothèque, lectures, rapports, réglages) et
  `client` (lecture et décisions).
- Le backend se connecte avec un rôle privilégié (bypass RLS) et filtre
  lui-même sur `org_id` ; les policies RLS sont le filet de sécurité pour
  tout autre accès.
- pgvector pour les embeddings CLIP (`vector(512)`).
- Tout le binaire dans Storage : `refs/{org_id}/...`,
  `site-images/{org_id}/...`, `reports/{org_id}/{report_id}/...`,
  vignettes dans `{org_id}/thumbs/`.

## Tests

`backend/tests/conftest.py` applique ces migrations sur un Postgres
jetable (avec des bouchons minimaux pour `auth` et `storage`) : voir
`docs/DEVELOPMENT.md`. Ne jamais pointer `TEST_DATABASE_URL` vers un vrai
projet.
