# Feuille de route produit

Les idées pour le produit, à cocher quand c'est fait. Chaque idée dit **ce qu'on
veut**, **pourquoi**, et une **piste** de réalisation (à valider avant de coder).
Une idée terminée est cochée avec la date et le commit ; on ne la supprime pas.

Légende : `[ ]` à faire · `[~]` en cours · `[x]` fait.

La page « Droits non vérifiés » s'appelle désormais **« Images en ligne »** : une page par marque avec des onglets (À qualifier,
Retrouvées, Logos, Ignorées, Toutes). Les idées ci-dessous gardent l'ancien nom ; « Droits non vérifiés » y désigne l'onglet « À qualifier ».

## Cap : un service qu'on peut présenter

Ce qui manque pour présenter Nyra comme un service n'est pas la détection
(solide, voir `MATCHING.md`) mais **la boucle qui rend le produit utile sans
qu'on y pense** : aujourd'hui il faut se connecter et cliquer (audit du
29 septembre, `archive/AUDIT_PRODUIT.md`, archivé). Ordre proposé, du plus décisif au moins :

1. **La boucle automatique** (idée 3) : le produit vient à l'utilisateur.
2. **La valeur juridique** (idée 4) : de quoi demander un retrait.
3. **Résoudre, pas seulement détecter** (idée 5).
4. **Des chiffres réels** (idée 6) : sans eux, tout le reste reste théorique.
5. Les idées 1, 2 et 7 : utiles, mais elles améliorent l'exploration et le
   tri, pas la promesse centrale. L'idée 7 (étiquettes de type sur les images du
   site) rend la liste « Droits non vérifiés » lisible : elle prolonge l'idée 2
   et passe en tête de ce groupe.
6. L'idée 8 (droits illimités) : petite et indépendante, à glisser dès qu'on
   retouche l'import ou la fiche d'une référence.

Chiffrages : ceux de l'audit, à confirmer avant de s'y engager.

## En premier

### [~] 1. Regrouper les recadrages d'une même image (Droits non vérifiés, « Tous »)

**17 octobre 2026.** Le regroupement sert aussi la page « À traiter » : une ligne par photo (« N fichiers »), sous le visuel de la bibliothèque, avec sa progression (« 1 retirée sur 3 »). **Reste** : « Vérifier les retraits » (une lecture complète qui confirme qu'un visuel marqué « Retiré » n'est plus en ligne ; aujourd'hui rien n'enregistre qu'une image a disparu d'un site).

**Ce qu'on veut.** Dans la liste « Droits non vérifiés », une même photo
présente sur le site sous plusieurs recadrages (ex. `magnum_6glasses_set.webp`
en 435 × 628 et en 366 × 372, ou la photo du service du Louis XIII) ne doit
apparaître qu'**une seule fois**. La carte représente le groupe ; en cliquant
dessus, on voit **chaque occurrence** : sur quelle page elle se trouve, et sur
quelle version du site (marché/langue).

**Pourquoi.** Aujourd'hui chaque variante est une carte : la liste est
gonflée et on revérifie la même image plusieurs fois.

**État (7 octobre 2026).** Code commité (`71d45bd`, 4 octobre) et testé : les
tests passent sur un Postgres jetable où la migration `20261004000017` est
appliquée, et elle l'est aussi sur la base réelle (vérifié le 13 octobre).
« Ignorer » agit sur toutes les variantes d'un groupe. À faire avant de cocher :
vérifier sur les vraies images de Louis XIII. Risque connu : deux photos différentes qui
partagent un même bandeau ou gabarit peuvent être fusionnées (le contrôle de
points clés les juge identiques) ; à calibrer sur des images réelles, et un
groupe ne peut pas encore être scindé à la main.

**Piste.**
- Le regroupement ne compare pas une référence à une image du site mais des
  **images du site entre elles**. Les briques existent : empreintes
  perceptuelles et embeddings CLIP déjà calculés pour chaque image du site, et
  le contrôle de points clés `verify.py` (SIFT + RANSAC), qui reconnaît déjà un
  recadrage. Voir `docs/MATCHING.md`, niveaux 1 à 3.
- Candidats par CLIP (cosinus élevé), puis confirmation par points clés pour
  ne pas fusionner deux prises de vue différentes du même produit. Fusion
  transitive (composantes connexes) avec un plafond de taille de groupe.
- Choisir une image « représentante » du groupe (la plus grande).
- Stocker l'appartenance à un groupe plutôt que de la recalculer à chaque
  affichage ; recalcul incrémental à chaque crawl (comme le matching).
- Interface : badge « × N » sur la carte, volet de détail listant page +
  version du site de chaque occurrence. Le crawl garde déjà la page d'origine
  de chaque image (à confirmer dans le schéma, `docs/DATABASE_SCHEMA.md`).
- Décision : « Ignorer » sur un groupe met de côté toutes les variantes
  (fait). Reste à confirmer pour « + Ajouter » et l'annulation.
- Vérification : un jeu d'images recadrées à la main (comme la calibration de
  `MATCHING.md`) pour mesurer faux regroupements et regroupements manqués.

### [~] 2. Étiquettes automatiques de contenu (verres en cristal, carafe, pique…)

**Désactivée pour l'instant** (9 octobre 2026) : le code est là mais l'API ne s'enregistre qu'avec `NYRA_LABELS=1` et l'interface qu'avec `VITE_LABELS=1`.

**État (13 octobre 2026).** Code commité (`18101ab`) et testé (`test_labels.py`, test d'API des étiquettes) ; la
migration `20261009000020` est appliquée sur la base réelle. Le nom d'une étiquette de contenu se tape dans la barre de
sélection de la bibliothèque ou de « Droits non vérifiés » : « Étiqueter » pose les exemples, « Pas ça » refuse une
proposition ; trois exemples suffisent pour que les autres images soient proposées (pointillés et « ? » dans la
carte). Filtre par contenu sur les deux pages. **Reste** : le seuil de 0,82 est une estimation à calibrer sur de vraies
images (un seuil par étiquette) ; pas de zero-shot par texte ; pas d'écran pour supprimer une étiquette (l'API
`DELETE /labels/{id}` existe). Les étiquettes sont propres à chaque marque.

**Ce qu'on veut.** Par marque (ex. Louis XIII), définir des étiquettes
d'objets et que l'algorithme dise **lesquels sont visibles** sur chaque image :
verres en cristal, carafe, pique, etc. Permet de filtrer et de rechercher la
bibliothèque et les images du site par contenu.

**Pourquoi.** Retrouver « toutes les images avec la carafe » sans ouvrir les
fichiers un à un, et préparer des tris par produit.

**Piste (à définir, par ordre de coût croissant).**
1. **CLIP en zero-shot.** On réutilise les embeddings déjà calculés : pour
   chaque étiquette, un ou plusieurs textes (« a crystal glass », « a cognac
   decanter »), on compare au texte. Aucun nouveau modèle, aucun coût de
   calcul de plus. Limite : mal à l'aise avec les objets propres à la marque
   et avec plusieurs objets sur la même image.
2. **Exemples par étiquette (few-shot).** La marque choisit 5 à 20 images
   d'exemple par étiquette depuis sa bibliothèque ; on moyenne leurs embeddings
   (un « prototype ») et on classe par similarité. Plus fiable pour « la
   carafe Louis XIII » que pour « une carafe ». Les corrections de
   l'utilisateur (étiquette fausse/manquante) enrichissent les exemples.
3. **Détecteur d'objets à vocabulaire ouvert** (OWL-ViT, Grounding DINO) : donne
   aussi l'emplacement. Plus lourd (GPU, nouveau modèle) ; à garder si on a
   besoin de savoir *où* est l'objet.

Recommandation de départ : 1 + 2, calibrés comme les seuils de matching
(précision/rappel sur des images étiquetées à la main). Étiquettes multiples
par image, seuil par étiquette, résultat stocké avec le score pour pouvoir
re-seuiller sans recalculer.

Questions ouvertes : qui définit les étiquettes (admin de la marque, nous) ?
Les étiquettes sont-elles propres à une marque ou partagées ? Que veut dire
« pique » pour Louis XIII (l'objet tenu par le sommelier sur la photo) ?

### [ ] 3. La boucle automatique (passage hebdomadaire, résumé, alertes)

**Ce qu'on veut.** Un passage de lecture chaque semaine par marque, un e-mail de
résumé (« 3 nouveaux visuels expirés détectés ») et une alerte d'expiration de
la bibliothèque (« 5 visuels expirent dans 30 jours »), indépendante du crawl.

**Pourquoi.** Un outil de surveillance qu'il faut penser à lancer perd
l'essentiel de sa valeur. Aujourd'hui il n'y a ni passage planifié ni alerte.

**Piste.** Réglage par organisation et déclencheur pg_cron qui insère un job
`crawl` avec `fresh: true` (sinon le mode reprise saute les pages déjà vues) ;
e-mails par Resend, destinataires par organisation. Environ 4 à 6 jours.

### [ ] 4. Preuve datée

**Ce qu'on veut.** Au moment de la détection, une capture de la page et un
horodatage, joints au rapport.

**Pourquoi.** Donne une valeur juridique au rapport, nécessaire pour demander
un retrait. Environ 2 à 4 jours.

### [ ] 5. Suivi de retrait

**Ce qu'on veut.** Assigner, commenter, fixer une échéance, relancer.

**Pourquoi.** Fait passer de « détecter » à « résoudre ». Environ 3 à 5 jours.

### [ ] 6. Chiffres réels

**Ce qu'on veut.** Importer une vraie bibliothèque, lancer une vraie lecture,
prendre 20 à 30 décisions, et mesurer le rappel sur 30 à 50 paires connues à la
main (`nyra calibrate`).

**Pourquoi.** Les 97,5 % de `MATCHING.md` viennent de copies retouchées
fabriquées, pas de vrais cas de réutilisation ; le rappel réel est inconnu.

### [~] 7. Étiqueter les images du site (logo, packshot…) pour trier « Droits non vérifiés »

**Désactivée pour l'instant** (9 octobre 2026) : le code est là mais l'API ne s'enregistre qu'avec `NYRA_LABELS=1` et l'interface qu'avec `VITE_LABELS=1`.

**État (13 octobre 2026).** Code commité (`18101ab`) et testé ; migration `20261009000020` appliquée sur la base réelle. Quatre types fixes
par marque (logo, packshot, pictogramme, autre). Dans « Droits non vérifiés » : bouton « Étiqueter à la main » (une
image à la fois, touches 1 à 4, images choisies pour être variées, tableau précision/rappel à la fin), boutons de type
dans la barre de sélection, filtre par type et « Sans type ». L'algorithme propose un type aux autres images (badge en
pointillés). Rien n'est masqué d'office. **Reste** : mesurer précision et rappel sur de vraies images étiquetées par
Louis XIII (20 à 30 par type) avant de s'y fier.

**Logos sans configuration (17 octobre 2026).** Un détecteur de logos générique, sans étiquetage par marque, est dans
`nyra/logos.py` (CLIP, descriptions précalculées ; voir `MATCHING.md`, « Spotting logos ») : sur les 1 716 images de
Louis XIII, 10 images au-dessus de 0,95, toutes des logos. **Pas encore branché** : il reste à décider la mise de côté
(réglage par marque, liste réversible) et à vérifier sur une autre marque.

**Ce qu'on veut.** Le même principe que l'idée 2, appliqué aux **images du
site** : chaque image porte des étiquettes de type (« logo », « packshot
produit »…), posées par l'algorithme, et la liste « Droits non vérifiés » se
filtre dessus. On trie ainsi les images sans enjeu de droits sans les écarter
une par une. Pour que l'algorithme les reconnaisse, il faut **définir l'aspect
de chaque type** : qu'est-ce qu'un logo, qu'est-ce qu'un packshot ?

**Pourquoi.** La liste mélange de vraies photos à vérifier et des images
récurrentes sans enjeu, aujourd'hui écartées une à une (exclusions, voir
`MATCHING.md`). Le bruit cache l'essentiel.

**Piste.**
- Les étiquettes existent déjà sur les références de la bibliothèque (libres,
  posées à la main, jamais lues par le matching). Les images du site n'en ont
  pas : c'est le manque à combler, avec la même notion.
- Même mécanisme que l'idée 2 : prototypes CLIP à partir d'exemples choisis
  par type, score stocké pour re-seuiller sans recalculer. Les signaux simples
  (petite taille, fond uni ou transparent) peuvent aider à les définir.
- Les types sont des étiquettes comme les autres : on ne masque rien d'office,
  on filtre. Une vraie photo mal étiquetée « parasite » reste un droit non
  vérifié : l'écarter est une action de l'utilisateur, annulable.
- Vérification : des images réelles étiquetées à la main par type, précision
  et rappel mesurés comme pour le matching.

**Questions ouvertes.** Quels types exactement ? Un packshot est-il toujours
sans enjeu, ou parfois soumis à des droits (le contrat décide, voir « produit
découpé » dans `MATCHING.md`) ?

### [~] 8. Droits illimités (« libre d'usage »)

**État (13 octobre 2026).** Code commité (`18101ab`) et testé ; la migration `20261009000019` est appliquée sur la
base réelle. Une case « Droits illimités » (fiche, ajout depuis le site, sélection de la bibliothèque) ; dans le
CSV, le mot `illimité` à la place de la date. Une date et des droits illimités s'excluent (un contrôle en base). Hors
des urgences et du tableau de bord ; compté à part dans les statistiques. Retrouvée avec certitude (niveau
« Confirmé »), la référence va dans l'onglet « Libre d'usage » de la page des correspondances ; un « Probable » ou
« À vérifier » reste à vérifier. Pas de règle automatique par étiquette : un packshot n'est jamais sûr à 100 %, la case
est seulement précochée pour un packshot qu'on ajoute à la bibliothèque. **Reste** : les alertes de l'idée 3 devront
ignorer les droits illimités.

**Ce qu'on veut.** Pouvoir marquer une référence comme **libre d'usage / droits
illimités** : elle n'expire jamais, donc elle ne devient jamais « expirée » ni
« à échéance », mais elle reste reconnue sur les sites (utile pour retrouver où
elle est utilisée).

**Pourquoi.** Aujourd'hui une référence sans date veut dire « date inconnue »
(`without_expiry` dans les statistiques, et `parse_expiry` accepte le vide).
Les deux cas se mélangent : une image aux droits illimités ressemble à une
image dont on a oublié de saisir la date, et fausse le suivi.

**Piste (à valider).**
- Un état distinct de « date inconnue » : par exemple une case « droits
  illimités » à côté de la date d'expiration (ajout de colonne, additif), avec
  la valeur correspondante dans l'import CSV.
- Les droits illimités sortent des urgences d'expiration, du tableau de bord et
  des alertes de l'idée 3 ; ils restent comptés à part dans les statistiques.
- **Liée aux étiquettes.** Les droits illimités doivent pouvoir se rattacher à
  des étiquettes : les packshots, par exemple, sont très souvent sans
  expiration. Piste : une règle par marque « les références portant
  l'étiquette *packshot* sont à droits illimités » (ou l'inverse : proposer
  l'étiquette quand on coche « droits illimités »), pour ne pas cocher image
  par image. Les étiquettes de la bibliothèque existent déjà (migration
  `…016`) ; celles des images du site viennent avec l'idée 7.
- À trancher : une règle d'étiquette écrase-t-elle une date saisie à la main
  (proposition : non, la date explicite l'emporte) ? Une image libre d'usage
  trouvée sur un site apparaît-elle dans « À traiter », ou est-elle rangée
  d'office comme conforme ?

## Avant d'ouvrir à un client

Limitation de débit sur l'API, tests cloud en CI sur une vraie base, vraie
adresse de contact du robot dans `config.yaml`, politique de conservation des
images (`archive/AUDIT_PRODUIT.md`, archivé, §3.2).

## Propositions à discuter

- [ ] **Aperçu des occurrences dans la liste** : survol d'une carte groupée =
  miniatures des autres recadrages (dépend de l'idée 1).
- [ ] **Trier par catégorie de page** : filtrer « À traiter » et « Images en
  ligne » selon la section du site où l'image se trouve (ex. Louis XIII :
  `/news` ou `/products`). Piste : le premier segment du chemin de la page
  d'origine, que le crawl garde déjà ; même mécanique que le filtre par pays
  (l'étiquette d'un site).
- [ ] **Étiquettes dans les rapports** : regrouper ou filtrer le rapport par
  étiquette (dépend de l'idée 2).
- [x] **Collections dans la bibliothèque** (migration `20261013000021`) : dossiers sur 3 niveaux (une saison, un shooting),
  un visuel dans une seule collection ; la recherche multi-mots (nom, tags, chemin de collection, crédit) couvre toute
  la bibliothèque. Depuis le 16 octobre : le volet de la bibliothèque ne sert plus qu'à naviguer
  (accordéon sur 3 niveaux, fil d'Ariane, tuiles de sous-dossiers) ; l'arbre se gère dans Réglages > Collections
  (ajouter, renommer, **déplacer**, supprimer), et « + Sous-collection » se trouve dans une collection de 2e niveau.
  **Reste** : glisser-déposer vers l'arbre, un visuel dans plusieurs collections, un champ « produit » (aujourd'hui
  les tags, plus tard les étiquettes de contenu proposées depuis des exemples).
- [ ] **Recherche par image dans la bibliothèque** : déposer une photo, voir
  les références proches (embeddings déjà disponibles).
- [ ] **Détection de doublons dans la bibliothèque** : même regroupement que
  l'idée 1, appliqué aux références.
- [ ] **Mesures de qualité affichées** : précision/rappel du regroupement et
  des étiquettes, visibles dans la page Statistiques.

## Faits

Entre le 16 et le 17 octobre 2026 (détails dans les documents indiqués) :

- **Bibliothèque** : volet de navigation en accordéon sur 3 niveaux, sans bouton de gestion ; collections gérées dans
  Réglages > Collections (ajout, renommage, déplacement, suppression) ; accueil de la bibliothèque sur les 4 derniers
  visuels ajoutés et « Afficher tous les visuels » (aucune vignette de plus à l'arrivée) ; plus de tuile « Déposez des
  images ».
- **Indexation à la demande** : Réglages > Général > Maintenance (administrateurs), `POST …/jobs/index`.
- **Logos** (`MATCHING.md`, « Spotting logos ») : détecteur sans configuration par marque (CLIP, seuil 0,95), réglage par
  marque « Ne pas comparer les logos » (désactivé par défaut, Réglages > Général > Logos), liste réversible.
- **« Images en ligne »** (ancienne page « Droits non vérifiés », adresse `/online-images`) : toutes les images lues sur les
  sites, en cinq onglets (À qualifier, Retrouvées, Logos, Ignorées, Toutes), chargés par pages de 60 (`ARCHITECTURE.md`).
- **Qualité** : les deux tests du back qui étaient rouges en CI depuis le 14 octobre sont corrigés ; plus aucune mention de
  facturation dans les textes vus par les clients.

## À reprendre

Ouvert au 17 octobre 2026 :

- **PR #8** (calcul des scores de logos à l'ouverture de « Images en ligne », sans quoi l'onglet Logos reste à 0 ; plus cette
  documentation) : à relire et fusionner. La PR #7 (« Images en ligne ») est déjà fusionnée. La migration
  `20261017000024_set_aside_logos.sql` doit être appliquée **avant** de démarrer le nouveau code, partout (elle l'est sur la
  base de développement).
- **Logos** : le seuil de 0,95 est mesuré sur **une seule marque** (Louis XIII : 10 fichiers, 18 adresses, tous de vrais logos) : à vérifier
  sur une autre marque ; le rappel n'est pas compté ; une image relue sous une nouvelle adresse après une remise repart
  comme logo (la liste le dit).
- **Images en ligne** : l'onglet « Ignorées » est en lecture seule (on réintègre depuis Réglages > Général) ; la recherche par
  page ne voit que les 20 premières pages de chaque image ; la liste relit toutes les images à chaque requête (mesuré :
  0,3 s à 10 000 images, 1 s à 30 000 : au-delà, compter en SQL ou mettre en cache).
- **Tests** : `test_big_responses_are_gzipped…` échoue sous Windows (fins de ligne) mais passe en CI ; le test de création
  d'organisation (« Hennessy ») n'est pas rejouable sur une base réutilisée.
- **Budget** : le bundle d'entrée est à 26,3 / 26,5 Ko gzip (voir `DEVELOPMENT.md`).
