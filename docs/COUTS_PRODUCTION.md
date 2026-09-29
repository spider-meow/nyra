# Coûts de production — estimation

Estimation du coût d'infrastructure mensuel pour faire tourner Nyra en
production, à partir de l'architecture actuelle du dépôt. Prix relevés
fin septembre 2026, en euros HT, avec 1 $ ≈ 0,86 €.

## Point de départ : ce que le code fait déjà

- **Base de données.** Le produit hébergé tourne déjà sur Supabase :
  Postgres avec pgvector (`embedding vector(512)`), Auth et Storage.
  SQLite ne sert plus qu'à la CLI hors ligne (debug, calibration).
  **Aucune migration SQLite → Postgres à chiffrer.**
- **Multi-tenant.** Déjà en place : `org_id` sur toutes les tables, RLS
  (`20260920000005_row_level_security.sql`), rôles admin/client, objets
  Storage préfixés par `{org_id}/`, un seul job actif par organisation.
  Une marque correspond à une organisation. Un groupe qui gère plusieurs
  marques peut aussi n'avoir qu'une organisation avec plusieurs sites.
  **Aucun développement multi-tenant à chiffrer.**
- **Deux processus** (`docs/DEPLOYMENT.md`) :
  - `web` : FastAPI plus l'interface React compilée, léger ;
  - `worker` : Chromium, CLIP sur CPU et les jobs.

## Volumes mesurés sur remymartin.com

Mesure faite avec les fonctions du crawler et du matcher de Nyra
(`extract_images_from_html`, filtre de taille minimale, dédoublonnage par
hash de contenu, vignettes, lot CLIP ViT-B-32). Les pages HTML ont été
récupérées sans Chromium : le site est rendu côté serveur (WordPress),
mais les fonds CSS et le lazy-loading JavaScript n'ont pas été vus. Il
faut donc lire le nombre d'images comme un plancher.

| Mesure (300 pages, sur 508 URL de sitemap) | Valeur |
|---|---|
| Occurrences d'images dans les pages | 9 839 |
| URL d'images distinctes | 1 500 |
| Images stockées (≥ 200 px, octets uniques) | **1 190** |
| Poids des originaux + vignettes | 179 Mo + 16 Mo ≈ **0,2 Go** |
| Poids moyen d'un original | 150 Ko |
| CLIP sur CPU (4 vCPU), après 14 s de chargement du modèle | **33 images/s**, soit 36 s pour tout le site |

**CPU ou GPU ? Le CPU suffit largement.** Le premier passage complet
d'une marque demande environ une minute de calcul CLIP. Les passages
hebdomadaires ne calculent que les nouvelles images : une URL déjà
connue n'est ni retéléchargée ni ré-embeddée (`known_images`), et des
octets déjà vus sous une autre URL sont réutilisés. Cela représente
quelques secondes par semaine. Le temps d'un crawl vient de Chromium et
des délais de politesse (3 pages en parallèle, 1 à 2 s d'attente par
page), pas de CLIP. Compter 10 à 20 minutes pour 300 pages. Un GPU
coûterait plus cher à démarrer que le calcul lui-même.

## Fournisseurs retenus

| Brique | Retenu (bas) | Variante (haut) | Pourquoi |
|---|---|---|---|
| Postgres + pgvector, Auth, Storage | Supabase Pro, région Paris (eu-west-3) : 25 $ | Idem + compute Small (+5 $) | Le code en dépend déjà (Auth, Storage, RLS, pgvector) : zéro migration. Le plan Pro inclut 8 Go de base, 100 Go de stockage, 250 Go d'egress et des sauvegardes quotidiennes. Le plan gratuit ne convient pas : un projet est mis en pause après 7 jours d'inactivité. |
| Worker (Chromium + CLIP) | Hetzner CX33 : 4 vCPU, 8 Go, 8,49 € + IPv4 à 0,50 € | Scaleway BASIC2-A2C-8G, Paris : ≈ 25 € + IPv4 ≈ 3 € | Il faut 2 Go minimum (Chromium + CLIP) : 8 Go laissent de la marge. Hetzner est dans l'UE et c'est le moins cher. Scaleway si un client exige un hébergement en France. |
| Web + frontend | Même VPS que le worker (`docker compose` + Caddy pour le TLS) : 0 € | Vercel Pro : 20 $ (le plan Hobby interdit l'usage commercial) | Le process web demande 256 à 512 Mo et tient à côté du worker. Le dépôt est déjà prêt pour Vercel (`vercel.json`, `app.py`). |
| E-mails (alertes + invitations Auth) | Resend gratuit : 3 000/mois, 100/jour | Resend Pro : 20 $ | Il faut de toute façon un SMTP personnalisé pour Supabase Auth : le SMTP intégré est limité à quelques e-mails par heure. |
| Monitoring | Offres gratuites (Sentry Developer, Better Stack uptime) | Sentry Team ≈ 26 $ | Surveiller les jobs en échec et le healthcheck `/api/healthz`. |
| Sauvegarde des fichiers Storage | — | Copie vers Scaleway Object Storage ≈ 1 € | Les sauvegardes Supabase couvrent la base, pas les fichiers. |
| Nom de domaine | ≈ 1 € | ≈ 1 € | |

## Tableau des coûts (€ HT/mois)

Chaque cellule donne la fourchette basse – haute.

| Poste | Pilote (1 marque) | 3 marques | 6 marques | 12 marques |
|---|---|---|---|---|
| Supabase (base + pgvector, Auth, Storage) | 21,5 – 26 | 21,5 – 26 | 21,5 – 26 | 21,5 – 26 |
| ↳ dont stockage images au-delà de 100 Go | 0 | 0 | 0 | 0 |
| Worker crawl + CLIP (CPU) | 9 – 28 | 9 – 28 | 9 – 28 | 9 – 28 |
| GPU | 0 | 0 | 0 | 0 |
| Web + frontend | 0 – 17 | 0 – 17 | 0 – 17 | 0 – 17 |
| E-mails d'alerte | 0 – 17 | 0 – 17 | 0 – 17 | 0 – 17 |
| Monitoring | 0 – 22 | 0 – 22 | 0 – 22 | 0 – 22 |
| Sauvegarde Storage + domaine | 1 – 2 | 1 – 2 | 1 – 2 | 1 – 2 |
| **Total** | **≈ 32 – 112** | **≈ 32 – 112** | **≈ 32 – 112** | **≈ 32 – 112** |

Même à 12 marques, le stockage estimé (15 à 65 Go) reste sous les
100 Go inclus dans Supabase Pro.

## Ce qui scale, ce qui ne scale pas

- **Fixe** (ne bouge quasiment pas avec N) : plan Supabase, Auth,
  backend web, monitoring, domaine, et le worker lui-même. Un seul worker
  traite les crawls les uns après les autres. À 10–20 min par marque,
  12 marques représentent 2 à 4 heures par semaine : une nuit suffit.
- **Linéaire en N, mais absorbé par les quotas inclus** : stockage des
  images (environ 0,2 Go de site + la bibliothèque de références par
  marque), lignes en base (environ 10 Mo par marque), temps de crawl,
  e-mails (quelques dizaines par marque et par mois).
- **Par paliers** : un worker de plus (≈ 9 €) quand la somme des crawls
  ne tient plus dans la fenêtre de nuit, vers 20–25 marques à 300 pages.
  Le stockage n'est facturé qu'au-delà de 100 Go.

**Formule :**

```
C(N) ≈ F
     + 9 € × (⌈N / N_w⌉ − 1)                  worker supplémentaire
     + 0,018 €/Go × max(0, N × s − 100 Go)    stockage au-delà du quota
```

avec :

- `F` = 32 € (bas) ou 112 € (haut) ;
- `s` = stockage par marque, entre 1,2 et 5,5 Go (voir hypothèses) ;
- `N_w` ≈ 20 à 25 marques par worker (crawls de 300 pages, fenêtre de
  8 h).

Jusqu'à une vingtaine de marques, **C(N) ≈ F** : le coût marginal
d'infrastructure d'une marque est d'environ 0 €. Lissé sur les paliers,
il tourne autour de **0,5 € par marque et par mois**. Le vrai coût
marginal d'une marque est humain (onboarding, bibliothèque de
références, revue), pas l'infrastructure.

## Développement restant (coût ponctuel, distinct de l'infra)

| Sujet | État dans le code | Estimation |
|---|---|---|
| Migration SQLite → Postgres/pgvector | Fait (`supabase/migrations/`) | 0 j |
| Multi-tenant (RLS, `org_id`) | Fait | 0 j |
| Run hebdomadaire automatique | **Absent.** Les crawls ne partent que depuis l'interface ou l'API. À ajouter : un réglage par organisation et un déclencheur (pg_cron, inclus dans Supabase, qui insère un job `crawl`). **Attention :** le job doit passer `fresh: true`. Sinon le mode reprise saute toutes les pages déjà visitées (`crawl.py`, `discover`) et le passage hebdomadaire ne revérifie rien. Les images déjà connues ne sont de toute façon pas retéléchargées. | 1,5 – 2 j |
| E-mails d'alerte | **Absent** (seuls les e-mails d'invitation Supabase existent). À ajouter : destinataires par organisation, envoi après le passage de comparaison (nouveaux matchs, expirations proches), gabarit, intégration Resend. | 2 – 3 j |
| SMTP personnalisé pour Supabase Auth | Configuration seulement | 0,5 j |
| Alerting sur jobs en échec + purge des images disparues du site | Absent, optionnel pour le pilote | 1 – 2 j |
| **Total** | | **≈ 4 – 7,5 j** |

## Hypothèses à ajuster

- **Pages par marque : 300** (`crawl.max_pages` par défaut). remymartin.com
  en déclare 508 dans ses sitemaps. Un site multi-pays plus gros peut
  monter jusqu'au plafond de 2 000 pages : environ 8 000 images, 1,3 Go
  et environ 1 h de crawl. Le total mensuel ne change pas.
- **Images par page** : environ 4 images uniques stockées par page
  (1 190 pour 300 pages), mesuré sur le HTML statique. Avec Chromium
  (fonds CSS, lazy-loading), compter 1 200 à 2 000 images par marque.
- **Bibliothèque de références : 200 à 1 000 images par marque, de 5 Mo
  en moyenne** (HD fournies par la marque ; l'upload est plafonné à
  30 Mo), soit 1 à 5 Go. C'est le poste de stockage principal, et
  l'hypothèse la plus incertaine.
- **Renouvellement du contenu** : 20 à 60 nouvelles images par semaine et
  par marque, soit environ 0,2 à 0,5 Go par an. Rien n'est purgé
  aujourd'hui : le stockage ne fait que croître, mais lentement.
- **Utilisateurs** : moins de 10 par marque. On reste loin des 100 000
  MAU et des 250 Go d'egress inclus : l'interface n'affiche que des
  vignettes d'environ 14 Ko via des URL signées.
- **Fréquence** : un run par semaine et par marque. Passer à un run
  quotidien multiplie le temps worker par 7 (12 marques ≈ 20 à 30 h par
  semaine) : un worker suffit encore, mais la fenêtre de nuit ne tient
  plus, et on passe à 2 workers vers 8–10 marques.
- **Prix** : relevés en septembre 2026 (Hetzner après la hausse du 15 juin
  2026, Supabase, Resend, Scaleway). Le prix de Sentry, l'IPv4 Scaleway
  et le taux de change sont approximatifs.
