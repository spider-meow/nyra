# Audit produit — Nyra

Audit du 29 septembre 2026, fait à partir de la lecture de la doc et du code
(`README`, `ARCHITECTURE`, `MATCHING`, `COUTS_PRODUCTION`, `DEPLOYMENT`,
`cloud/api.py`, `cloud/auth.py`, `config.yaml`, pages Tableau de bord et
À traiter du frontend) et de l'exécution de la suite de tests backend.

**Non vérifié :** le produit n'a pas été essayé de bout en bout avec de
vraies données (la bibliothèque de Rémy Martin est vide en local), `.env`
n'a pas été lu, le workflow CI n'a pas été relu, les tests cloud n'ont pas
tourné (pas de base de test). Tout ce qui touche à la performance réelle
est donc une lecture de la doc, pas une mesure.

---

## 1. En bref

Le cœur du produit est solide et bien documenté. Ce qui manque pour le
présenter comme un service, ce n'est pas de la technique de détection,
c'est **la boucle qui rend le produit utile sans qu'on y pense** : un
passage automatique chaque semaine, et un e-mail quand quelque chose
apparaît. Aujourd'hui, il faut se connecter et cliquer.

| | |
|---|---|
| Détection | Solide sur le papier, à valider sur de vrais cas |
| Sécurité | Bonne base, deux points à traiter avant un client |
| Interface | Soignée, en français, parcours clair |
| Automatisation | **Absente** (pas de passage planifié, pas d'alertes) |
| Tests | Backend correct, cloud non exécuté en local, frontend sans test |
| Coût | Faible : ≈ 32 à 112 €/mois d'infrastructure |

---

## 2. Ce qui va

**Détection déterministe et explicable.** Pas de LLM : hashes perceptuels,
puis CLIP, puis une vérification géométrique par points-clés (SIFT +
RANSAC). Chaque correspondance a un niveau de confiance (confirmé,
probable, à vérifier). C'est un argument fort face à un manager : les
résultats sont reproductibles et on sait dire pourquoi une image est
proposée. Sur 400 candidats CLIP, le niveau 3 en écarte 42 % (autres prises
de vue des mêmes bouteilles, caves, vignobles), vérifiés à l'œil.

**Mesures déjà faites.** Sur 1 057 images réelles d'un site de marque :
97,5 % des copies retouchées retrouvées (84 % en « confirmé »), 0 % de
faux positifs sur 300 paires d'images différentes (`MATCHING.md`).
Limite : voir 3.3.

**Architecture propre.** Deux processus (web léger, worker lourd), file de
tâches dans Postgres (reprise après crash, un seul job actif par marque),
même pipeline en local (SQLite) et hébergé. Une marque = une bibliothèque
+ plusieurs sites (un par marché).

**Sécurité de base sérieuse.** Pas d'inscription publique, JWT vérifié
localement, contrôle d'appartenance et de rôle sur chaque route, RLS en
filet de sécurité, crawler protégé contre le SSRF (adresses privées,
métadonnées cloud), tailles et pixels plafonnés, en-têtes CSP.

**Parcours utilisateur pensé.** Mise en route en trois étapes, tri par
urgence d'expiration, comparaison côte à côte, décision annulable, tableau
de bord avec un chiffre central (« visuels expirés encore en ligne »),
rapport HTML/PDF et CSV. C'est démontrable en 10 minutes.

**Mesure et exploitation.** Statistiques par lecture et par marque, back
office interne, logs JSON, Sentry prévu. Documentation abondante (9
documents), 72 tests backend qui passent.

**Coût maîtrisé.** Pas de GPU, pas d'API payante par image : le coût par
marque supplémentaire est proche de 0 (voir 5).

---

## 3. Ce qui ne va pas

### 3.1 Pas d'automatisation (le plus important)

- **Aucun passage planifié.** Les lectures ne partent que depuis
  l'interface. Un outil de surveillance qu'il faut penser à lancer perd
  l'essentiel de sa valeur. `COUTS_PRODUCTION.md` l'estime à 1,5–2 jours
  (pg_cron qui insère un job, avec `fresh: true`, sinon le mode reprise
  saute toutes les pages déjà vues).
- **Aucune alerte.** Seuls les e-mails d'invitation existent. Pas de
  résumé « 3 nouveaux visuels expirés détectés », pas de rappel « 5
  visuels expirent dans 30 jours ». Estimation : 2–3 jours (Resend).
- **Pas d'alerte sur les échecs** de lecture (optionnel dans le chiffrage).

### 3.2 Sécurité et conformité

- **Pas de limitation de débit** sur l'API (aucune trace de rate limiting
  dans le backend). Un compte compromis ou un script peut enchaîner les
  téléversements de 30 Mo et les tâches. À traiter avant d'ouvrir à des
  clients.
- **Le backend utilise la clé `service_role`**, qui contourne la RLS :
  chaque requête doit filtrer sur `org_id` elle-même. La sécurité repose
  donc sur la discipline du code et sur les tests cloud, qui **sont
  ignorés sans base de test** (50 tests sur 122 sautés dans ma
  exécution). À faire tourner en CI sur une vraie base.
- **Clé `service_role` dans un dossier synchronisé par OneDrive** (le
  projet est dans `Documents/`, OneDrive). La doc elle-même demande de ne
  pas laisser `.env` dans un dossier synchronisé. Je n'ai pas lu le
  fichier ; à vérifier, et à déplacer hors OneDrive si la clé est réelle.
- **Pas de double authentification** mentionnée (dépend de la config
  Supabase).
- **Aucune purge** : les images du site ne sont jamais supprimées, même
  celles qui ont disparu. Coût faible, mais c'est aussi un sujet de
  conformité : Nyra conserve des copies d'images de tiers. À cadrer
  (durée de conservation, droit à l'effacement d'une marque).
- **Identifiant du robot à corriger.** `config.yaml` annonce
  `contact: rights-team@example.com` et une URL GitHub personnelle. Avant
  de crawler le site d'un client, mettre une vraie adresse de contact et
  une page d'explication.

### 3.3 Fiabilité de la détection à démontrer

- Les 97,5 % viennent de **copies retouchées fabriquées** à partir des
  images du site. C'est une bonne mesure de robustesse aux retouches, pas
  une mesure sur de vrais cas de réutilisation. Le rappel réel est
  inconnu.
- Le rappel n'est mesurable qu'avec des paires connues à la main
  (`ground_truth.csv`) ; les décisions de l'interface ne mesurent que la
  précision.
- Limites déjà connues (`MATCHING.md`) : recadrages très serrés, rotations
  autres que le miroir, détourages du même produit sur un autre fond
  (rangés en « à vérifier »).
- **Sites protégés.** Un site derrière un pare-feu anti-bot (Cloudflare,
  etc.) peut bloquer un crawl depuis l'IP d'un hébergeur. Non testé.
- **Périmètre.** Nyra ne regarde que les sites de la marque. Un visuel
  utilisé après expiration par un distributeur ou sur les réseaux
  sociaux n'est pas vu. C'est un choix de produit à annoncer clairement.

### 3.4 Tests et qualité

- Aucun test automatique côté frontend. Un changement d'interface peut
  casser un parcours sans alerte.
- Le cloud (auth, RLS, API, worker) n'est testé qu'avec `TEST_DATABASE_URL`.
- Les tests d'interface sont manuels.

### 3.5 Cohérence de la doc

- `COUTS_PRODUCTION.md` dit « une marque correspond à une organisation »,
  alors que le produit gère maintenant plusieurs marques par organisation
  (`ARCHITECTURE.md`). Les conclusions sur le coût tiennent, la phrase est
  périmée.
- Le chiffrage repose sur une mesure faite **sans Chromium** (HTML
  statique). Le nombre d'images est un plancher, le temps de crawl est une
  estimation.

---

## 4. Ce qu'on peut améliorer, dans l'ordre

Classé par valeur pour une présentation à un manager, puis effort.

### Avant la démo (quelques heures à 1 jour)

1. **Mettre de vraies données.** Importer la bibliothèque Rémy Martin
   (`refs.csv` + images), lancer une vraie lecture complète, prendre 20 à
   30 décisions dans « À traiter ». Effet : de vrais chiffres dans
   Statistiques (temps, volumes, faux positifs), un rapport réel à
   montrer, et la réponse à « combien ça coûte vraiment » sur du mesuré.
2. **Préparer un jeu de démo** propre (une marque de démonstration avec
   des cas parlants : image retouchée, image retournée, image expirée
   encore en ligne). Un parcours scénarisé de 10 minutes.
3. **Remplacer l'identifiant du robot** (contact + URL) dans
   `config.yaml`.
4. **Mesurer le rappel réel** sur 30 à 50 paires connues à la main
   (`nyra calibrate`). Un chiffre honnête vaut mieux qu'un chiffre
   flatteur.

### Fonctionnalités qui changent la valeur du produit

| # | Fonctionnalité | Pourquoi | Effort |
|---|---|---|---|
| 1 | **Passage hebdomadaire automatique** (réglage par organisation) | Transforme un outil en service | 1,5–2 j |
| 2 | **Résumé par e-mail** (nouveaux expirés, expirations à 30 j) | Le produit vient à l'utilisateur | 2–3 j |
| 3 | **Alertes d'expiration de la bibliothèque**, indépendantes du crawl | Utile même sans détection | 0,5–1 j |
| 4 | **Preuve datée** : capture de la page et horodatage au moment de la détection | Donne une valeur juridique au rapport, nécessaire pour demander un retrait | 2–4 j |
| 5 | **Suivi de retrait** : assigner, commenter, échéance, relance | Fait passer de « détecter » à « résoudre » | 3–5 j |
| 6 | **Import depuis une DAM** (Bynder, Adobe, etc.) au lieu du CSV | Supprime l'effort d'onboarding, `RefSource` est déjà prévu pour | 3–5 j selon la DAM |
| 7 | **Webhook Slack / Teams** | Alertes dans l'outil quotidien | 1 j |
| 8 | **Meilleur modèle de détection de copies** (SSCD, DINOv2) | Rappel sur recadrages serrés ; demande de recalculer tous les embeddings | 3–5 j + validation |

Pistes plus lointaines : surveillance hors des sites de la marque
(recherche d'image inversée, réseaux sociaux : périmètre et coût par
requête très différents), interface en plusieurs langues, rapport PDF
généré côté serveur.

### Sécurité et robustesse (avant un vrai client)

1. Limitation de débit sur les routes d'écriture et de téléversement.
2. Suite de tests cloud en CI sur une base Postgres réelle.
3. Déplacer `.env` hors d'OneDrive, faire tourner la clé si elle a été
   synchronisée.
4. Politique de conservation et purge des images disparues.
5. Sentry côté backend et navigateur (l'écran d'erreur est prêt à s'y
   brancher), test de disponibilité sur `/api/healthz`.
6. Quelques tests de rendu frontend sur les parcours critiques (connexion,
   lancer une lecture, décider).

### Interface

- Les boutons désactivés sans explication sont à passer en revue : la
  bulle qui suit le curseur (`CursorTip`) est en place sur « Lancer la
  lecture » et « Comparer sans relire », à généraliser.
- Écran d'erreur ajouté (`ErrorScreen`).
- Animations légères et cohérentes (entrées de listes, changement de
  marque, décision prise) sans en abuser.

---

## 5. Coût, en clair

Source : `docs/COUTS_PRODUCTION.md` (prix de septembre 2026, € HT).

**Infrastructure : ≈ 32 à 112 € par mois**, quelle que soit la taille
jusqu'à une vingtaine de marques.

| Poste | Bas | Haut |
|---|---|---|
| Supabase Pro (base, pgvector, auth, stockage) | 22 | 26 |
| Worker (Chromium + CLIP, CPU) | 9 | 28 |
| Web + interface | 0 (même serveur) | 17 (Vercel Pro) |
| E-mails | 0 | 17 |
| Monitoring | 0 | 22 |
| Sauvegarde + domaine | 1 | 2 |

- **Pas de GPU** : CLIP sur CPU traite ≈ 33 images/s, soit environ une
  minute pour un site entier. Le temps de crawl vient de Chromium et des
  délais de politesse (10 à 20 min pour 300 pages).
- **Coût marginal d'une marque** : ≈ 0,5 €/mois. Le vrai coût est humain
  (constituer la bibliothèque, revoir les résultats).
- **Un worker de plus (+9 €)** vers 20–25 marques.

**Développement restant** pour un service complet : ≈ 4 à 7,5 jours
(passage hebdomadaire, e-mails, SMTP, alerting). À ajouter :
preuve datée et suivi de retrait si on les retient (5 à 9 jours de plus).

**À dire honnêtement au manager :**

- Ces chiffres viennent d'une mesure sur du HTML statique, sans
  navigateur. Ils seront confirmés par la première vraie lecture.
- Ils ne comptent pas le temps de l'équipe (onboarding d'une marque,
  revue des résultats), qui domine.
- Un hébergement en France (Scaleway plutôt que Hetzner) coûte quelques
  euros de plus si un client l'exige.

---

## 6. Proposition de plan

1. **Cette semaine, pour la démo :** vraie bibliothèque + vraie lecture +
   décisions, mesure du rappel sur paires connues, identifiant du robot.
2. **Ensuite :** passage hebdomadaire, e-mail de résumé, alertes
   d'expiration (≈ 4 à 6 jours).
3. **Puis :** preuve datée, suivi de retrait, limitation de débit, tests
   cloud en CI, Sentry.
4. **Plus tard :** import DAM, PostHog, Grafana, modèle de détection.
