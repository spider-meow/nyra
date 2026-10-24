# Nyra — schéma Supabase

Migrations SQL du produit hébergé (Postgres + Auth + Storage). Le détail
des tables est dans `docs/DATABASE_SCHEMA.md`.

```
supabase/migrations/
  20260920000001_extensions.sql                     pgcrypto, vector (pgvector)
  20260920000002_organizations_and_memberships.sql
  20260920000003_pipeline_core.sql                  sites, références, pages, images, matches, reviews
  20260920000004_crawl_runs_and_reports.sql
  20260920000005_row_level_security.sql
  20260920000006_storage_buckets.sql                buckets refs / site-images / reports + policies
  20260920000007_organization_slug.sql
  20260923000008_thumbnails_and_flip_hashes.sql     vignettes, hash miroir, index content_hash
  20260923000009_jobs.sql                           file de tâches (web → worker)
  20260923000010_org_settings.sql                   réglages par organisation
  20260924000011_excluded_hashes.sql                images exclues (faux positifs récurrents)
  20260925000012_brands.sql                         marques (étape 1, sans risque pour le code en place)
  20260925000013_brands_constraints.sql             marques (étape 2, après déploiement du code)
  20260926000014_reference_working_copies.sql       copie de travail des références (work_path)
  20260929000015_insights.sql                       mesures des lectures, poids et format des images, équipe Nyra, vues `insights`
  20260930000016_reference_tags.sql                 tags libres des références (colonne `tags text[]`, index GIN ; jamais lus par le matching)
  20261004000017_site_image_variants.sql            regroupement des recadrages d'une photo (`site_images.variant_group`)
  20261005000018_reference_locations.sql            « Où est-elle utilisée ? » : `reference_locations`, tâche `locate`
  20261009000019_unlimited_rights.sql               droits illimités (colonne `unlimited_rights`, contrôle date/illimité, vue `insights.library`)
  20261009000020_image_labels.sql                   étiquettes (`labels`, `image_labels`) : types d'images du site et contenus
  20261013000021_collections.sql                    collections (dossiers de la bibliothèque, 3 niveaux) et `reference_images.collection_id`
  20261014000022_brand_created_by.sql               auteur de la création d'une marque
  20261014000023_brand_logos.sql                    logo d'une marque
  20261017000024_set_aside_logos.sql                logos mis de côté (réglage par marque, `logo_score`, `logo_kept`)
  20261017000025_page_presence.sql                  `image_pages.last_seen`, `pages.links_tracked` : une image est-elle encore sur une page (additive ; une page compte « pas relue » tant qu'elle n'est pas relue)
  20261017000026_page_checks.sql                    tâche `check` (relire seulement quelques pages), `pages.read_started_at` (additive ; les pages déjà lues ont un début approximatif)
  20261017000027_page_replacements.sql              pages marquées « remplacée » à la main, par visuel (additive)
  20261020000029_read_health.sql                    santé d'une lecture (`crawl_runs.pages_ok`, `error_share`, `images_seen`…) et `suspect` : une lecture suspecte ne marque rien « retiré » (additive ; les lectures déjà faites ne sont pas suspectes)
  20261020000030_usage_quotas.sql                   compteurs d'usage par organisation (`usage_counters`) et limites propres à une organisation (`organizations.quota_overrides`) (additive ; à appliquer avant le code)
  20261020000031_audit_log.sql                      journal d'audit (`audit_log` : qui a fait quoi, lu par les admins de l'organisation, écrit par le backend, gardé 12 mois) et `deleted_organizations` (trace d'une suppression complète) (additive ; le numéro 030 est pris par une autre migration)
  20261021000032_one_running_job_per_org.sql       un seul job `running` par organisation (remplace l'index par marque de 013) et index `jobs_org_active_idx` (lu par `jobs/current`) ; échoue avec un message clair s'il y a déjà deux jobs `running` d'une même organisation. Ordre : arrêter les workers, vérifier qu'aucun job ne tourne, appliquer la 032, déployer le web, démarrer les nouveaux workers (voir DEPLOYMENT.md)
  20261024000033_brand_schedules.sql                lecture mensuelle automatique : table `brand_schedules` (par marque : activée, jour du mois, dernière mise en file ; lecture par les membres, écriture par le backend) (additive, vide et inactive au départ : à appliquer avant ou après le déploiement, avant de préférence)
```

## Marques (012 puis 013)

En deux temps, pour que le code en place continue de tourner entre les deux :

1. **012** : crée `brands`, une marque par organisation (même nom, même
   slug) qui reçoit tout l'existant, ajoute les colonnes `brand_id`, et un
   trigger range sous cette marque ce que l'ancien code écrit encore.
2. Déployer le code web et worker.
3. **013** : `brand_id` obligatoire, clés d'unicité par marque, un job
   actif par marque, suppression du trigger. (La **032** remplace ce dernier
   point : un job actif par organisation.)

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

Ou collez simplement 008 à 016 dans l'ordre dans le SQL Editor (012 et 013 en deux temps, voir « Marques » ci-dessus).

Après 008, les références et images existantes n'ont ni vignette ni hash
miroir : le worker les complète via une tâche `index` (voir
`docs/DEPLOYMENT.md`).

## À régler dans le dashboard

- **Authentication > Providers > Email** : désactiver *Allow new users to
  sign up*. L'accès se fait sur invitation (`nyra cloud-invite`).
- **Authentication > URL Configuration** : *Site URL* et redirections
  `<site>/login`, `<site>/set-password` (et les anciennes
  `<site>/connexion`, `<site>/mot-de-passe`, pour les liens déjà envoyés).

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

## Données des clients : journal, export, suppression, sauvegardes

- **Journal d'audit** : `audit_log` (migration 031). RLS : lecture par les administrateurs de
  l'organisation, aucune politique d'écriture (seul le backend écrit, avec la clé de service).
  Conservation 12 mois (`nyra cloud-purge-audit`, lancé aussi chaque jour par le worker).
- **Suppression complète d'une organisation** : toutes les tables suivent `organizations` par `on delete cascade`
  (vérifié sur le catalogue par `backend/tests/test_offboarding.py`) ; les fichiers Storage `{org_id}/…` sont
  supprimés par la commande, pas par la base. Une nouvelle table doit porter `org_id` avec la cascade.
- **Sauvegardes** : Supabase sauvegarde Postgres chaque jour (Pro), **pas les fichiers de Storage**. Procédure de
  copie des buckets, de sauvegarde de la base et de restauration d'une organisation : `docs/DEPLOYMENT.md`
  (rien n'a été joué à ce jour).

## Tests

`backend/tests/conftest.py` applique ces migrations sur un Postgres
jetable (avec des bouchons minimaux pour `auth` et `storage`) : voir
`docs/DEVELOPMENT.md`. Ne jamais pointer `TEST_DATABASE_URL` vers un vrai
projet.
